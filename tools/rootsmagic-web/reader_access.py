"""Authentication and request guards for the hosted reader."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, replace
from ipaddress import ip_address
from pathlib import Path
from secrets import compare_digest, token_urlsafe
from typing import TYPE_CHECKING, Final
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse

from reader_accounts import Accounts, Role, Session
from reader_api_keys import LOOKUP_PATH
from tree_catalog import Tree, available_trees

if TYPE_CHECKING:
    from fastapi import Request
    from starlette.responses import Response

    from reader_accounts import IssuedSession

SESSION_COOKIE: Final = "__Host-rootsmagic-session"
FORM_COOKIE: Final = "__Host-rootsmagic-form"
MFA_COOKIE: Final = "__Host-rootsmagic-verify"
FORM_LIMIT: Final = 8192
PUBLIC_PATHS: Final = frozenset(
    {
        "/login",
        "/login/verify",
        "/login/passkey/options",
        "/login/passkey/verify",
        "/invite",
        "/reader.css",
        "/reader-accounts.css",
        "/reader-passkeys.js",
    }
)
SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})


def https_origin(value: str, setting: str) -> str:
    """Read a configured HTTPS origin without a path, query, or credentials."""
    message = f"Set {setting} to the reader's HTTPS origin."
    try:
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.port == 0
            or value != f"https://{parts.netloc}"
            or any(character.isspace() for character in value)
            or any(character in parts.netloc for character in "*%\\")
        ):
            raise RuntimeError(message)
    except ValueError as error:
        raise RuntimeError(message) from error
    return value.lower()


@dataclass(frozen=True, slots=True)
class ReaderAccess:
    """The hosted sign-in configuration. The Mac reader omits it."""

    accounts: Accounts | None = None
    origin: str = ""
    additional_origins: tuple[str, ...] = ()
    shared_proxies: frozenset[str] = frozenset()

    @classmethod
    def from_environment(cls) -> ReaderAccess:
        """Read the trusted origin and account database from deployment settings."""
        require_accounts = os.environ.get("ROOTSMAGIC_REQUIRE_ACCOUNTS") == "1"
        database = os.environ.get("ROOTSMAGIC_ACCOUNTS_DB")
        if not database:
            if require_accounts:
                message = "Hosted mode requires ROOTSMAGIC_ACCOUNTS_DB."
                raise RuntimeError(message)
            return cls()
        origin = https_origin(
            os.environ.get("ROOTSMAGIC_PUBLIC_ORIGIN", ""), "ROOTSMAGIC_PUBLIC_ORIGIN"
        )
        additional_origins = tuple(
            https_origin(value.strip(), "ROOTSMAGIC_ADDITIONAL_ORIGINS")
            for value in os.environ.get("ROOTSMAGIC_ADDITIONAL_ORIGINS", "").split(",")
            if value.strip()
        )
        key_file = os.environ.get("ROOTSMAGIC_MFA_KEY_FILE")
        try:
            shared_proxies = frozenset(
                str(ip_address(value.strip()))
                for value in os.environ.get(
                    "ROOTSMAGIC_SHARED_PROXY_ADDRESSES", ""
                ).split(",")
                if value.strip()
            )
        except ValueError as error:
            raise RuntimeError(
                "Set ROOTSMAGIC_SHARED_PROXY_ADDRESSES to exact IP addresses."
            ) from error
        accounts = Accounts(Path(database), Path(key_file) if key_file else None)
        if require_accounts:
            try:
                accounts.check_schema()
            except (OSError, sqlite3.Error) as error:
                message = "Cannot open the reader accounts database. Check ROOTSMAGIC_ACCOUNTS_DB."
                raise RuntimeError(message) from error
        return cls(accounts, origin, additional_origins, shared_proxies)

    def client_address(self, request: Request) -> str | None:
        """Use the trusted socket address when it identifies a visitor."""
        if request.client is None or request.client.host in self.shared_proxies:
            return None
        return request.client.host

    def local_browser(self, request: Request) -> bool:
        """Identify the Mac reader's direct, loopback browser connection."""
        loopback = {"localhost", "127.0.0.1", "::1"}
        return (
            self.accounts is None
            and request.url.scheme == "http"
            and request.url.hostname in loopback
            and request.client is not None
            and request.client.host in loopback
        )

    def store(self) -> Accounts:
        """Return the account store for a hosted-only route."""
        if self.accounts is None:
            raise HTTPException(status_code=404, detail="Page unavailable.")
        return self.accounts

    def same_origin(self, request: Request) -> bool:
        """Require the browser origin to match the configured destination host."""
        hosts = request.headers.getlist("Host")
        if len(hosts) != 1:
            return False
        expected = f"https://{hosts[0].lower()}"
        if expected != self.origin and expected not in self.additional_origins:
            return False
        origins = request.headers.getlist("Origin")
        return origins == [expected] or (
            origins in ([], ["null"])
            and request.headers.getlist("Sec-Fetch-Site") == ["same-origin"]
        )

    async def guard(self, request: Request) -> Response | None:
        """Authenticate before the application can read protected tree data."""
        if self.accounts is None or request.url.path in PUBLIC_PATHS:
            return None
        if request.method == "GET" and request.url.path == LOOKUP_PATH:
            # The integration route authenticates the key and checks its tree grant.
            return None
        api = request.url.path.startswith("/api/")
        try:
            session = await run_in_threadpool(
                self.accounts.session, request.cookies.get(SESSION_COOKIE)
            )
        except (OSError, sqlite3.Error):
            return failure("Sign-in is temporarily unavailable.", 503, api=api)
        if session is None:
            if api or request.method not in SAFE_METHODS:
                return failure("Sign in to use the reader.", 401, api=api)
            path = request.url.path
            if request.url.query:
                path += f"?{request.url.query}"
            return RedirectResponse(
                f"/login?{urlencode({'next': path})}", status_code=303
            )
        request.state.reader_session = session
        if request.method not in SAFE_METHODS and not self.same_origin(request):
            return failure("Open the reader and try again.", 403, api=api)
        return None

    def visible_trees(self, request: Request) -> tuple[Tree, ...]:
        """List just the trees accessible to the current account."""
        catalog = available_trees()
        if self.accounts is None:
            return catalog
        session = current_session(request)
        if session is None:
            return ()
        return tuple(
            replace(tree, external=role is Role.EXTERNAL_READER)
            for tree in catalog
            if (role := session.account.role_for(tree.slug)) is not None
        )

    def family_trees(self, request: Request) -> tuple[Tree, ...]:
        return tuple(tree for tree in self.visible_trees(request) if not tree.external)


