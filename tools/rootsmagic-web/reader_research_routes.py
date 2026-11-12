"""Research follow-up list and status changes in the browser."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Final
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from reader_access import current_session, read_form
from reader_comments import comments_token, reader_actor, verify_reader_mutation
from reader_follow_ups import FollowUpStatus
from store import PersonID, person_names

if TYPE_CHECKING:
    from jinja2 import Environment

    from reader_access import ReaderAccess
    from reader_follow_ups import FollowUps
    from tree_catalog import Tree

PAGE_SIZE: Final = 30
PAGE: Final = "/pedigree/follow-ups"
TreeFilter = Annotated[str, Query(max_length=240)]
PersonFilter = Annotated[str, Query(max_length=25)]


def person_filter(query: str) -> PersonID | None:
    value = query.strip().removeprefix("#")
    if not value:
        return None
    if value.isascii() and value.isdigit() and 0 < int(value) < 2**63:
        return PersonID(int(value))
    raise HTTPException(status_code=422, detail="Enter a valid RootsMagic record ID.")


def tree_filter(catalog: tuple[Tree, ...], slug: str) -> tuple[Tree, ...]:
    if not slug:
        return catalog
    selected = tuple(tree for tree in catalog if tree.slug == slug)
    if not selected:
        raise HTTPException(status_code=404, detail="Selected tree is unavailable.")
    return selected


def follow_up_router(
    access: ReaderAccess, store: FollowUps, templates: Environment
) -> APIRouter:
    router = APIRouter(include_in_schema=False)

    @router.get(PAGE)
    def follow_ups_page(
        request: Request,
        tree: TreeFilter = "",
        person: PersonFilter = "",
        status: FollowUpStatus = "open",
        offset: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0,
        updated: Annotated[str, Query(max_length=12)] = "",
    ) -> HTMLResponse:
        actor = reader_actor(request)
        catalog = access.visible_trees(request)
        selected = tree_filter(catalog, tree)
        entries = store.page(
            actor.id,
            tuple(item.slug for item in selected),
            person_filter(person),
            status,
            PAGE_SIZE + 1,
            offset,
        )
        items = entries[:PAGE_SIZE]
        names = {
            item.slug: person_names(
                item.path,
                tuple(
                    {entry.person_id for entry in items if entry.tree_slug == item.slug}
                ),
            )
            for item in selected
        }
        query = urlencode({"tree": tree, "person": person, "status": status})
        return HTMLResponse(
            templates.get_template("reader-follow-ups.html").render(
                tree=selected[0] if selected else None,
                catalog=catalog,
                trees_by_slug={item.slug: item for item in selected},
                selected_tree=tree,
                person=person,
                status=status,
                items=items,
                names=names,
                filters=query,
                next_offset=offset + PAGE_SIZE if len(entries) > PAGE_SIZE else None,
                previous_offset=max(0, offset - PAGE_SIZE) if offset else None,
                csrf_token=comments_token(request),
                reader_session=current_session(request),
                system_local=access.local_browser(request),
                notice={
                    "complete": "Follow-up completed.",
                    "open": "Follow-up reopened.",
                }.get(updated, ""),
            )
        )

    @router.post(PAGE + "/{item_id}/status")
    async def follow_up_status(
        request: Request,
        item_id: Annotated[int, Path(ge=1, le=2**63 - 1)],
        tree: TreeFilter = "",
        person: PersonFilter = "",
        status: FollowUpStatus = "open",
    ) -> RedirectResponse:
        form = await read_form(request)
        actor = verify_reader_mutation(request, form.get("csrf_token", ""))
        catalog = access.visible_trees(request)
        item_tree = form.get("item_tree", "")
        selected = next(
            (candidate for candidate in catalog if candidate.slug == item_tree), None
        )
        if selected is None:
            raise HTTPException(status_code=404, detail="Selected tree is unavailable.")
        complete = form.get("complete", "")
        if complete not in {"yes", "no"}:
            raise HTTPException(status_code=422, detail="Choose a follow-up status.")
        tree_filter(catalog, tree)
        person_filter(person)
        await run_in_threadpool(
            store.set_complete, actor.id, selected, item_id, complete == "yes"
        )
        query = urlencode(
            {
                "tree": tree,
                "person": person,
                "status": status,
                "updated": "complete" if complete == "yes" else "open",
            }
        )
        return RedirectResponse(f"{PAGE}?{query}", status_code=303)

    return router
