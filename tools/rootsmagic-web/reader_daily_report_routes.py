"""The hosted daily report schedule and each account's subscription form."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from reader_access import SESSION_COOKIE, admin_form, require_admin, require_session
from reader_accounts import AccountsError
from reader_daily_report import read_subscription, save_subscription
from reader_mail import Mail
from reader_mfa_routes import security_form
from reader_settings import DailyReportSettings, save_settings, site_settings

if TYPE_CHECKING:
    from jinja2 import Environment

    from reader_access import ReaderAccess


def daily_report_router(access: ReaderAccess, templates: Environment) -> APIRouter:
    """Register the settings only when the reader has hosted accounts."""
    router = APIRouter(include_in_schema=False)

    def admin_page(
        request: Request, *, saved: bool = False, error: str = ""
    ) -> HTMLResponse:
        session = require_admin(request)
        schedule = site_settings().daily_report
        return HTMLResponse(
            templates.get_template("reader-daily-report-admin.html").render(
                current_user=session.account,
                csrf_token=session.csrf_token,
                system_section="daily-report",
                schedule=schedule,
                delivery_time=schedule.delivery_time.strftime("%H:%M")
                if schedule.delivery_time
                else "",
                mail_ready=Mail(access.store()).account_links_available(),
                error=error,
                notice="Daily report schedule saved." if saved else "",
            ),
            status_code=400 if error else 200,
        )

    def profile_page(
        request: Request, *, saved: bool = False, error: str = ""
    ) -> HTMLResponse:
        session = require_session(request)
        accounts = access.store()
        trees = tuple(
            tree for tree in access.visible_trees(request) if tree.path.is_file()
        )
        return HTMLResponse(
            templates.get_template("reader-daily-report.html").render(
                current_user=session.account,
                csrf_token=session.csrf_token,
                profile_page=True,
                trees=trees,
                selected_tree=read_subscription(accounts, session.account.id),
                enabled=site_settings().daily_report.enabled,
                mail_ready=Mail(accounts).account_links_available(),
                error=error,
                notice="Daily report preference saved." if saved else "",
            ),
            status_code=400 if error else 200,
        )

    @router.get("/system/daily-report")
    def schedule(request: Request, saved: bool = False) -> HTMLResponse:
        return admin_page(request, saved=saved)

    @router.post("/system/daily-report")
    async def update_schedule(request: Request) -> Response:
        form = await admin_form(request)
        try:
            settings = DailyReportSettings.model_validate(
                {
                    "enabled": form.get("enabled") == "on",
                    "delivery_time": form.get("delivery_time") or None,
                    "timezone": form.get("timezone", "").strip(),
                }
            )
        except ValidationError:
            return await run_in_threadpool(
                admin_page,
                request,
                error="Check the delivery time and time zone. A delivery time is required when sending is enabled.",
            )
        await run_in_threadpool(save_settings, "daily_report", settings)
        return RedirectResponse("/system/daily-report?saved=true", status_code=303)

    @router.get("/account/daily-report")
    def subscription(request: Request, saved: bool = False) -> HTMLResponse:
        return profile_page(request, saved=saved)

    @router.post("/account/daily-report")
    async def update_subscription(request: Request) -> Response:
        form = await security_form(request)
        tree = form.get("tree", "") if form.get("subscribed") == "on" else None
        try:
            await run_in_threadpool(
                save_subscription,
                access.store(),
                request.cookies.get(SESSION_COOKIE),
                tree,
            )
        except AccountsError as error:
            return await run_in_threadpool(profile_page, request, error=str(error))
        return RedirectResponse("/account/daily-report?saved=true", status_code=303)

    return router