def require_family_tree(tree: Tree) -> None:
    """Keep private collaboration and evidence on family memberships."""
    if tree.external:
        raise HTTPException(
            status_code=403, detail="This feature is available with family access."
        )


def failure(message: str, status: int, *, api: bool) -> Response:
    """Send an appropriate response before routing unauthenticated requests."""
    if api:
        return JSONResponse({"detail": message}, status_code=status)
    return PlainTextResponse(message, status_code=status)


def current_session(request: Request) -> Session | None:
    """Read the session attached by the request guard."""
    return getattr(request.state, "reader_session", None)


def require_session(request: Request) -> Session:
    """Return the signed-in user for a hosted route."""
    session = current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Sign in to use the reader.")
    return session


def require_admin(request: Request) -> Session:
    """Authorize account administration independently of tree membership."""
    session = require_session(request)
    if not session.account.administrator:
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return session


def verify_csrf(expected: str, supplied: str) -> None:
    """Require the current form or session token on state-changing requests."""
    if not expected or not compare_digest(expected.encode(), supplied.encode()):
        raise HTTPException(status_code=403, detail="Reload the page and try again.")


async def admin_form(request: Request) -> dict[str, str]:
    """Require the administrator session token before changing system state."""
    session = require_admin(request)
    form = await read_form(request)
    verify_csrf(session.csrf_token, form.get("csrf_token", ""))
    return form


def form_token(request: Request) -> str:
    """Reuse the temporary sign-in form token across browser tabs."""
    token = request.cookies.get(FORM_COOKIE, "")
    return token if len(token) == 43 and token.isascii() else token_urlsafe(32)


def set_session_cookie(response: Response, issued: IssuedSession) -> None:
    """Install a new session and retire the browser's pre-authentication cookies."""
    response.set_cookie(
        SESSION_COOKIE, issued.token, secure=True, httponly=True, samesite="lax"
    )
    for cookie in (FORM_COOKIE, MFA_COOKIE):
        response.delete_cookie(cookie, secure=True, httponly=True, samesite="lax")


def signed_in(issued: IssuedSession, destination: str) -> RedirectResponse:
    """Redirect the browser with its new authenticated session."""
    response = RedirectResponse(destination, status_code=303)
    set_session_cookie(response, issued)
    return response


async def sign_in_form(access: ReaderAccess, request: Request) -> dict[str, str]:
    """Validate browser origin and the temporary cookie for a sign-in form."""
    if not access.same_origin(request):
        raise HTTPException(status_code=403, detail="Open the form and try again.")
    form = await read_form(request)
    verify_csrf(request.cookies.get(FORM_COOKIE, ""), form.get("csrf_token", ""))
    return form


async def read_form(request: Request) -> dict[str, str]:
    """Read a bounded URL-encoded account form without multipart dependencies."""
    if request.headers.get("Content-Type", "").split(";", 1)[0] != (
        "application/x-www-form-urlencoded"
    ):
        raise HTTPException(status_code=415, detail="Open the form and try again.")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > FORM_LIMIT:
            raise HTTPException(status_code=413, detail="The form is too large.")
        body.extend(chunk)
    try:
        values = parse_qs(
            body.decode("utf-8"), keep_blank_values=True, max_num_fields=16
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail="The form is invalid.") from error
    if any(len(items) != 1 for items in values.values()):
        raise HTTPException(status_code=400, detail="The form is invalid.")
    return {name: items[0] for name, items in values.items()}


async def read_json(request: Request, limit: int) -> bytes:
    """Read JSON bytes with a streaming size cap before parsing."""
    if (
        request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise HTTPException(status_code=415, detail="Send the request as JSON.")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(status_code=413, detail="The request is too large.")
        body.extend(chunk)
    return bytes(body)


def safe_next(value: str) -> str:
    """Restrict post-sign-in redirects to local reader paths."""
    if (
        not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or len(value) > 4096
        or any(ord(char) < 32 for char in value)
    ):
        return "/pedigree"
    return value
