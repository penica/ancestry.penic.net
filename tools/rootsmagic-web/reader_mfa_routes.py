"""Two-step sign-in and authenticator setup for VM accounts."""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING, Annotated, Literal
from urllib.parse import urlencode

import qrcode
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from reader_access import (
    FORM_COOKIE,
    MFA_COOKIE,
    SESSION_COOKIE,
    form_token,
    read_form,
    require_session,
    safe_next,
    set_session_cookie,
    sign_in_form,
    signed_in,
    verify_csrf,
)
from reader_accounts import AccountsError, LoginThrottled
from reader_audit import audit
from reader_mfa import MFA_LOGIN_SECONDS

if TYPE_CHECKING:
    from jinja2 import Environment

    from reader_access import ReaderAccess
    from reader_accounts import LoginChallenge, Session
    from reader_mfa import MFASetup


def start_mfa_login(challenge: LoginChallenge, destination: str) -> RedirectResponse:
    """Store a short-lived challenge in a cookie that cannot authorize tree access."""
    query = urlencode({"next": destination})
    response = RedirectResponse(f"/login/verify?{query}", status_code=303)
    response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax")
    response.set_cookie(
        MFA_COOKIE,
        challenge.token,
        max_age=MFA_LOGIN_SECONDS,
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response


async def security_form(request: Request) -> dict[str, str]:
    """Require the current account's token for a security change."""
    session = require_session(request)
    form = await read_form(request)
    verify_csrf(session.csrf_token, form.get("csrf_token", ""))
    return form


def mfa_router(access: ReaderAccess, templates: Environment) -> APIRouter:
    """Register the authenticated setup and pre-authentication verification pages."""
    router = APIRouter(include_in_schema=False)

    def login_form(
        request: Request,
        destination: str,
        *,
        error: str = "",
        status: int = 200,
    ) -> HTMLResponse:
        csrf = form_token(request)
        response = HTMLResponse(
            templates.get_template("reader-two-factor-login.html").render(
                csrf_token=csrf, next_path=destination, error=error
            ),
            status_code=status,
        )
        response.set_cookie(
            FORM_COOKIE, csrf, max_age=1800, secure=True, httponly=True, samesite="lax"
        )
        return response

    def settings_page(
        session: Session,
        *,
        setup: MFASetup | None = None,
        recovery_codes: tuple[str, ...] = (),
        error: str = "",
        notice: str = "",
        status: int = 200,
    ) -> HTMLResponse:
        stage = "recovery" if recovery_codes else "setup" if setup else "overview"
        return HTMLResponse(
            templates.get_template("reader-two-factor.html").render(
                stage=stage,
                current_user=session.account,
                csrf_token=session.csrf_token,
                mfa_enabled=session.account.mfa_enabled,
                secret=setup.secret if setup else "",
                recovery_codes=recovery_codes,
                profile_page=True,
                error=error,
                notice=notice,
            ),
            status_code=status,
        )

    def setup_store(request: Request) -> MFASetup | None:
        return access.store().read_mfa_setup(request.cookies.get(SESSION_COOKIE))

    @router.get("/login/verify")
    def verify_page(
        request: Request, next: Annotated[str, Query(max_length=4096)] = ""
    ) -> Response:
        access.store()
        if MFA_COOKIE not in request.cookies:
            return RedirectResponse("/login", status_code=303)
        return login_form(request, safe_next(next))

    @router.post("/login/verify")
    async def verify_login(request: Request) -> Response:
        store = access.store()
        form = await sign_in_form(access, request)
        destination = safe_next(form.get("next", ""))
        challenge = request.cookies.get(MFA_COOKIE)
        if not challenge:
            return RedirectResponse("/login", status_code=303)
        try:
            issued = await run_in_threadpool(
                store.complete_mfa,
                challenge,
                access.client_address(request),
                form.get("code", ""),
            )
        except LoginThrottled as error:
            audit("sign_in", "limited")
            response = login_form(request, destination, error=str(error), status=429)
            response.headers["Retry-After"] = str(error.retry_after)
            return response
        except AccountsError as error:
            audit("sign_in", "failed")
            return login_form(request, destination, error=str(error), status=503)
        if issued is None:
            audit("sign_in", "denied")
            return login_form(
                request,
                destination,
                error="Use a current authenticator code or a recovery code. Sign in again if the prompt expired.",
                status=401,
            )
        audit("sign_in", "ok", actor=issued.session.account.id)
        return signed_in(issued, destination)

    @router.get("/account/security")
    def security(request: Request, updated: str = "") -> HTMLResponse:
        if not access.store().mfa_available:
            raise HTTPException(
                status_code=404, detail="Two-step verification is unavailable."
            )
        return settings_page(
            require_session(request),
            notice="Two-step verification was turned off."
            if updated == "disabled"
            else "",
        )

    @router.post("/account/security/setup")
    async def begin_setup(request: Request) -> Response:
        store = access.store()
        session = require_session(request)
        form = await security_form(request)
        try:
            await run_in_threadpool(
                store.begin_mfa,
                request.cookies.get(SESSION_COOKIE),
                access.client_address(request),
                form.get("current_password", ""),
            )
        except LoginThrottled as error:
            response = settings_page(session, error=str(error), status=429)
            response.headers["Retry-After"] = str(error.retry_after)
            return response
        except AccountsError as error:
            return settings_page(session, error=str(error), status=400)
        return RedirectResponse("/account/security/setup", status_code=303)

    @router.get("/account/security/setup")
    def setup_page(request: Request) -> Response:
        session = require_session(request)
        setup = setup_store(request)
        if setup is None:
            return RedirectResponse("/account/security", status_code=303)
        return settings_page(session, setup=setup)

    @router.get("/account/security/qr")
    def qr_image(request: Request) -> Response:
        require_session(request)
        setup = setup_store(request)
        if setup is None:
            raise HTTPException(
                status_code=404, detail="Start authenticator setup first."
            )
        buffer = BytesIO()
        qrcode.make(setup.provisioning_uri, box_size=6, border=4).save(
            buffer, format="PNG"
        )
        return Response(buffer.getvalue(), media_type="image/png")

    @router.post("/account/security/confirm")
    async def confirm_setup(request: Request) -> HTMLResponse:
        store = access.store()
        session = require_session(request)
        form = await security_form(request)
        try:
            result = await run_in_threadpool(
                store.confirm_mfa,
                request.cookies.get(SESSION_COOKIE),
                access.client_address(request),
                form.get("code", ""),
            )
        except AccountsError as error:
            setup = await run_in_threadpool(setup_store, request)
            status = 429 if isinstance(error, LoginThrottled) else 400
            response = settings_page(
                session, setup=setup, error=str(error), status=status
            )
            if isinstance(error, LoginThrottled):
                response.headers["Retry-After"] = str(error.retry_after)
            return response
        response = settings_page(
            result.issued.session,
            recovery_codes=result.recovery_codes,
            notice="Two-step verification is on.",
        )
        set_session_cookie(response, result.issued)
        audit("mfa_changed", "ok", actor=session.account.id)
        return response

    @router.post("/account/security/{action}")
    async def manage_mfa(
        request: Request, action: Literal["disable", "recovery"]
    ) -> Response:
        store = access.store()
        session = require_session(request)
        form = await security_form(request)
        args = (
            request.cookies.get(SESSION_COOKIE),
            access.client_address(request),
            form.get("current_password", ""),
            form.get("code", ""),
        )
        try:
            if action == "disable":
                issued = await run_in_threadpool(store.disable_mfa, *args)
                audit("mfa_changed", "ok", actor=session.account.id)
                return signed_in(issued, "/account/security?updated=disabled")
            result = await run_in_threadpool(store.regenerate_recovery_codes, *args)
        except LoginThrottled as error:
            response = settings_page(session, error=str(error), status=429)
            response.headers["Retry-After"] = str(error.retry_after)
            return response
        except AccountsError as error:
            return settings_page(session, error=str(error), status=400)
        response = settings_page(
            result.issued.session,
            recovery_codes=result.recovery_codes,
            notice="New recovery codes are ready.",
        )
        set_session_cookie(response, result.issued)
        audit("mfa_changed", "ok", actor=session.account.id)
        return response

    return router
