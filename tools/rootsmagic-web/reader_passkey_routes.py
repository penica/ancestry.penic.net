"""WebAuthn registration and sign-in routes for the public reader hostname."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from pydantic import ConfigDict, Field, JsonValue, ValidationError
from starlette.concurrency import run_in_threadpool

from reader_access import (
    FORM_COOKIE,
    SESSION_COOKIE,
    read_form,
    read_json,
    require_session,
    safe_next,
    set_session_cookie,
    verify_csrf,
)
from reader_accounts import TOKEN, AccountsError, LoginThrottled
from reader_audit import audit
from reader_passkeys import Passkeys
from store import Record

if TYPE_CHECKING:
    from jinja2 import Environment

    from reader_access import ReaderAccess
    from reader_passkeys import Ceremony


class BrowserCredential(Record):
    """Browser JSON from WebAuthn with optional UI values."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    credential: dict[str, JsonValue]
    name: Annotated[str, Field(max_length=80)] = ""
    next: Annotated[str, Field(max_length=4096)] = ""


def passkeys_available(access: ReaderAccess, request: Request) -> bool:
    """Keep passkeys tied to the public HTTPS hostname."""
    return (
        access.accounts is not None
        and request.url.scheme == "https"
        and request.headers.getlist("Host") == [urlsplit(access.origin).netloc]
    )


def require_public_origin(access: ReaderAccess, request: Request) -> None:
    if not passkeys_available(access, request):
        raise HTTPException(
            status_code=404, detail="Open the public reader to use passkeys."
        )
    if not access.same_origin(request):
        raise HTTPException(status_code=403, detail="Open the reader and try again.")


def header(request: Request, name: str) -> str:
    values = request.headers.getlist(name)
    return values[0] if len(values) == 1 else ""


def ceremony_response(ceremony: Ceremony) -> JSONResponse:
    return JSONResponse(
        {"token": ceremony.token, "options": json.loads(ceremony.options)}
    )


async def browser_credential(request: Request) -> tuple[str, BrowserCredential]:
    token = header(request, "X-Passkey-Ceremony")
    if not TOKEN.fullmatch(token):
        raise HTTPException(status_code=400, detail="Start the passkey request again.")
    try:
        credential = BrowserCredential.model_validate_json(
            await read_json(request, 64 * 1024)
        )
    except ValidationError as error:
        raise HTTPException(
            status_code=422, detail="The passkey response is invalid."
        ) from error
    return token, credential


def passkey_error(error: AccountsError) -> JSONResponse:
    if isinstance(error, LoginThrottled):
        return JSONResponse(
            {"detail": str(error)},
            status_code=429,
            headers={"Retry-After": str(error.retry_after)},
        )
    return JSONResponse({"detail": str(error)}, status_code=400)


def passkey_router(access: ReaderAccess, templates: Environment) -> APIRouter:
    """Register public sign-in and authenticated credential management."""
    router = APIRouter(include_in_schema=False)

    async def management_page(
        request: Request, *, error: str = "", updated: bool = False
    ) -> HTMLResponse:
        session = require_session(request)
        available = passkeys_available(access, request)
        passkeys = (
            await run_in_threadpool(
                Passkeys(access.store(), access.origin).listing, session.account.id
            )
            if available
            else ()
        )
        return HTMLResponse(
            templates.get_template("reader-passkeys.html").render(
                current_user=session.account,
                csrf_token=session.csrf_token,
                passkeys=passkeys,
                passkeys_available=available,
                public_origin=access.origin,
                profile_page=True,
                error=error,
                notice="Passkey settings saved." if updated else "",
            ),
            status_code=400 if error else 200,
        )

    @router.get("/reader-passkeys.js")
    def script() -> FileResponse:
        return FileResponse(
            Path(__file__).with_name("reader-passkeys.js"), media_type="text/javascript"
        )

    @router.get("/account/passkeys")
    async def passkey_settings(request: Request, updated: bool = False) -> HTMLResponse:
        return await management_page(request, updated=updated)

    @router.post("/account/passkeys/options")
    async def start_registration(request: Request) -> JSONResponse:
        require_public_origin(access, request)
        session = require_session(request)
        form = await read_form(request)
        verify_csrf(session.csrf_token, form.get("csrf_token", ""))
        try:
            ceremony = await run_in_threadpool(
                Passkeys(access.store(), access.origin).registration,
                request.cookies.get(SESSION_COOKIE),
                access.client_address(request),
                form.get("password", ""),
                form.get("code", ""),
            )
        except AccountsError as error:
            return passkey_error(error)
        return ceremony_response(ceremony)

    @router.post("/account/passkeys/verify")
    async def finish_registration(request: Request) -> JSONResponse:
        require_public_origin(access, request)
        session = require_session(request)
        verify_csrf(session.csrf_token, header(request, "X-CSRF-Token"))
        token, payload = await browser_credential(request)
        try:
            await run_in_threadpool(
                Passkeys(access.store(), access.origin).register,
                request.cookies.get(SESSION_COOKIE),
                token,
                json.dumps(payload.credential),
                payload.name,
            )
        except AccountsError as error:
            return passkey_error(error)
        audit("passkey_changed", "ok", actor=session.account.id)
        return JSONResponse({"next": "/account/passkeys?updated=true"})

    @router.post("/account/passkeys/{action}")
    async def manage(request: Request, action: Literal["rename", "remove"]) -> Response:
        require_public_origin(access, request)
        session = require_session(request)
        form = await read_form(request)
        verify_csrf(session.csrf_token, form.get("csrf_token", ""))
        try:
            await run_in_threadpool(
                Passkeys(access.store(), access.origin).manage,
                request.cookies.get(SESSION_COOKIE),
                access.client_address(request),
                form.get("password", ""),
                form.get("code", ""),
                form.get("credential", ""),
                form.get("name", "") if action == "rename" else None,
            )
        except AccountsError as error:
            return await management_page(request, error=str(error))
        audit("passkey_changed", "ok", actor=session.account.id)
        return RedirectResponse("/account/passkeys?updated=true", status_code=303)

    @router.post("/login/passkey/options")
    async def start_login(request: Request) -> JSONResponse:
        require_public_origin(access, request)
        browser = request.cookies.get(FORM_COOKIE, "")
        verify_csrf(browser, header(request, "X-CSRF-Token"))
        try:
            ceremony = await run_in_threadpool(
                Passkeys(access.store(), access.origin).authentication,
                browser,
                access.client_address(request),
            )
        except AccountsError as error:
            audit(
                "sign_in", "limited" if isinstance(error, LoginThrottled) else "denied"
            )
            return passkey_error(error)
        return ceremony_response(ceremony)

    @router.post("/login/passkey/verify")
    async def finish_login(request: Request) -> JSONResponse:
        require_public_origin(access, request)
        browser = request.cookies.get(FORM_COOKIE, "")
        verify_csrf(browser, header(request, "X-CSRF-Token"))
        token, payload = await browser_credential(request)
        store = access.store()
        try:
            issued = await run_in_threadpool(
                Passkeys(store, access.origin).login,
                browser,
                token,
                json.dumps(payload.credential),
            )
        except AccountsError as error:
            audit("sign_in", "denied")
            return passkey_error(error)
        await run_in_threadpool(store.logout, request.cookies.get(SESSION_COOKIE))
        audit("sign_in", "ok", actor=issued.session.account.id)
        response = JSONResponse({"next": safe_next(payload.next)})
        set_session_cookie(response, issued)
        return response

    return router
