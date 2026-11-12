#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "fastapi>=0.115,<1", "uvicorn>=0.34,<1", "jinja2>=3.1,<4", "reportlab>=4.4,<5",
#   "cryptography>=50.0.2,<51", "pyotp>=2.9,<3", "qrcode[pil]>=8,<9", "webauthn>=3.0.1,<4",
# ]
# ///
# ─── How to run ───
# uv run tools/rootsmagic-web/serve.py
# ──────────────────
"""Local browser and JSON API for a RootsMagic tree."""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path
from secrets import compare_digest, token_urlsafe
from threading import Lock
from typing import TYPE_CHECKING, Annotated, Final, Literal
from urllib.parse import urlencode

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from pedigree_evidence import (
    INLINE_IMAGE_TYPES,
    PersonEvidence,
    media_file,
    person_evidence,
    record_images,
)
from pedigree_print import PrintFontError, PrintLayoutError, render_pedigree_pdf
from pedigree_research import (
    RelationshipPaths,
    ResearchGaps,
    relationship_paths,
    research_gaps,
)
from pedigree_store import (
    Pedigree,
    PedigreeNode,
    pedigree,
    pedigree_details,
    read_pedigree_details,
)
from reader_access import ReaderAccess, current_session, read_json
from reader_account_routes import account_router
from reader_comments import (
    Comment,
    CommentID,
    CommentPage,
    Comments,
    CommentStorageError,
    comment_author,
    comments_path,
    comments_token,
    read_comment,
    require_comments,
)
from reader_print_limits import PDF_BODY_LIMIT, print_slot, validate_print
from reader_settings import SettingsError, site_settings
from store import (
    Directory,
    PedigreeDirectory,
    PersonID,
    Profile,
    Record,
    connect,
    pedigree_search,
    profile,
    search,
)
from tree_catalog import LIVE, Tree, same_file

if TYPE_CHECKING:
    from starlette.middleware.base import RequestResponseEndpoint

HERE: Final = Path(__file__).resolve().parent
PAGE_SIZE: Final = 20
PEDIGREE_GENERATIONS: Final = 11
PUBLISH_SCRIPT: Final = HERE / "deploy" / "publish-tree.py"
PUBLISH_TIMEOUT: Final = 600
PUBLISH_TOKEN: Final = token_urlsafe(32)
PUBLISH_LOCK: Final = Lock()
QueryText = Annotated[str, Query(max_length=160)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]
Limit = Annotated[int, Query(ge=1, le=100)]
RecordID = Annotated[int, PathParam(ge=1, le=2**63 - 1)]
Generations = Annotated[int, Query(ge=1, le=PEDIGREE_GENERATIONS)]
PositiveID = Annotated[int, Field(ge=1, le=2**63 - 1)]


