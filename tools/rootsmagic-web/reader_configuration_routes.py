"""Tree, chart, security, and backup options for the reader."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from secrets import token_urlsafe
from typing import TYPE_CHECKING, Final, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from reader_access import (
    ReaderAccess,
    admin_form,
    require_admin,
    read_form,
    verify_csrf,
)
from reader_audit import audit
from reader_person_routes import read_tree_person
from reader_settings import (
    BackupSettings,
    ChartSettings,
    SecuritySettings,
    SettingsError,
    SettingsModel,
    SourceSettings,
    save_settings,
    site_settings,
)
from store import connect
from tree_catalog import available_trees

if TYPE_CHECKING:
    from jinja2 import Environment

LOCAL_SETTINGS_TOKEN: Final = token_urlsafe(32)
SYSTEM_PAGES: Final = {
    "trees": "Trees and chart",
    "sources": "Local files",
    "security": "Security and limits",
    "backups": "Backups",
}
Page = Literal["trees", "sources", "security", "backups"]


class BackupStatus(SettingsModel):
    verified_at: datetime
    comments: bool = False
    settings: bool = False


def backup_status() -> BackupStatus | None:
    """Read verification metadata only, never the backup contents."""
    path = os.environ.get("ROOTSMAGIC_BACKUP_STATUS_FILE")
    if not path:
        return None
    try:
        with Path(path).open("rb") as file:
            return BackupStatus.model_validate_json(file.read(4096))
    except (OSError, ValidationError):
        return None


def tree_file_option(value: str) -> str:
    """Accept an existing local RootsMagic database that the reader can query."""
    if not value:
        return ""
    path = Path(value).expanduser().resolve()
    if path.suffix.casefold() != ".rmtree" or not path.is_file():
        raise SettingsError("Choose an existing .rmtree file.")
    try:
        with connect(path) as database:
            database.execute("SELECT PersonID FROM PersonTable LIMIT 1")
            database.execute("SELECT NameID FROM NameTable LIMIT 1")
    except sqlite3.Error as error:
        raise SettingsError(
            "The selected file could not be read as a RootsMagic tree."
        ) from error
    return str(path)


def tree_folder_option(value: str) -> str:
    """Accept a local directory for RootsMagic file discovery."""
    if not value:
        return ""
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise SettingsError("Choose an existing folder for tree discovery.")
    return str(path)


def save_chart_settings(value: ChartSettings) -> None:
    """Validate the selected tree and starting person before saving."""
    settings = site_settings()
    catalog = available_trees(settings.model_copy(update={"chart": ChartSettings()}))
    tree = next((tree for tree in catalog if tree.slug == value.tree), None)
    if value.tree and tree is None:
        raise SettingsError("Choose an available tree.")
    if value.person is not None:
        selected = tree or (catalog[0] if catalog else None)
        if selected is None or read_tree_person(selected, value.person) is None:
            raise SettingsError(
                "The starting person ID must exist in the selected tree."
            )
        value = value.model_copy(update={"tree": selected.slug})
    save_settings("chart", value)


def save_source_settings(value: SourceSettings) -> None:
    """Store checked local paths without remapping an existing tree identifier."""
    file = tree_file_option(value.file or "")
    folder = tree_folder_option(value.folder or "")
    save_settings(
        "sources", SourceSettings(file=file, folder=folder, publish=value.publish)
    )


def configuration_router(access: ReaderAccess, templates: Environment) -> APIRouter:
    """Register the independent System settings sections."""
    router = APIRouter(include_in_schema=False)

    def check_access(request: Request, section: Page) -> bool:
        local = access.local_browser(request)
        if local and section in {"trees", "sources"}:
            return True
        if access.accounts is None or section == "sources":
            raise HTTPException(
                status_code=404, detail="This setting is unavailable here."
            )
        require_admin(request)
        return False

    def render(
        request: Request, section: Page, *, error: str = "", saved: bool = False
    ) -> HTMLResponse:
        local = check_access(request, section)
        session = None if local else require_admin(request)
        settings = site_settings()
        trees = available_trees(settings) if section == "trees" else ()
        valid_default = any(tree.slug == settings.chart.tree for tree in trees)
        source_file = settings.sources.file
        source_folder = settings.sources.folder
        publish = settings.sources.publish
        return HTMLResponse(
            templates.get_template("reader-configuration.html").render(
                current_user=session.account if session else None,
                csrf_token=session.csrf_token if session else LOCAL_SETTINGS_TOKEN,
                system_local=local,
                system_section=section,
                section_title=SYSTEM_PAGES[section],
                chart=settings.chart,
                default_tree=settings.chart.tree if valid_default else "",
                trees=trees,
                security=settings.security,
                backups=settings.backups,
                latest=backup_status() if section == "backups" else None,
                public_origin=access.origin,
                shared_proxy=bool(access.shared_proxies),
                source_file=os.environ.get("ROOTSMAGIC_DB", "")
                if source_file is None
                else source_file,
                source_folder=os.environ.get("ROOTSMAGIC_TREE_DIR", "")
                if source_folder is None
                else source_folder,
                publish=os.environ.get("ROOTSMAGIC_ENABLE_PUBLISH") == "1"
                if publish is None
                else publish,
                error=error,
                notice="Settings saved." if saved else "",
            ),
            status_code=400 if error else 200,
        )

    async def save(request: Request, section: Page) -> Response:
        local = check_access(request, section)
        if local:
            form = await read_form(request)
            verify_csrf(LOCAL_SETTINGS_TOKEN, form.get("csrf_token", ""))
            expected = f"{request.url.scheme}://{request.url.netloc}"
            origin = request.headers.getlist("Origin")
            if origin != [expected] and not (
                origin in ([], ["null"])
                and request.headers.getlist("Sec-Fetch-Site") == ["same-origin"]
            ):
                raise HTTPException(
                    status_code=403, detail="Open the local reader and try again."
                )
        else:
            form = await admin_form(request)
        try:
            if section == "trees":
                chart = ChartSettings(
                    tree=form.get("tree", ""),
                    person=form.get("person") or None,
                    depth=int(form.get("depth", "")),
                    children=form.get("children") == "on",
                    siblings=form.get("siblings") == "on",
                )
                await run_in_threadpool(save_chart_settings, chart)
            elif section == "sources":
                sources = SourceSettings(
                    file=form.get("file", "").strip(),
                    folder=form.get("folder", "").strip(),
                    publish=form.get("publish") == "on",
                )
                await run_in_threadpool(save_source_settings, sources)
            elif section == "security":
                security = SecuritySettings.model_validate(
                    {name: form.get(name, "") for name in SecuritySettings.model_fields}
                )
                await run_in_threadpool(save_settings, "security", security)
            else:
                backups = BackupSettings.model_validate(
                    {"retention": form.get("retention", "")}
                )
                await run_in_threadpool(save_settings, "backups", backups)
        except SettingsError as error:
            return await run_in_threadpool(render, request, section, error=str(error))
        except (ValidationError, ValueError):
            return await run_in_threadpool(
                render, request, section, error="Check the values and try again."
            )
        if not local:
            audit(
                "system_settings_changed", "ok", actor=require_admin(request).account.id
            )
        return RedirectResponse(f"/system/{section}?saved=true", status_code=303)

    def add_section(section: Page) -> None:
        def show(request: Request, saved: bool = False) -> HTMLResponse:
            return render(request, section, saved=saved)

        async def submit(request: Request) -> Response:
            return await save(request, section)

        route = f"/system/{section}"
        router.add_api_route(route, show, methods=["GET"], name=f"show_{section}")
        router.add_api_route(route, submit, methods=["POST"], name=f"save_{section}")

    for section in ("trees", "sources", "security", "backups"):
        add_section(section)
    return router
