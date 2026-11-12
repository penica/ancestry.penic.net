"""Administrator-only system configuration pages."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from reader_access import ReaderAccess, admin_form, require_admin
from reader_accounts import AccountsError
from reader_audit import audit
from reader_configuration_routes import configuration_router
from reader_mail import Mail, SMTPSettings

if TYPE_CHECKING:
    from jinja2 import Environment


def system_router(access: ReaderAccess, templates: Environment) -> APIRouter:
    """Register the settings index and SMTP module."""
    router = APIRouter(include_in_schema=False)
    router.include_router(configuration_router(access, templates))

    async def email_page(
        request: Request, *, error: str = "", notice: str = ""
    ) -> HTMLResponse:
        session = require_admin(request)
        settings, password_saved = await run_in_threadpool(
            Mail(access.store()).configuration, session.account.id
        )
        return HTMLResponse(
            templates.get_template("reader-email-settings.html").render(
                current_user=session.account,
                csrf_token=session.csrf_token,
                system_section="email",
                settings=settings,
                password_saved=password_saved,
                error=error,
                notice=notice,
            ),
            status_code=400 if error else 200,
        )

    @router.get("/system")
    def system_home(request: Request) -> Response:
        if access.local_browser(request):
            return RedirectResponse("/system/trees", status_code=303)
        access.store()
        session = require_admin(request)
        return HTMLResponse(
            templates.get_template("reader-system.html").render(
                current_user=session.account,
                csrf_token=session.csrf_token,
            )
        )

    @router.get("/system/email")
    async def email_settings(request: Request, saved: bool = False) -> HTMLResponse:
        return await email_page(
            request, notice="Email server settings saved." if saved else ""
        )

    @router.post("/system/email")
    async def save_email(request: Request) -> Response:
        form = await admin_form(request)
        actor = require_admin(request).account.id
        try:
            settings = SMTPSettings.parse(
                form.get("host", ""),
                form.get("port", ""),
                form.get("security", ""),
                form.get("username", ""),
                form.get("sender", ""),
            )
            await run_in_threadpool(
                Mail(access.store()).save, actor, settings, form.get("password", "")
            )
        except AccountsError as error:
            return await email_page(request, error=str(error))
        audit("mail_settings_changed", "ok", actor=actor)
        return RedirectResponse("/system/email?saved=true", status_code=303)

    @router.post("/system/email/test")
    async def test_email(request: Request) -> HTMLResponse:
        form = await admin_form(request)
        actor = require_admin(request).account.id
        try:
            await run_in_threadpool(
                Mail(access.store()).send,
                actor,
                form.get("recipient", ""),
                "RootsMagic Reader test email",
                "This test email was requested in RootsMagic Reader's System settings.",
            )
        except AccountsError as error:
            audit("test_mail", "failed", actor=actor)
            return await email_page(request, error=str(error))
        audit("test_mail", "ok", actor=actor)
        return await email_page(
            request, notice="Test email accepted by the mail server."
        )

    return router