def selected_tree(
    request: Request, tree: Annotated[str, Query(max_length=240)] = ""
) -> Tree:
    """Resolve a tree slug only against the current account's catalog."""
    catalog = reader_access.visible_trees(request)
    if not catalog:
        raise HTTPException(
            status_code=403, detail="Ask an administrator for access to a tree."
        )
    if not tree:
        return catalog[0]
    match = next((item for item in catalog if item.slug == tree), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Selected tree is unavailable")
    return match


SelectedTree = Annotated[Tree, Depends(selected_tree)]


class TreeChoice(Record):
    slug: str
    name: str
    default: bool


class GapRequest(BaseModel):
    person_ids: list[PositiveID] = Field(min_length=1, max_length=100)


class PrintRequest(BaseModel):
    chart: Pedigree
    show_children: bool = False
    show_partners: bool = False
    partner_roots: tuple[PedigreeNode, ...] = ()


class PublishResult(Record):
    """Media counts and snapshot outcome returned by the publisher."""

    status: Literal["published", "already_published"]
    copied: Annotated[int, Field(ge=0)]
    replaced: Annotated[int, Field(ge=0)]
    reused: Annotated[int, Field(ge=0)]


def local_publish_source(request: Request, tree: Tree) -> Path | None:
    """Limit publishing to the configured working tree in the Mac reader."""
    publish = site_settings().sources.publish
    if (
        sys.platform != "darwin"
        or not (
            os.environ.get("ROOTSMAGIC_ENABLE_PUBLISH") == "1"
            if publish is None
            else publish
        )
        or request.client is None
        or request.client.host not in {"127.0.0.1", "::1"}
        or request.url.hostname not in {"127.0.0.1", "localhost"}
        or request.url.scheme != "http"
    ):
        return None
    configured = os.environ.get("ROOTSMAGIC_DB")
    source = Path(configured).expanduser().resolve() if configured else LIVE.resolve()
    return source if source.is_file() and same_file(tree.path, source) else None


def run_publisher(source: Path) -> PublishResult:
    """Invoke the local publisher and keep its SSH children in the same group."""
    try:
        with subprocess.Popen(
            [
                sys.executable,
                str(PUBLISH_SCRIPT),
                "--with-media",
                "--json",
                str(source),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        ) as process:
            try:
                stdout, stderr = process.communicate(timeout=PUBLISH_TIMEOUT)
            except subprocess.TimeoutExpired as error:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
                message = "Publish timed out. Check the VM reader before trying again."
                raise HTTPException(status_code=504, detail=message) from error
    except OSError as error:
        message = "Could not start the RootsMagic publisher on this Mac."
        raise HTTPException(status_code=503, detail=message) from error

    if process.returncode != 0:
        message = stderr.strip()[-1400:] or "Publish failed. Try again."
        raise HTTPException(status_code=502, detail=message)
    try:
        return PublishResult.model_validate_json(stdout, strict=True)
    except ValidationError as error:
        message = "The publisher returned an unreadable result. Check the VM reader."
        raise HTTPException(status_code=502, detail=message) from error


def allowed_hosts() -> list[str]:
    """Allow loopback and explicitly configured reverse proxy hostnames."""
    hosts = ["127.0.0.1", "localhost"]
    for value in os.environ.get("ROOTSMAGIC_ALLOWED_HOSTS", "").split(","):
        host = value.strip().lower()
        if host and "*" not in host:
            hosts.append(host)
    return hosts


app = FastAPI(title="RootsMagic Reader", docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts())
templates = Environment(loader=FileSystemLoader(HERE), autoescape=select_autoescape())
reader_access = ReaderAccess.from_environment()
comment_store = Comments(
    comments_path(reader_access.accounts.path if reader_access.accounts else None)
)
app.include_router(account_router(reader_access, templates))


@app.middleware("http")
async def local_headers(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """Keep responses private and prevent embedding or loading third-party assets."""
    response = await reader_access.guard(request)
    if response is None:
        response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    policy = (
        "default-src 'none'; style-src 'self'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    )
    if request.url.path == "/pedigree":
        policy += "; script-src 'self'; connect-src 'self'; img-src 'self'"
    elif request.url.path in {
        "/login",
        "/account/passkeys",
        "/account/passkeys/rename",
        "/account/passkeys/remove",
    }:
        policy += "; script-src 'self'; connect-src 'self'"
    elif request.url.path.startswith("/pedigree/people/") and request.url.path.endswith(
        "/record"
    ):
        policy += "; script-src 'self'; img-src 'self'"
    elif request.url.path.startswith("/account/security"):
        policy += "; img-src 'self'"
    response.headers["Content-Security-Policy"] = policy
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(sqlite3.Error)
async def unavailable(request: Request, _error: sqlite3.Error) -> Response:
    """Return a useful error without exposing the local database path."""
    message = "Check that the RootsMagic file is available and uses a supported schema."
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": message}, status_code=503)
    catalog = reader_access.visible_trees(request)
    return HTMLResponse(
        templates.get_template("page.html").render(
            title="Database unavailable",
            message=message,
            tree=catalog[0] if catalog else None,
            trees=catalog,
            reader_session=current_session(request),
            local_system=reader_access.local_browser(request),
        ),
        status_code=503,
    )


@app.exception_handler(CommentStorageError)
async def comments_unavailable(
    _request: Request, error: CommentStorageError
) -> JSONResponse:
    """Report comment storage errors without exposing local paths."""
    return JSONResponse({"detail": str(error)}, status_code=503)


@app.exception_handler(SettingsError)
async def settings_unavailable(request: Request, error: SettingsError) -> Response:
    """Report configuration errors without reading the unavailable settings again."""
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": str(error)}, status_code=503)
    return PlainTextResponse(str(error), status_code=503)


@app.exception_handler(HTTPException)
async def rejected_request(request: Request, error: HTTPException) -> Response:
    """Keep a usable tree chooser when an old browser link is unavailable."""
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {"detail": error.detail},
            status_code=error.status_code,
            headers=error.headers,
        )
    catalog = reader_access.visible_trees(request)
    return HTMLResponse(
        templates.get_template("page.html").render(
            title="Page unavailable",
            message=error.detail,
            tree=catalog[0] if catalog else None,
            trees=catalog,
            reader_session=current_session(request),
            local_system=reader_access.local_browser(request),
        ),
        status_code=error.status_code,
        headers=error.headers,
    )


@app.get("/api/v1/trees", tags=["Trees"])
def api_trees(request: Request) -> tuple[TreeChoice, ...]:
    """List configured trees without exposing filesystem paths."""
    return tuple(
        TreeChoice(slug=tree.slug, name=tree.name, default=index == 0)
        for index, tree in enumerate(reader_access.visible_trees(request))
    )


@app.get("/api/v1/people", tags=["People"])
def api_people(
    tree: SelectedTree, q: QueryText = "", limit: Limit = PAGE_SIZE, offset: Offset = 0
) -> Directory:
    """Search by RootsMagic ID or name, including alternate names."""
    return search(tree.path, q, limit, offset)


@app.get("/api/v1/people/{person_id}", tags=["People"])
def api_person(person_id: RecordID, tree: SelectedTree) -> Profile:
    """Get a person, their immediate family, events and event citations."""
    record = profile(tree.path, PersonID(person_id))
    if record is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return record


@app.get("/api/v1/pedigree/people", tags=["Pedigree"])
def api_pedigree_people(
    tree: SelectedTree,
    q: QueryText = "",
    limit: Limit = PAGE_SIZE,
    offset: Offset = 0,
) -> PedigreeDirectory:
    """Search the same database that supplies the interactive chart."""
    query = q.strip()
    if query.startswith("#") and query[1:].isdigit():
        query = query[1:]
    return pedigree_search(tree.path, query, limit, offset)


@app.get("/api/v1/pedigree/{person_id}", tags=["Pedigree"])
def api_pedigree(
    person_id: RecordID,
    tree: SelectedTree,
    generations: Generations = PEDIGREE_GENERATIONS,
) -> Pedigree:
    """Get a person, their siblings and the requested generations of ancestry."""
    chart = pedigree(tree.path, PersonID(person_id), generations)
    if chart is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return chart


@app.get("/pedigree", include_in_schema=False)
def pedigree_page(request: Request, tree: SelectedTree) -> HTMLResponse:
    """Serve the interactive chart."""
    return HTMLResponse(
        templates.get_template("pedigree.html").render(
            tree=tree,
            trees=reader_access.visible_trees(request),
            reader_session=current_session(request),
            local_system=reader_access.local_browser(request),
            starting_person=tree.starting_person() or 0,
            chart_settings=site_settings().chart,
            comments_token=comments_token(request),
            publish_token=PUBLISH_TOKEN
            if local_publish_source(request, tree)
            else None,
        )
    )


@app.post("/api/v1/pedigree/publish", include_in_schema=False)
def api_publish(request: Request, tree: SelectedTree) -> PublishResult:
    """Publish the working tree only after a same-origin local browser action."""
    source = local_publish_source(request, tree)
    if source is None:
        raise HTTPException(status_code=404, detail="Publishing is unavailable here.")
    origin = f"{request.url.scheme}://{request.url.netloc}"
    token = request.headers.get("X-RootsMagic-Publish-Token", "")
    if request.headers.getlist("Origin") != [origin] or not compare_digest(
        token.encode(), PUBLISH_TOKEN.encode()
    ):
        raise HTTPException(
            status_code=403, detail="Reload the local reader and retry."
        )
    if not PUBLISH_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A publish is already running.")
    try:
        return run_publisher(source)
    finally:
        PUBLISH_LOCK.release()


@app.get("/api/v1/pedigree/{person_id}/details", tags=["Pedigree"])
def api_pedigree_details(person_id: RecordID, tree: SelectedTree) -> Profile:
    """Get the selected chart person's record from the same live database."""
    record = pedigree_details(tree.path, PersonID(person_id))
    if record is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return record


@app.get("/api/v1/pedigree/{person_id}/comments", tags=["Pedigree"])
def api_comments(
    request: Request,
    person_id: RecordID,
    tree: SelectedTree,
    limit: Limit = 50,
    before: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
) -> CommentPage:
    """List comments on a person in the selected tree."""
    require_comments(request)
    cursor = CommentID(before) if before is not None else None
    return comment_store.page(tree, PersonID(person_id), limit, cursor)


@app.post("/api/v1/pedigree/{person_id}/comments", tags=["Pedigree"], status_code=201)
async def api_add_comment(
    request: Request, person_id: RecordID, tree: SelectedTree
) -> Comment:
    """Append a plain-text comment as the current reader."""
    author = comment_author(request)
    draft = await read_comment(request)
    return await run_in_threadpool(
        comment_store.add, tree, PersonID(person_id), author, draft
    )


@app.get("/api/v1/pedigree/{person_id}/relationship", tags=["Pedigree"])
def api_relationship(
    person_id: RecordID,
    from_id: Annotated[int, Query(ge=1, le=2**63 - 1)],
    tree: SelectedTree,
) -> RelationshipPaths:
    result = relationship_paths(tree.path, PersonID(from_id), PersonID(person_id))
    if result is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return result


@app.post("/api/v1/pedigree/research-gaps", tags=["Pedigree"])
def api_research_gaps(request: GapRequest, tree: SelectedTree) -> ResearchGaps:
    return research_gaps(tree.path, tuple(map(PersonID, request.person_ids)))


@app.get("/api/v1/pedigree/{person_id}/evidence", tags=["Pedigree"])
def api_evidence(person_id: RecordID, tree: SelectedTree) -> PersonEvidence:
    result = person_evidence(tree.path, PersonID(person_id))
    if result is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return result


@app.get("/api/v1/pedigree/media/{media_id}", tags=["Pedigree"])
def api_media(media_id: RecordID, tree: SelectedTree) -> FileResponse:
    file = media_file(tree.path, media_id)
    if file is None:
        raise HTTPException(status_code=404, detail="Linked media is unavailable")
    suffix = file.suffix.lower()
    media_type = (
        "application/pdf" if suffix == ".pdf" else INLINE_IMAGE_TYPES.get(suffix)
    )
    return FileResponse(
        file,
        filename=file.name,
        media_type=media_type or "application/octet-stream",
        content_disposition_type="inline" if media_type else "attachment",
        headers={"Cross-Origin-Resource-Policy": "same-origin"},
    )


@app.post("/api/v1/pedigree/print", tags=["Pedigree"])
async def api_print(request: Request, tree: SelectedTree) -> Response:
    session = current_session(request)
    with print_slot(session.account.id if session else None):
        try:
            payload = PrintRequest.model_validate_json(
                await read_json(request, PDF_BODY_LIMIT)
            )
        except ValidationError as error:
            raise HTTPException(
                status_code=422, detail="The pedigree for this PDF is invalid."
            ) from error
        data = await run_in_threadpool(print_pedigree, payload)
    filename = f"{tree.slug}-pedigree-{payload.chart.root.person.id}-a0-roll.pdf"
    return Response(
        data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def print_pedigree(request: PrintRequest) -> bytes:
    """Validate the chart and render it within the reserved PDF slot."""
    validate_print(request.chart, request.partner_roots)
    try:
        return render_pedigree_pdf(
            request.chart,
            show_children=request.show_children,
            show_partners=request.show_partners,
            partner_roots=request.partner_roots,
        )
    except PrintLayoutError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except PrintFontError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get(
    "/pedigree/people/{person_id}/record",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def pedigree_record_page(
    request: Request, person_id: RecordID, tree: SelectedTree
) -> HTMLResponse:
    """Render a record and its linked images from the same read-only snapshot."""
    with connect(tree.path) as db:
        record = read_pedigree_details(db, PersonID(person_id))
        images = record_images(db, tree.path, record) if record is not None else None
    return HTMLResponse(
        templates.get_template("page.html").render(
            title=record.name if record else "Person not found",
            record=record,
            message="There is no person with that RootsMagic ID.",
            pedigree_record=True,
            record_images=images,
            tree=tree,
            trees=reader_access.visible_trees(request),
            reader_session=current_session(request),
            local_system=reader_access.local_browser(request),
        ),
        status_code=200 if record else 404,
    )


@app.get("/pedigree.css", include_in_schema=False)
def pedigree_styles() -> FileResponse:
    """Serve the chart's stylesheet."""
    return FileResponse(HERE / "pedigree.css", media_type="text/css")


@app.get("/image-preview.css", include_in_schema=False)
def image_preview_styles() -> FileResponse:
    """Serve the shared image preview stylesheet."""
    return FileResponse(HERE / "image-preview.css", media_type="text/css")


@app.get("/family.css", include_in_schema=False)
def family_styles() -> FileResponse:
    """Serve the shared family-section stylesheet."""
    return FileResponse(HERE / "family.css", media_type="text/css")


@app.get("/image-preview.js", include_in_schema=False)
def image_preview_script() -> FileResponse:
    """Serve the shared image preview interactions."""
    return FileResponse(HERE / "image-preview.js", media_type="text/javascript")


@app.get("/pedigree.js", include_in_schema=False)
def pedigree_script() -> FileResponse:
    """Serve the chart's browser interactions."""
    return FileResponse(HERE / "pedigree.js", media_type="text/javascript")


@app.get("/pedigree-tools.js", include_in_schema=False)
def pedigree_tools_script() -> FileResponse:
    return FileResponse(HERE / "pedigree-tools.js", media_type="text/javascript")


@app.get("/pedigree-worklist.js", include_in_schema=False)
def pedigree_worklist_script() -> FileResponse:
    """Serve the research worklist interactions."""
    return FileResponse(HERE / "pedigree-worklist.js", media_type="text/javascript")


@app.get("/", include_in_schema=False)
def landing_page(request: Request) -> RedirectResponse:
    """Open the chart, with a redirect for old directory search bookmarks."""
    query = request.query_params
    if "q" in query or "offset" in query:
        destination = "/people"
        fields = {"tree", "q", "offset"}
    else:
        destination = "/pedigree"
        fields = {"tree", "person"}
    params = urlencode(
        [(key, value) for key, value in query.multi_items() if key in fields]
    )
    return RedirectResponse(f"{destination}?{params}" if params else destination)


@app.get("/people", response_class=HTMLResponse, include_in_schema=False)
def directory(
    request: Request, tree: SelectedTree, q: QueryText = "", offset: Offset = 0
) -> HTMLResponse:
    """Render the directory and preserve the search in pagination links."""
    result = search(tree.path, q, PAGE_SIZE, offset)
    previous = (
        urlencode({"q": q, "offset": max(0, offset - PAGE_SIZE), "tree": tree.slug})
        if offset
        else None
    )
    following = (
        urlencode({"q": q, "offset": offset + PAGE_SIZE, "tree": tree.slug})
        if offset + PAGE_SIZE < result.total
        else None
    )
    return HTMLResponse(
        templates.get_template("page.html").render(
            title="People",
            q=q,
            result=result,
            previous=previous,
            following=following,
            tree=tree,
            trees=reader_access.visible_trees(request),
            reader_session=current_session(request),
            local_system=reader_access.local_browser(request),
        )
    )


@app.get("/people/{person_id}", response_class=HTMLResponse, include_in_schema=False)
def person_page(
    request: Request, person_id: RecordID, tree: SelectedTree
) -> HTMLResponse:
    """Render a browsable person record or a clear not-found page."""
    record = profile(tree.path, PersonID(person_id))
    if record is None:
        return HTMLResponse(
            templates.get_template("page.html").render(
                title="Person not found",
                message="There is no person with that RootsMagic ID.",
                tree=tree,
                trees=reader_access.visible_trees(request),
                reader_session=current_session(request),
                local_system=reader_access.local_browser(request),
            ),
            status_code=404,
        )
    return HTMLResponse(
        templates.get_template("page.html").render(
            title=record.name,
            record=record,
            tree=tree,
            trees=reader_access.visible_trees(request),
            reader_session=current_session(request),
            local_system=reader_access.local_browser(request),
        )
    )


@app.get("/reader.css", include_in_schema=False)
def styles() -> FileResponse:
    """Serve the local stylesheet."""
    return FileResponse(HERE / "reader.css", media_type="text/css")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, access_log=False, proxy_headers=False)
