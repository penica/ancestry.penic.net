"""Administrator key settings and the read-only person lookup API."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from time import time
from typing import TYPE_CHECKING, Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Path, Query, Request, Security
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from reader_access import admin_form, require_admin
from reader_accounts import AccountsError
from reader_api_keys import (
    LOOKUP_PATH,
    ApiKeys,
    IssuedKey,
    KeyConflict,
    KeyID,
    KeyInfo,
    KeyLifetime,
    KeyScope,
    retry_after,
)
from reader_audit import audit
from store import PersonID, Record, connect, person_identity, read_search_rows
from tree_catalog import available_trees

if TYPE_CHECKING:
    from jinja2 import Environment

    from reader_access import ReaderAccess

KeyPath = Annotated[int, Path(ge=1, le=2**63 - 1)]
BEARER = HTTPBearer(scheme_name="ReaderApiKey", auto_error=False)


class KeyLabel(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)
    label: Annotated[str, Field(min_length=1, max_length=80)]


class NewKey(KeyLabel):
    tree_slug: Annotated[str, Field(min_length=1, max_length=240)]
    scope: KeyScope
    lifetime_days: KeyLifetime


class PersonMatch(Record):
    id: PersonID
    unique_id: str | None
    name: str
    birth_year: int | None
    death_year: int | None
    record_path: str


class PersonMatches(Record):
    tree_slug: str
    people: tuple[PersonMatch, ...]
    total: int
    limit: int
    offset: int


def timestamp(value: int) -> str:
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d %H:%M UTC")


def key_version(value: str) -> int:
    if value.isascii() and value.isdecimal() and len(value) <= 18 and int(value) > 0:
        return int(value)
    raise KeyConflict("This API key changed. Reload the page and try again.")


def api_key_settings_router(access: ReaderAccess, templates: Environment) -> APIRouter:
    router = APIRouter(prefix="/system/api-keys", include_in_schema=False)
    keys = ApiKeys(access.store())

    async def page(
        request: Request,
        *,
        issued: IssuedKey | None = None,
        error: str = "",
        status: int = 200,
        notice: str = "",
    ) -> HTMLResponse:
        session = require_admin(request)
        records = await run_in_threadpool(keys.list, session.account.id)
        trees = {tree.slug: tree.name for tree in available_trees()}
        return HTMLResponse(
            templates.get_template("reader-api-keys.html").render(
                current_user=session.account,
                csrf_token=session.csrf_token,
                system_section="api-keys",
                keys=records,
                trees=trees,
                issued=issued,
                now=int(time()),
                timestamp=timestamp,
                lookup_path=LOOKUP_PATH,
                origin=access.origin,
                error=error,
                notice=notice,
            ),
            status_code=status,
        )

    @router.get("")
    async def settings(request: Request, updated: str = "") -> HTMLResponse:
        return await page(
            request,
            notice={"renamed": "API key renamed.", "revoked": "API key revoked."}.get(
                updated, ""
            ),
        )

    @router.post("")
    async def create_key(request: Request) -> HTMLResponse:
        form = await admin_form(request)
        actor = require_admin(request).account.id
        days = {"30": 30, "90": 90, "365": 365}.get(form.get("lifetime_days", ""))
        try:
            draft = NewKey.model_validate(form | {"lifetime_days": days})
        except ValidationError:
            return await page(
                request,
                status=400,
                error="Enter a name and choose a tree, scope, and expiry.",
            )
        if not any(
            tree.slug == draft.tree_slug and tree.path.is_file()
            for tree in available_trees()
        ):
            return await page(
                request, status=400, error="Choose an available published tree."
            )
        try:
            issued = await run_in_threadpool(
                keys.create,
                actor,
                draft.label,
                draft.tree_slug,
                draft.scope,
                draft.lifetime_days,
            )
        except AccountsError as error:
            return await page(request, status=400, error=str(error))
        audit("api_key_created", "ok", actor=actor, target=issued.info.id)
        return await page(request, issued=issued)

    @router.post("/{key_id}/rename")
    async def rename_key(request: Request, key_id: KeyPath) -> Response:
        form = await admin_form(request)
        actor = require_admin(request).account.id
        try:
            label = KeyLabel.model_validate(form).label
            await run_in_threadpool(
                keys.rename,
                actor,
                KeyID(key_id),
                key_version(form.get("version", "")),
                label,
            )
        except ValidationError:
            return await page(
                request, status=400, error="Enter a key name of 1 to 80 characters."
            )
        except KeyConflict as error:
            return await page(request, status=409, error=str(error))
        except AccountsError as error:
            return await page(request, status=400, error=str(error))
        audit("api_key_renamed", "ok", actor=actor, target=key_id)
        return RedirectResponse("/system/api-keys?updated=renamed", status_code=303)

    @router.post("/{key_id}/rotate")
    async def rotate_key(request: Request, key_id: KeyPath) -> HTMLResponse:
        form = await admin_form(request)
        actor = require_admin(request).account.id
        try:
            issued = await run_in_threadpool(
                keys.rotate, actor, KeyID(key_id), key_version(form.get("version", ""))
            )
        except KeyConflict as error:
            return await page(request, status=409, error=str(error))
        audit("api_key_rotated", "ok", actor=actor, target=key_id)
        return await page(request, issued=issued)

    @router.post("/{key_id}/revoke")
    async def revoke_key(request: Request, key_id: KeyPath) -> Response:
        form = await admin_form(request)
        actor = require_admin(request).account.id
        try:
            await run_in_threadpool(
                keys.revoke, actor, KeyID(key_id), key_version(form.get("version", ""))
            )
        except KeyConflict as error:
            return await page(request, status=409, error=str(error))
        audit("api_key_revoked", "ok", actor=actor, target=key_id)
        return RedirectResponse("/system/api-keys?updated=revoked", status_code=303)

    return router


def integration_router(access: ReaderAccess) -> APIRouter:
    router = APIRouter(tags=["Integrations"])
    keys = ApiKeys(access.store())

    def require_key(
        request: Request,
        bearer: Annotated[HTTPAuthorizationCredentials | None, Security(BEARER)],
    ) -> KeyInfo:
        key = (
            keys.authenticate(bearer.credentials)
            if request.url.scheme == "https"
            and len(request.headers.getlist("Authorization")) == 1
            and bearer is not None
            else None
        )
        if key is None:
            raise HTTPException(
                status_code=401,
                detail="Use a valid API key over HTTPS.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return key

    @router.get(
        LOOKUP_PATH,
        responses={
            401: {"description": "Missing or invalid API key"},
            429: {"description": "Key rate limit reached"},
        },
    )
    def lookup_people(
        q: Annotated[str, Query(min_length=1, max_length=160)],
        key: KeyInfo = Security(require_key),
        tree: Annotated[str, Query(max_length=240)] = "",
        limit: Annotated[int, Query(ge=1, le=30)] = 20,
        offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    ) -> PersonMatches:
        """Find people by name or RootsMagic ID in the tree assigned to the key."""
        if not q.strip():
            raise HTTPException(
                status_code=422, detail="Enter a name or RootsMagic ID."
            )
        if tree and tree != key.tree_slug:
            raise HTTPException(status_code=404, detail="Selected tree is unavailable.")
        delay = retry_after(key.id)
        if delay:
            raise HTTPException(
                status_code=429,
                detail="Too many lookups.",
                headers={"Retry-After": str(delay)},
            )
        selected = next(
            (
                item
                for item in available_trees()
                if item.slug == key.tree_slug and item.path.is_file()
            ),
            None,
        )
        if selected is None:
            raise HTTPException(status_code=404, detail="Selected tree is unavailable.")
        scoped = replace(selected, external=key.scope == "deceased")
        with connect(scoped.database) as db:
            rows, total = read_search_rows(db, q, limit, offset)
            people = []
            for row in rows:
                person_id = PersonID(row["id"])
                identity = person_identity(db, person_id)
                path = f"/pedigree/people/{person_id}/record"
                if identity:
                    path += "?" + urlencode({"identity": identity})
                people.append(
                    PersonMatch(
                        id=person_id,
                        unique_id=identity,
                        name=row["name"],
                        birth_year=row["birth_year"],
                        death_year=row["death_year"],
                        record_path=scoped.url(path),
                    )
                )
        keys.record_use(key)
        return PersonMatches(
            tree_slug=key.tree_slug,
            people=tuple(people),
            total=total,
            limit=limit,
            offset=offset,
        )

    return router
