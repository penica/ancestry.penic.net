"""Verification of contact addresses and one-time password recovery."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Final
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from reader_access import FORM_COOKIE, SESSION_COOKIE, form_token, sign_in_form
from reader_accounts import AccountsError, EmailPurpose, LoginThrottled
from reader_audit import audit
from reader_mail import Mail, MailError
from reader_mfa_routes import security_form

if TYPE_CHECKING:
    from jinja2 import Environment

    from reader_access import ReaderAccess

VERIFY_PATH: Final = "/email/verify"
RESET_PATH: Final = "/password/reset"
LINK_COOKIE: Final = {
    "verify": "__Host-rootsmagic-email-verification",
    "reset": "__Host-rootsmagic-password-reset",
}


def email_router(access: ReaderAccess, templates: Environment) -> APIRouter:
    """Register the hosted account mail flows."""
    router = APIRouter(include_in_schema=False)

    def page(
        request: Request, mode: str, *, error: str = "", status: int = 200
    ) -> HTMLResponse:
        csrf = form_token(request)
        response = HTMLResponse(
            templates.get_template("reader-email-link.html").render(
                mode=mode, csrf_token=csrf, error=error
            ),
            status_code=status,
        )
        response.set_cookie(
            FORM_COOKIE, csrf, max_age=1800, secure=True, httponly=True, samesite="lax"
        )
        return response

    def send_link(recipient: str, token: str, purpose: EmailPurpose) -> None:
        path = VERIFY_PATH if purpose == "verify" else RESET_PATH
        description = "Verify your email address" if purpose == "verify" else "Reset your password"
        url = access.origin + path + "?" + urlencode({"token": token})
        try:
            Mail(access.store()).send_saved(
                recipient,
                f"RootsMagic Reader · {description}",
                f"{description} using this one-time link. It expires in 30 minutes.\n\n"
                f"{url}\n\nIf you did not request this, you can ignore this email.",
                password_reset=purpose == "reset",
            )
        except MailError:
            access.store().discard_email_token(token)
            audit("account_email", "failed")
            raise
        audit("account_email", "ok")

    def deliver_reset(username: str, visitor: str | None) -> None:
        try:
            store = access.store()
            if not Mail(store).password_reset_available():
                return
            if delivery := store.password_reset_link(username, visitor):
                send_link(*delivery, "reset")
        except (LoginThrottled, MailError):
            return

    @router.post("/account/email/verify")
    async def request_verification(request: Request) -> Response:
        store = access.store()
        form = await security_form(request)
        try:
            if not await run_in_threadpool(Mail(store).configured):
                raise MailError("Configure the email server first.")
            recipient, token = await run_in_threadpool(
                store.email_verification_link, request.cookies.get(SESSION_COOKIE),
                access.client_address(request), form.get("current_password", "")
            )
            await run_in_threadpool(send_link, recipient, token, "verify")
        except AccountsError as error:
            return page(request, "verify-error", error=str(error), status=400)
        return RedirectResponse("/account/email?updated=verification-sent", status_code=303)

    def link_page(request: Request, token: str, purpose: EmailPurpose) -> Response:
        store = access.store()
        path = VERIFY_PATH if purpose == "verify" else RESET_PATH
        cookie = LINK_COOKIE[purpose]
        if token:
            response = RedirectResponse(path, status_code=303)
            response.set_cookie(cookie, token, max_age=1800, secure=True, httponly=True, samesite="lax")
            return response
        if store.email_token_available(request.cookies.get(cookie), purpose):
            return page(request, purpose)
        if purpose == "reset" and cookie not in request.cookies:
            return page(request, "request")
        return page(request, "invalid", status=400)

    @router.get(VERIFY_PATH)
    def verify_form(request: Request, token: Annotated[str, Query(max_length=128)] = "") -> Response:
        return link_page(request, token, "verify")

    @router.get(RESET_PATH)
    def reset_form(request: Request, token: Annotated[str, Query(max_length=128)] = "") -> Response:
        return link_page(request, token, "reset")

    @router.post(VERIFY_PATH)
    async def confirm_email(request: Request) -> Response:
        await sign_in_form(access, request)
        try:
            await run_in_threadpool(
                access.store().verify_email_link, request.cookies.get(LINK_COOKIE["verify"], "")
            )
        except AccountsError as error:
            return page(request, "invalid", error=str(error), status=400)
        response = page(request, "verified")
        response.delete_cookie(LINK_COOKIE["verify"], secure=True, httponly=True, samesite="lax")
        audit("email_verified", "ok")
        return response

    @router.post(RESET_PATH)
    async def reset_password(request: Request) -> Response:
        form = await sign_in_form(access, request)
        if form.get("action") == "request":
            response = page(request, "sent")
            response.background = BackgroundTask(
                deliver_reset, form.get("username", ""), access.client_address(request)
            )
            return response
        store = access.store()
        token = request.cookies.get(LINK_COOKIE["reset"], "")
        if not await run_in_threadpool(store.email_token_available, token, "reset"):
            return page(request, "invalid", status=400)
        password = form.get("password", "")
        try:
            if password != form.get("password_confirm"):
                raise AccountsError("Enter the same password in both fields.")
            await run_in_threadpool(store.redeem_password_reset, token, password)
        except AccountsError as error:
            return page(request, "reset", error=str(error), status=400)
        response = page(request, "reset-done")
        response.delete_cookie(LINK_COOKIE["reset"], secure=True, httponly=True, samesite="lax")
        response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax")
        audit("password_reset", "ok")
        return response

    return router
