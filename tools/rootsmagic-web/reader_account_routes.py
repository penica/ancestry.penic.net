"""Account forms for the hosted reader."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from reader_access import (
    FORM_COOKIE,
    SESSION_COOKIE,
    ReaderAccess,
    admin_form,
    form_token,
    read_form,
    require_admin,
    require_session,
    safe_next,
    sign_in_form,
    signed_in,
    verify_csrf,
)
from reader_accounts import (
    AccountsError,
    LoginChallenge,
    LoginThrottled,
    ProfileField,
    Role,
)
from reader_audit import audit
from reader_mail import Mail, mail_address
from reader_mfa_routes import mfa_router, start_mfa_login
from reader_passkey_routes import passkey_router, passkeys_available
from reader_person_routes import person_router, read_tree_person
from reader_system_routes import system_router
from tree_catalog import available_trees

if TYPE_CHECKING:
    from jinja2 import Environment

PROFILE_NOTICES: Final = {
    "name": "Name saved.",
    "username": "Username saved.",
    "email": "Email saved.",
    "password": "Password changed. Other sessions were signed out.",
}


def configured_tree(slug: str) -> str:
    """Accept an invitation or membership only for a published tree."""
    if not any(tree.slug == slug for tree in available_trees()):
        raise AccountsError("Choose a published tree.")
    return slug


def account_router(access: ReaderAccess, templates: Environment) -> APIRouter:
    """Register the hosted-only sign-in, invitation, and account routes."""
    router = APIRouter(include_in_schema=False)
    router.include_router(person_router(access, templates))
    router.include_router(mfa_router(access, templates))
    router.include_router(passkey_router(access, templates))
    router.include_router(system_router(access, templates))

    def form_page(
        request: Request,
        name: str,
        *,
        username: str = "",
        next_path: str = "/pedigree",
        token: str = "",
        error: str = "",
        status: int = 200,
    ) -> HTMLResponse:
        csrf = form_token(request)
        response = HTMLResponse(
            templates.get_template(name).render(
                username=username,
                next_path=next_path,
                token=token,
                error=error,
                csrf_token=csrf,
                passkeys_available=passkeys_available(access, request),
            ),
            status_code=status,
        )
        response.set_cookie(
            FORM_COOKIE, csrf, max_age=1800, secure=True, httponly=True, samesite="lax"
        )
        return response

    async def admin_page(
        request: Request, *, error: str = "", invitation_url: str = "", notice: str = ""
    ) -> HTMLResponse:
        session = require_admin(request)
        store = access.store()
        accounts = await run_in_threadpool(store.list_accounts, session.account.id)
        invitations = await run_in_threadpool(
            store.list_invitations, session.account.id
        )
        mail_settings, _ = await run_in_threadpool(
            Mail(store).configuration, session.account.id
        )
        return HTMLResponse(
            templates.get_template("reader-admin.html").render(
                current_user=session.account,
                csrf_token=session.csrf_token,
                trees=available_trees(),
                accounts=accounts,
                invitations=tuple(
                    {
                        "id": invite.id,
                        "username": invite.username,
                        "role": invite.role,
                        "tree_slug": invite.tree_slug,
                        "expires_display": datetime.fromtimestamp(
                            invite.expires_at, UTC
                        ).strftime("%d %b %Y at %H:%M UTC"),
                    }
                    for invite in invitations
                ),
                invitation_url=invitation_url,
                system_section="accounts",
                mail_configured=mail_settings is not None,
                notice=notice,
                error=error,
            ),
            status_code=400 if error and not invitation_url else 200,
        )

    def profile_page(
        request: Request,
        *,
        field: ProfileField | None = None,
        tree: str = "",
        error: str = "",
        notice: str = "",
        attempted_value: str | None = None,
        status: int = 200,
    ) -> HTMLResponse:
        store = access.store()
        session = require_session(request)
        person_url = "/account/person"
        person_summary = ""
        if field is None:
            trees = access.visible_trees(request)
            selected = next((choice for choice in trees if choice.slug == tree), None)
            if selected is None and trees:
                selected = trees[0]
            if selected is not None:
                person_url = selected.url(person_url)
                person_id = session.account.person_for(selected.slug)
                if person_id is not None and (
                    person := read_tree_person(selected, person_id)
                ):
                    person_summary = f"{person.name} (#{person.id})"
        return HTMLResponse(
            templates.get_template("reader-profile.html").render(
                current_user=session.account,
                csrf_token=session.csrf_token,
                profile_page=True,
                field=field,
                error=error,
                notice=notice,
                attempted_value=attempted_value,
                person_url=person_url,
                person_summary=person_summary,
                mfa_available=store.mfa_available,
            ),
            status_code=status,
        )

    @router.get("/reader-accounts.css")
    def styles() -> FileResponse:
        return FileResponse(
            Path(__file__).with_name("reader-accounts.css"), media_type="text/css"
        )

    @router.get("/reader-profile.css")
    def profile_styles() -> FileResponse:
        return FileResponse(
            Path(__file__).with_name("reader-profile.css"), media_type="text/css"
        )

    @router.get("/account")
    def account_settings(
        request: Request,
        tree: Annotated[str, Query(max_length=240)] = "",
        updated: Annotated[str, Query(max_length=16)] = "",
    ) -> HTMLResponse:
        return profile_page(request, tree=tree, notice=PROFILE_NOTICES.get(updated, ""))

    @router.get("/account/{field}")
    def edit_profile(request: Request, field: ProfileField) -> HTMLResponse:
        return profile_page(request, field=field)

    @router.post("/account/{field}")
    async def update_profile(request: Request, field: ProfileField) -> Response:
        store = access.store()
        session = require_session(request)
        form = await read_form(request)
        verify_csrf(session.csrf_token, form.get("csrf_token", ""))
        value = form.get("value", "")
        attempted_value = value if field != "password" else None
        client = access.client_address(request)
        try:
            if field == "password" and value != form.get("password_confirm"):
                raise AccountsError("Enter the same new password in both fields.")
            issued = await run_in_threadpool(
                store.update_profile,
                request.cookies.get(SESSION_COOKIE),
                client,
                field,
                value,
                form.get("current_password", ""),
            )
        except LoginThrottled as error:
            response = profile_page(
                request,
                field=field,
                error=str(error),
                attempted_value=attempted_value,
                status=429,
            )
            response.headers["Retry-After"] = str(error.retry_after)
            return response
        except AccountsError as error:
            return profile_page(
                request,
                field=field,
                error=str(error),
                attempted_value=attempted_value,
                status=400,
            )
        destination = f"/account?updated={field}"
        audit("profile_changed", "ok", actor=session.account.id)
        if issued is not None:
            return signed_in(issued, destination)
        return RedirectResponse(destination, status_code=303)

    @router.get("/login")
    def login_page(
        request: Request, next: Annotated[str, Query(max_length=4096)] = ""
    ) -> HTMLResponse:
        access.store()
        return form_page(request, "reader-login.html", next_path=safe_next(next))

    @router.post("/login")
    async def login(request: Request) -> Response:
        store = access.store()
        form = await sign_in_form(access, request)
        username = form.get("username", "")
        destination = safe_next(form.get("next", ""))
        client = access.client_address(request)
        status = 401
        retry_after = 0
        message = "Check your username and password and try again."
        try:
            issued = await run_in_threadpool(
                store.login, username, form.get("password", ""), client
            )
        except LoginThrottled as error:
            issued = None
            status, message = 429, str(error)
            retry_after = error.retry_after
        if issued is not None:
            await run_in_threadpool(
                store.logout, request.cookies.get(SESSION_COOKIE, "")
            )
            if isinstance(issued, LoginChallenge):
                return start_mfa_login(issued, destination)
            audit("sign_in", "ok", actor=issued.session.account.id)
            return signed_in(issued, destination)
        audit("sign_in", "limited" if status == 429 else "denied")
        response = form_page(
            request,
            "reader-login.html",
            username=username,
            next_path=destination,
            error=message,
            status=status,
        )
        if retry_after:
            response.headers["Retry-After"] = str(retry_after)
        return response

    @router.get("/invite")
    def invitation_page(
        request: Request, token: Annotated[str, Query(max_length=128)] = ""
    ) -> HTMLResponse:
        invite = access.store().invitation(token)
        if invite is None:
            return form_page(
                request,
                "reader-invitation.html",
                status=404,
                error="This invitation is unavailable.",
            )
        return form_page(
            request, "reader-invitation.html", token=token, username=invite.username
        )

    @router.post("/invite")
    async def redeem(request: Request) -> Response:
        store = access.store()
        form = await sign_in_form(access, request)
        token = form.get("token", "")
        invite = await run_in_threadpool(store.invitation, token)
        if invite is None:
            return form_page(
                request,
                "reader-invitation.html",
                status=404,
                error="This invitation is unavailable.",
            )
        try:
            password = form.get("password", "")
            if password != form.get("password_confirm"):
                raise AccountsError("Enter the same password in both fields.")
            issued = await run_in_threadpool(store.redeem, token, password)
        except AccountsError as error:
            return form_page(
                request,
                "reader-invitation.html",
                token=token,
                username=invite.username,
                error=str(error),
                status=400,
            )
        await run_in_threadpool(store.logout, request.cookies.get(SESSION_COOKIE, ""))
        audit("invitation_redeemed", "ok", actor=issued.session.account.id)
        return signed_in(
            issued,
            "/system/accounts" if issued.session.account.administrator else "/pedigree",
        )

    @router.post("/logout")
    async def logout(request: Request) -> RedirectResponse:
        store = access.store()
        session = require_session(request)
        form = await read_form(request)
        verify_csrf(session.csrf_token, form.get("csrf_token", ""))
        await run_in_threadpool(store.logout, request.cookies.get(SESSION_COOKIE, ""))
        audit("sign_out", "ok", actor=session.account.id)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(
            SESSION_COOKIE, secure=True, httponly=True, samesite="lax"
        )
        return response

    @router.get("/admin")
    def legacy_administration(request: Request) -> RedirectResponse:
        access.store()
        require_admin(request)
        return RedirectResponse("/system/accounts", status_code=303)

    @router.get("/system/accounts")
    async def administration(request: Request) -> HTMLResponse:
        access.store()
        return await admin_page(request)

    @router.post("/admin/invite")
    async def create_invitation(request: Request) -> HTMLResponse:
        form = await admin_form(request)
        session = require_admin(request)
        store = access.store()
        try:
            role = Role(form.get("role", ""))
            slug = form.get("tree_slug", "")
            tree = configured_tree(slug) if role is Role.READER else None
            recipient = form.get("recipient", "").strip()
            if recipient:
                recipient = mail_address(recipient)
            token = await run_in_threadpool(
                store.invite, session.account.id, form.get("username", ""), tree, role
            )
        except AccountsError as error:
            return await admin_page(request, error=str(error))
        except ValueError:
            return await admin_page(request, error="Choose Reader or Administrator.")
        url = f"{access.origin}/invite?{urlencode({'token': token})}"
        audit("invitation_created", "ok", actor=session.account.id)
        if recipient:
            try:
                await run_in_threadpool(
                    Mail(store).send,
                    session.account.id,
                    recipient,
                    "Your RootsMagic Reader invitation",
                    f"You have been invited to RootsMagic Reader. Open this one-time link to set your password:\n\n{url}\n\nThe link expires in seven days.",
                )
            except AccountsError as error:
                audit("invitation_mail", "failed", actor=session.account.id)
                return await admin_page(
                    request,
                    invitation_url=url,
                    error=f"{error} You can share the link below.",
                )
            audit("invitation_mail", "ok", actor=session.account.id)
            return await admin_page(
                request,
                invitation_url=url,
                notice="Invitation email accepted by the mail server.",
            )
        return await admin_page(request, invitation_url=url)

    @router.post("/admin/account/{account_id}/access")
    async def change_access(request: Request, account_id: int) -> Response:
        form = await admin_form(request)
        actor = require_admin(request).account.id
        store = access.store()
        role = form.get("role", "")
        try:
            if role in {"administrator", "remove-administrator"}:
                await run_in_threadpool(
                    store.set_administrator, actor, account_id, role == "administrator"
                )
            elif role in {"reader", "remove"}:
                slug = configured_tree(form.get("tree_slug", ""))
                await run_in_threadpool(
                    store.set_membership,
                    actor,
                    account_id,
                    slug,
                    Role.READER if role == "reader" else None,
                )
            else:
                raise AccountsError("Choose a role.")
        except AccountsError as error:
            return await admin_page(request, error=str(error))
        audit("account_access_changed", "ok", actor=actor, target=account_id)
        return RedirectResponse("/system/accounts", status_code=303)

    @router.post("/admin/account/{account_id}/status")
    async def change_status(request: Request, account_id: int) -> Response:
        form = await admin_form(request)
        actor = require_admin(request).account.id
        active = form.get("active", "")
        if active not in {"true", "false"}:
            return await admin_page(request, error="Choose an account status.")
        try:
            await run_in_threadpool(
                access.store().set_disabled, actor, account_id, active == "false"
            )
        except AccountsError as error:
            return await admin_page(request, error=str(error))
        audit("account_status_changed", "ok", actor=actor, target=account_id)
        return RedirectResponse("/system/accounts", status_code=303)

    @router.post("/admin/invite/{invitation_id}/revoke")
    async def revoke(request: Request, invitation_id: int) -> Response:
        await admin_form(request)
        actor = require_admin(request).account.id
        try:
            await run_in_threadpool(
                access.store().revoke_invitation, actor, invitation_id
            )
        except AccountsError as error:
            return await admin_page(request, error=str(error))
        audit("invitation_revoked", "ok", actor=actor, target=invitation_id)
        return RedirectResponse("/system/accounts", status_code=303)

    return router
