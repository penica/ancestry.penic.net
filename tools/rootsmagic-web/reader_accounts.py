"""Store VM reader accounts and access grants separately from family-tree files."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import re
import secrets
import sqlite3
import unicodedata
from contextlib import closing, contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import BoundedSemaphore
from time import time
from typing import TYPE_CHECKING, Final, Literal, NewType, assert_never

from reader_membership_schema import (
    INVITATIONS,
    MEMBERSHIPS,
    has_external_role,
    migrate_roles,
)
from reader_mfa import (
    MFA_LOGIN_SECONDS,
    MFA_SCHEMA,
    MFA_SETUP_SECONDS,
    MFAError,
    MFASetup,
    consume_factor,
    load_cipher,
    new_secret,
    open_secret,
    replace_recovery_codes,
    seal_secret,
    setup_for,
    valid_step,
)
from reader_settings import settings_path, site_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from cryptography.fernet import Fernet

AccountID = NewType("AccountID", int)
ProfileField = Literal["name", "username", "email", "password"]
SESSION_SECONDS: Final = 12 * 60 * 60
INVITATION_SECONDS: Final = 7 * 24 * 60 * 60
BOOTSTRAP_SECONDS: Final = 30 * 60
ATTEMPT_WINDOW: Final = 15 * 60
USERNAME: Final = re.compile(r"[a-z0-9][a-z0-9_.-]{2,63}")
EMAIL_LOCAL: Final = re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+", re.ASCII | re.I)
EMAIL_LABEL: Final = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.ASCII | re.I
)
TREE_SLUG: Final = re.compile(r"[a-z0-9][a-z0-9-]{0,239}")
TOKEN: Final = re.compile(r"[A-Za-z0-9_-]{43}")
PASSWORD_WORK: Final = BoundedSemaphore(2)
APPLICATION_ID: Final = 1380799041
REQUIRED_TABLES: Final = frozenset(
    {
        "reader_accounts",
        "reader_memberships",
        "reader_person_links",
        "reader_sessions",
        "reader_invitations",
        "reader_login_attempts",
        "reader_mfa_factors",
        "reader_mfa_setups",
        "reader_mfa_challenges",
        "reader_mfa_recovery",
        "reader_mail_settings",
        "reader_passkey_users",
        "reader_passkeys",
        "reader_passkey_challenges",
    }
)
SCHEMA: Final = f"""
CREATE TABLE IF NOT EXISTS reader_mail_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    configuration TEXT NOT NULL, password BLOB
);
CREATE TABLE IF NOT EXISTS reader_accounts (
    id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
    salt BLOB NOT NULL, password_hash BLOB NOT NULL,
    administrator INTEGER NOT NULL CHECK (administrator IN (0, 1)),
    disabled INTEGER NOT NULL DEFAULT 0 CHECK (disabled IN (0, 1)),
    display_name TEXT, email TEXT
);
{MEMBERSHIPS};
CREATE TABLE IF NOT EXISTS reader_person_links (
    account_id INTEGER NOT NULL REFERENCES reader_accounts(id) ON DELETE CASCADE,
    tree_slug TEXT NOT NULL,
    person_id INTEGER NOT NULL CHECK (person_id > 0),
    PRIMARY KEY (account_id, tree_slug)
);
CREATE TABLE IF NOT EXISTS reader_sessions (
    token_hash BLOB PRIMARY KEY NOT NULL,
    account_id INTEGER NOT NULL REFERENCES reader_accounts(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS reader_sessions_account ON reader_sessions(account_id);
{INVITATIONS};
CREATE TABLE IF NOT EXISTS reader_login_attempts (
    bucket BLOB PRIMARY KEY NOT NULL,
    expires_at INTEGER NOT NULL, attempts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS reader_passkey_users (
    account_id INTEGER PRIMARY KEY REFERENCES reader_accounts(id) ON DELETE CASCADE,
    user_handle BLOB NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS reader_passkeys (
    credential_id BLOB PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES reader_passkey_users(account_id) ON DELETE CASCADE,
    public_key BLOB NOT NULL, sign_count INTEGER NOT NULL, name TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reader_passkeys_account ON reader_passkeys(account_id);
CREATE TABLE IF NOT EXISTS reader_passkey_challenges (
    token_hash BLOB PRIMARY KEY, purpose TEXT NOT NULL, binding BLOB NOT NULL,
    challenge BLOB NOT NULL, account_id INTEGER REFERENCES reader_accounts(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL, UNIQUE (purpose, binding)
);
"""


class Role(StrEnum):
    READER = "reader"
    EXTERNAL_READER = "external_reader"
    ADMINISTRATOR = "administrator"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class AccountsError(RuntimeError):
    """An account operation cannot be performed."""


class LoginThrottled(AccountsError):
    """A login must wait for the attempt limit or an available password slot."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__("Too many sign-in attempts. Wait a little and try again.")


@dataclass(frozen=True, slots=True)
class Membership:
    tree_slug: str
    role: Role


@dataclass(frozen=True, slots=True)
class PersonLink:
    tree_slug: str
    person_id: int


@dataclass(frozen=True, slots=True)
class Account:
    id: AccountID
    username: str
    administrator: bool
    disabled: bool
    memberships: tuple[Membership, ...]
    display_name: str | None = None
    email: str | None = None
    person_links: tuple[PersonLink, ...] = ()
    mfa_enabled: bool = False

    def role_for(self, slug: str) -> Role | None:
        """Return the current role for a tree, including global administrators."""
        if self.disabled:
            return None
        if self.administrator:
            return Role.ADMINISTRATOR
        return next(
            (item.role for item in self.memberships if item.tree_slug == slug), None
        )

    def person_for(self, tree_slug: str) -> int | None:
        """Return the account's own person in a tree it may view."""
        if self.role_for(tree_slug) not in (Role.READER, Role.ADMINISTRATOR):
            return None
        return next(
            (
                link.person_id
                for link in self.person_links
                if link.tree_slug == tree_slug
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class Session:
    account: Account
    csrf_token: str
    expires_at: int


@dataclass(frozen=True, slots=True)
class IssuedSession:
    token: str
    session: Session


@dataclass(frozen=True, slots=True)
class LoginChallenge:
    """A short-lived token issued after password verification."""

    token: str


@dataclass(frozen=True, slots=True)
class MFARecovery:
    """A rotated session and the recovery codes shown once to its owner."""

    issued: IssuedSession
    recovery_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Invitation:
    id: int
    username: str
    role: Role
    tree_slug: str | None
    expires_at: int


def _username(value: str) -> str:
    username = value.strip().lower()
    if USERNAME.fullmatch(username) is None or not value.isascii():
        message = "Use 3 to 64 letters, numbers, dots, underscores or hyphens for the username."
        raise AccountsError(message)
    return username


def _display_name(value: str) -> str | None:
    name = value.strip()
    if len(name) > 120 or any(
        unicodedata.category(char) in {"Cc", "Cs"} for char in name
    ):
        message = "Use a name of up to 120 characters on one line."
        raise AccountsError(message)
    return name or None


def _email(value: str) -> str | None:
    address = value.strip()
    if not address:
        return None
    local, separator, domain = address.rpartition("@")
    if (
        not separator
        or len(address) > 254
        or len(local) > 64
        or not EMAIL_LOCAL.fullmatch(local)
        or not all(local.split("."))
        or not all(EMAIL_LABEL.fullmatch(label) for label in domain.split("."))
    ):
        message = "Enter a valid contact email address."
        raise AccountsError(message)
    return address


def _password(value: str) -> bytes:
    if not 12 <= len(value) <= 128:
        message = "Use a password with 12 to 128 characters."
        raise AccountsError(message)
    try:
        return value.encode()
    except UnicodeEncodeError as error:
        message = "Use a valid text password."
        raise AccountsError(message) from error


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def _token_hash(token: str | None) -> bytes | None:
    return _digest(token) if token is not None and TOKEN.fullmatch(token) else None


def _required_token_hash(token: str | None) -> bytes:
    token_hash = _token_hash(token)
    if token_hash is None:
        message = "Sign in again to update account settings."
        raise AccountsError(message)
    return token_hash


def _password_hash(password: bytes, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password, salt=salt, n=2**17, r=8, p=1, maxmem=192 * 1024**2, dklen=32
    )


@contextmanager
def _password_slot() -> Iterator[None]:
    if not PASSWORD_WORK.acquire(blocking=False):
        raise LoginThrottled(2)
    try:
        yield
    finally:
        PASSWORD_WORK.release()


def _new_password(password: str) -> tuple[bytes, bytes]:
    encoded = _password(password)
    salt = secrets.token_bytes(16)
    with _password_slot():
        return salt, _password_hash(encoded, salt)


def _tree_slug(value: str) -> str:
    if TREE_SLUG.fullmatch(value) is None:
        message = "Choose a valid tree."
        raise AccountsError(message)
    return value


def _read_account(db: sqlite3.Connection, row: sqlite3.Row) -> Account:
    memberships = db.execute(
        "SELECT tree_slug, role FROM reader_memberships WHERE account_id = ? ORDER BY tree_slug",
        (row["id"],),
    )
    people = db.execute(
        "SELECT tree_slug, person_id FROM reader_person_links WHERE account_id = ? ORDER BY tree_slug",
        (row["id"],),
    )
    return Account(
        AccountID(row["id"]),
        row["username"],
        bool(row["administrator"]),
        bool(row["disabled"]),
        tuple(Membership(slug, Role(role)) for slug, role in memberships),
        row["display_name"],
        row["email"],
        tuple(PersonLink(slug, person_id) for slug, person_id in people),
        _mfa_enabled(db, row["id"]),
    )


def _session_row(db: sqlite3.Connection, token_hash: bytes) -> sqlite3.Row | None:
    return db.execute(
        """SELECT a.*, s.csrf_token, s.expires_at FROM reader_sessions s
           JOIN reader_accounts a ON a.id = s.account_id
           WHERE s.token_hash = ? AND s.expires_at > ? AND NOT a.disabled""",
        (token_hash, int(time())),
    ).fetchone()


def _require_session_row(db: sqlite3.Connection, token_hash: bytes) -> sqlite3.Row:
    row = _session_row(db, token_hash)
    if row is None:
        message = "Sign in again to update account settings."
        raise AccountsError(message)
    return row


def client_bucket(prefix: str, address: str | None) -> bytes | None:
    """Identify one visitor where the proxy supplies a trustworthy address."""
    return _digest(f"{prefix}:{address}") if address is not None else None


def _clear_attempts(db: sqlite3.Connection, user: bytes, client: bytes | None) -> None:
    db.execute("DELETE FROM reader_login_attempts WHERE bucket = ?", (user,))
    db.execute(
        "UPDATE reader_login_attempts SET attempts = attempts - 1 WHERE bucket = ? AND attempts > 0",
        (client,),
    )


def _account_row(db: sqlite3.Connection, account_id: AccountID) -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM reader_accounts WHERE id = ?", (account_id,)
    ).fetchone()
    if row is None:
        message = "The account is unavailable."
        raise AccountsError(message)
    return row


def _require_admin(db: sqlite3.Connection, account_id: AccountID) -> None:
    row = _account_row(db, account_id)
    if row["disabled"] or not row["administrator"]:
        message = "Administrator access is required."
        raise AccountsError(message)


def _keep_admin(db: sqlite3.Connection, row: sqlite3.Row) -> None:
    if (
        row["administrator"]
        and not row["disabled"]
        and db.execute(
            "SELECT 1 FROM reader_accounts WHERE administrator AND NOT disabled AND id <> ? LIMIT 1",
            (row["id"],),
        ).fetchone()
        is None
    ):
        message = "Keep at least one active administrator."
        raise AccountsError(message)


def _read_invitation(row: sqlite3.Row) -> Invitation:
    return Invitation(
        row["id"],
        row["username"],
        Role(row["role"]),
        row["tree_slug"],
        row["expires_at"],
    )


def _require_empty(db: sqlite3.Connection) -> None:
    if db.execute("SELECT 1 FROM reader_accounts LIMIT 1").fetchone():
        message = "The reader already has an account."
        raise AccountsError(message)


def _issue_invitation(
    db: sqlite3.Connection,
    username: str,
    tree_slug: str | None,
    role: Role,
    expires_at: int,
) -> str:
    token = secrets.token_urlsafe(32)
    db.execute(
        """INSERT INTO reader_invitations(token_hash, username, tree_slug, role, expires_at)
           VALUES (?, ?, ?, ?, ?) ON CONFLICT(username) DO UPDATE SET
           token_hash = excluded.token_hash, tree_slug = excluded.tree_slug,
           role = excluded.role, expires_at = excluded.expires_at""",
        (_digest(token), username, tree_slug, role, expires_at),
    )
    return token


def _issue_session(db: sqlite3.Connection, account: Account) -> IssuedSession:
    now = int(time())
    token = secrets.token_urlsafe(32)
    session = Session(account, secrets.token_urlsafe(32), now + SESSION_SECONDS)
    db.execute("DELETE FROM reader_sessions WHERE expires_at <= ?", (now,))
    db.execute(
        "INSERT INTO reader_sessions VALUES (?, ?, ?, ?)",
        (_digest(token), account.id, session.csrf_token, session.expires_at),
    )
    return IssuedSession(token, session)


def _mfa_enabled(db: sqlite3.Connection, account_id: int) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM reader_mfa_factors WHERE account_id = ?", (account_id,)
        ).fetchone()
        is not None
    )


def _revoke_account_sessions(db: sqlite3.Connection, account_id: int) -> None:
    """Revoke sessions and their setup keys along with pending sign-ins."""
    db.execute("DELETE FROM reader_sessions WHERE account_id = ?", (account_id,))
    db.execute("DELETE FROM reader_mfa_challenges WHERE account_id = ?", (account_id,))


def _issue_login_challenge(
    db: sqlite3.Connection, account: sqlite3.Row
) -> LoginChallenge:
    now = int(time())
    token = secrets.token_urlsafe(32)
    db.execute("DELETE FROM reader_mfa_challenges WHERE expires_at <= ?", (now,))
    db.execute(
        """INSERT INTO reader_mfa_challenges VALUES (?, ?, ?, ?)
           ON CONFLICT(account_id) DO UPDATE SET token_hash = excluded.token_hash,
           password_salt = excluded.password_salt, expires_at = excluded.expires_at""",
        (account["id"], _digest(token), account["salt"], now + MFA_LOGIN_SECONDS),
    )
    return LoginChallenge(token)


def _challenge_account(db: sqlite3.Connection, token_hash: bytes) -> sqlite3.Row | None:
    return db.execute(
        """SELECT a.* FROM reader_mfa_challenges c JOIN reader_accounts a ON a.id = c.account_id
           JOIN reader_mfa_factors f ON f.account_id = a.id
           WHERE c.token_hash = ? AND c.expires_at > ? AND NOT a.disabled
             AND c.password_salt = a.salt""",
        (token_hash, int(time())),
    ).fetchone()


def _setup_row(db: sqlite3.Connection, token_hash: bytes) -> sqlite3.Row | None:
    return db.execute(
        "SELECT secret FROM reader_mfa_setups WHERE session_hash = ? AND expires_at > ?",
        (token_hash, int(time())),
    ).fetchone()


def _require_same_credentials(
    db: sqlite3.Connection, token_hash: bytes, original: sqlite3.Row
) -> sqlite3.Row:
    """Recheck the active session and password after the expensive verification."""
    current = _require_session_row(db, token_hash)
    if any(
        current[key] != original[key]
        for key in ("id", "username", "salt", "password_hash")
    ):
        message = "Sign in again to update account settings."
        raise AccountsError(message)
    return current


@dataclass(frozen=True, slots=True)
class Accounts:
    """Own one private account database and transact all permission changes."""

    path: Path
    mfa_key_file: Path | None = None

    @property
    def mfa_available(self) -> bool:
        """Report whether the hosted reader has a configured encryption key."""
        return self.mfa_key_file is not None

    @contextmanager
    def _connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        uri = f"{self.path.resolve().as_uri()}?mode=rw"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db

    def check_schema(self) -> None:
        """Open the existing account store and verify it is ready to serve."""
        with self._connect() as db:
            app_id = db.execute("PRAGMA application_id").fetchone()[0]
            tables = {
                row["name"]
                for row in db.execute(
                    "SELECT name FROM sqlite_schema WHERE type = 'table'"
                )
            }
            roles_ready = all(
                has_external_role(db, table)
                for table in ("reader_memberships", "reader_invitations")
            )
        if app_id != APPLICATION_ID:
            message = "ROOTSMAGIC_ACCOUNTS_DB must point to a reader accounts database."
            raise AccountsError(message)
        if missing := REQUIRED_TABLES - tables:
            message = f"Run the reader account migration. Missing tables: {', '.join(sorted(missing))}."
            raise AccountsError(message)
        if not roles_ready:
            raise AccountsError(
                "Run the reader account migration for External Reader access."
            )

    def initialize(self) -> None:
        """Create or migrate the private sidecar, refusing other databases."""
        if self.path.is_symlink() or self.path.suffix.lower() == ".rmtree":
            message = "Choose a separate reader accounts database."
            raise AccountsError(message)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        with self._connect(write=True) as db:
            app_id = db.execute("PRAGMA application_id").fetchone()[0]
            has_schema = db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone()
            if app_id != APPLICATION_ID and (app_id or has_schema):
                message = "Choose a separate reader accounts database."
                raise AccountsError(message)
            for statement in (SCHEMA + MFA_SCHEMA).split(";"):
                if statement.strip():
                    db.execute(statement)
            migrate_roles(db)
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(reader_accounts)")
            }
            if "display_name" not in columns:
                db.execute("ALTER TABLE reader_accounts ADD COLUMN display_name TEXT")
            if "email" not in columns:
                db.execute("ALTER TABLE reader_accounts ADD COLUMN email TEXT")
            db.execute(f"PRAGMA application_id = {APPLICATION_ID}")  # noqa: S608

    def bootstrap_admin(self, username: str, password: str) -> Account:
        """Create the first administrator using credentials entered by the operator."""
        username = _username(username)
        salt, password_hash = _new_password(password)
        with self._connect(write=True) as db:
            _require_empty(db)
            db.execute("DELETE FROM reader_invitations")
            row = db.execute(
                "INSERT INTO reader_accounts(username, salt, password_hash, administrator) VALUES (?, ?, ?, 1) RETURNING *",
                (username, salt, password_hash),
            ).fetchone()
            return _read_account(db, row)

    def bootstrap_invitation(self, username: str) -> str:
        """Create the first administrator's short-lived enrollment link."""
        username = _username(username)
        with self._connect(write=True) as db:
            _require_empty(db)
            now = int(time())
            db.execute("DELETE FROM reader_invitations WHERE expires_at <= ?", (now,))
            if db.execute("SELECT 1 FROM reader_invitations LIMIT 1").fetchone():
                message = (
                    "A setup invitation is already active. It expires after 30 minutes."
                )
                raise AccountsError(message)
            return _issue_invitation(
                db, username, None, Role.ADMINISTRATOR, now + BOOTSTRAP_SECONDS
            )

    def reset_password(self, username: str, password: str) -> Account:
        """Replace one existing account's password and revoke all its sessions."""
        username = _username(username)
        salt, password_hash = _new_password(password)
        with self._connect(write=True) as db:
            if db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                message = "Choose the reader accounts database."
                raise AccountsError(message)
            account = db.execute(
                """UPDATE reader_accounts SET salt = ?, password_hash = ?
                   WHERE username = ? RETURNING *""",
                (salt, password_hash, username),
            ).fetchone()
            if account is None:
                message = "The account is unavailable."
                raise AccountsError(message)
            _revoke_account_sessions(db, account["id"])
            db.execute(
                "DELETE FROM reader_login_attempts WHERE bucket = ?",
                (_digest(f"user:{username}"),),
            )
            return _read_account(db, account)

    def reset_mfa(self, username: str) -> Account:
        """Remove a lost factor through the trusted VM operator workflow."""
        username = _username(username)
        with self._connect(write=True) as db:
            if db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                message = "Choose the reader accounts database."
                raise AccountsError(message)
            account = db.execute(
                "SELECT * FROM reader_accounts WHERE username = ?", (username,)
            ).fetchone()
            if account is None:
                message = "The account is unavailable."
                raise AccountsError(message)
            account_id = account["id"]
            db.execute(
                "DELETE FROM reader_mfa_factors WHERE account_id = ?", (account_id,)
            )
            _revoke_account_sessions(db, account_id)
            db.execute(
                "DELETE FROM reader_login_attempts WHERE bucket = ?",
                (_digest(f"mfa:user:{account_id}"),),
            )
            return _read_account(db, account)

    def _reserve_attempt(
        self,
        user: bytes,
        client: bytes | None,
        *,
        limits: tuple[int, int] | None = None,
    ) -> None:
        if limits is None:
            security = site_settings(settings_path(self.path)).security
            limits = (security.address_attempts, security.account_attempts)
        now = int(time())
        with self._connect(write=True) as db:
            db.execute(
                "DELETE FROM reader_login_attempts WHERE expires_at <= ?", (now,)
            )
            for bucket, limit in zip((client, user), limits, strict=True):
                if bucket is None:
                    continue
                row = db.execute(
                    "SELECT expires_at, attempts FROM reader_login_attempts WHERE bucket = ?",
                    (bucket,),
                ).fetchone()
                if row is not None and row["attempts"] >= limit:
                    raise LoginThrottled(row["expires_at"] - now)
                db.execute(
                    """INSERT INTO reader_login_attempts VALUES (?, ?, 1)
                       ON CONFLICT(bucket) DO UPDATE SET attempts = attempts + 1""",
                    (bucket, now + ATTEMPT_WINDOW),
                )

    def login(
        self, username: str, password: str, client_key: str | None
    ) -> IssuedSession | LoginChallenge | None:
        """Verify credentials and apply rate limits even to unknown usernames."""
        try:
            username = _username(username)
        except AccountsError:
            username = ""
        try:
            password_bytes = _password(password)
        except AccountsError:
            password_bytes = b""
        user, client = _digest(f"user:{username}"), client_bucket("client", client_key)
        with _password_slot():
            self._reserve_attempt(user, client)
            with self._connect() as db:
                row = db.execute(
                    "SELECT * FROM reader_accounts WHERE username = ?",
                    (username,),
                ).fetchone()
            salt = row["salt"] if row is not None else bytes(16)
            expected = row["password_hash"] if row is not None else bytes(32)
            valid = secrets.compare_digest(
                _password_hash(password_bytes, salt), expected
            )
        if row is None or row["disabled"] or not valid:
            return None
        with self._connect(write=True) as db:
            current = _account_row(db, AccountID(row["id"]))
            if (
                current["disabled"]
                or current["username"] != username
                or current["password_hash"] != expected
            ):
                return None
            _clear_attempts(db, user, client)
            if _mfa_enabled(db, current["id"]):
                return _issue_login_challenge(db, current)
            return _issue_session(db, _read_account(db, current))

    @contextmanager
    def _mfa_cipher(self) -> Iterator[Fernet]:
        try:
            yield load_cipher(self.mfa_key_file)
        except MFAError as error:
            raise AccountsError(str(error)) from error

    def _reserve_mfa_attempt(
        self, account_id: int, client_key: str | None
    ) -> tuple[bytes, bytes | None]:
        user = _digest(f"mfa:user:{account_id}")
        client = client_bucket("mfa:client", client_key)
        self._reserve_attempt(user, client)
        return user, client

    def complete_mfa(
        self, challenge_token: str | None, client_key: str | None, code: str
    ) -> IssuedSession | None:
        """Finish a password-verified sign-in with a single-use second factor."""
        token_hash = _token_hash(challenge_token)
        if token_hash is None:
            return None
        with self._connect() as db:
            account = _challenge_account(db, token_hash)
        if account is None:
            return None
        account_id = account["id"]
        user, client = self._reserve_mfa_attempt(account_id, client_key)
        with self._mfa_cipher() as cipher, self._connect(write=True) as db:
            current = _challenge_account(db, token_hash)
            if current is None or not consume_factor(
                db, cipher, account_id, code, int(time())
            ):
                return None
            db.execute(
                "DELETE FROM reader_mfa_challenges WHERE token_hash = ?", (token_hash,)
            )
            _clear_attempts(db, user, client)
            return _issue_session(db, _read_account(db, current))

    def _verify_current_password(
        self, token_hash: bytes, client_key: str | None, password: str
    ) -> tuple[sqlite3.Row, bytes, bytes | None]:
        with self._connect() as db:
            original = _require_session_row(db, token_hash)
        user = _digest(f"user:{original['username']}")
        client = client_bucket("client", client_key)
        with _password_slot():
            self._reserve_attempt(user, client)
            try:
                entered = _password(password)
            except AccountsError:
                entered = b""
            if not secrets.compare_digest(
                _password_hash(entered, original["salt"]), original["password_hash"]
            ):
                message = "The current password is incorrect."
                raise AccountsError(message)
        return original, user, client

    def begin_mfa(
        self, session_token: str | None, client_key: str | None, current_password: str
    ) -> MFASetup:
        """Start a short-lived authenticator setup bound to the verified session."""
        token_hash = _required_token_hash(session_token)
        original, user, client = self._verify_current_password(
            token_hash, client_key, current_password
        )
        with self._mfa_cipher() as cipher, self._connect(write=True) as db:
            current = _require_same_credentials(db, token_hash, original)
            account_id = current["id"]
            if _mfa_enabled(db, account_id):
                message = "Two-step verification is already active."
                raise AccountsError(message)
            secret = new_secret()
            now = int(time())
            db.execute("DELETE FROM reader_mfa_setups WHERE expires_at <= ?", (now,))
            db.execute(
                """INSERT INTO reader_mfa_setups VALUES (?, ?, ?)
                   ON CONFLICT(session_hash) DO UPDATE SET secret = excluded.secret,
                   expires_at = excluded.expires_at""",
                (
                    token_hash,
                    seal_secret(cipher, account_id, secret),
                    now + MFA_SETUP_SECONDS,
                ),
            )
            _clear_attempts(db, user, client)
            return setup_for(secret, current["username"])

    def read_mfa_setup(self, session_token: str | None) -> MFASetup | None:
        """Read a pending setup only from the active session that started it."""
        token_hash = _token_hash(session_token)
        if token_hash is None:
            return None
        with self._connect() as db:
            current = _session_row(db, token_hash)
            pending = _setup_row(db, token_hash)
        if current is None or pending is None:
            return None
        with self._mfa_cipher() as cipher:
            secret = open_secret(cipher, current["id"], pending["secret"])
            return setup_for(secret, current["username"])

    def confirm_mfa(
        self, session_token: str | None, client_key: str | None, code: str
    ) -> MFARecovery:
        """Enable verification after a valid app code and rotate existing sessions."""
        token_hash = _required_token_hash(session_token)
        with self._connect() as db:
            original = _require_session_row(db, token_hash)
        account_id = original["id"]
        user, client = self._reserve_mfa_attempt(account_id, client_key)
        with self._mfa_cipher() as cipher, self._connect(write=True) as db:
            current = _require_same_credentials(db, token_hash, original)
            pending = _setup_row(db, token_hash)
            if pending is None or _mfa_enabled(db, account_id):
                message = "This authenticator setup has expired. Start setup again."
                raise AccountsError(message)
            secret = open_secret(cipher, account_id, pending["secret"])
            step = valid_step(secret, code, int(time()), -1)
            if step is None:
                message = "Enter a current code from your authenticator app."
                raise AccountsError(message)
            db.execute(
                "INSERT INTO reader_mfa_factors VALUES (?, ?, ?)",
                (account_id, pending["secret"], step),
            )
            recovery_codes = replace_recovery_codes(db, account_id)
            _clear_attempts(db, user, client)
            _revoke_account_sessions(db, account_id)
            return MFARecovery(
                _issue_session(db, _read_account(db, current)), recovery_codes
            )

    def _change_mfa(
        self,
        session_token: str | None,
        client_key: str | None,
        current_password: str,
        code: str,
        *,
        disable: bool,
    ) -> MFARecovery:
        token_hash = _required_token_hash(session_token)
        original, user, client = self._verify_current_password(
            token_hash, client_key, current_password
        )
        account_id = original["id"]
        mfa_user, mfa_client = self._reserve_mfa_attempt(account_id, client_key)
        with self._mfa_cipher() as cipher, self._connect(write=True) as db:
            current = _require_same_credentials(db, token_hash, original)
            if not consume_factor(db, cipher, account_id, code, int(time())):
                message = (
                    "Enter a current authenticator code or an unused recovery code."
                )
                raise AccountsError(message)
            if disable:
                db.execute(
                    "DELETE FROM reader_mfa_factors WHERE account_id = ?", (account_id,)
                )
                recovery_codes = ()
            else:
                recovery_codes = replace_recovery_codes(db, account_id)
            _clear_attempts(db, user, client)
            _clear_attempts(db, mfa_user, mfa_client)
            _revoke_account_sessions(db, account_id)
            return MFARecovery(
                _issue_session(db, _read_account(db, current)), recovery_codes
            )

    def disable_mfa(
        self,
        session_token: str | None,
        client_key: str | None,
        current_password: str,
        code: str,
    ) -> IssuedSession:
        """Remove the authenticator after password and second-factor verification."""
        return self._change_mfa(
            session_token, client_key, current_password, code, disable=True
        ).issued

    def regenerate_recovery_codes(
        self,
        session_token: str | None,
        client_key: str | None,
        current_password: str,
        code: str,
    ) -> MFARecovery:
        """Replace unused recovery codes after password and second-factor verification."""
        return self._change_mfa(
            session_token, client_key, current_password, code, disable=False
        )

    def session(self, token: str | None) -> Session | None:
        """Look up an unexpired session and load its current account permissions."""
        token_hash = _token_hash(token)
        if token_hash is None:
            return None
        with self._connect() as db:
            row = _session_row(db, token_hash)
            if row is None:
                return None
            return Session(_read_account(db, row), row["csrf_token"], row["expires_at"])

    def update_profile(
        self,
        token: str | None,
        client_key: str | None,
        field: ProfileField,
        value: str,
        current_password: str = "",
    ) -> IssuedSession | None:
        """Update the signed-in account and rotate sessions for sensitive changes."""
        token_hash = _required_token_hash(token)
        match field:
            case "name":
                name = _display_name(value)
                with self._connect(write=True) as db:
                    account = _require_session_row(db, token_hash)
                    db.execute(
                        "UPDATE reader_accounts SET display_name = ? WHERE id = ?",
                        (name, account["id"]),
                    )
                return None
            case "username" | "email" | "password":
                return self._update_sensitive_profile(
                    token_hash, client_key, field, value, current_password
                )
            case unreachable:
                assert_never(unreachable)

    def _update_sensitive_profile(
        self,
        token_hash: bytes,
        client_key: str | None,
        field: Literal["username", "email", "password"],
        value: str,
        current_password: str,
    ) -> IssuedSession:
        original, user, client = self._verify_current_password(
            token_hash, client_key, current_password
        )
        match field:
            case "username":
                username = _username(value)
                statement = "UPDATE reader_accounts SET username = ? WHERE id = ?"
                parameters = (username,)
            case "email":
                username = None
                statement = "UPDATE reader_accounts SET email = ? WHERE id = ?"
                parameters = (_email(value),)
            case "password":
                username = None
                statement = "UPDATE reader_accounts SET salt = ?, password_hash = ? WHERE id = ?"
                parameters = _new_password(value)
            case unreachable:
                assert_never(unreachable)
        with self._connect(write=True) as db:
            current = _require_same_credentials(db, token_hash, original)
            account_id = AccountID(current["id"])
            if (
                username is not None
                and db.execute(
                    """SELECT 1 FROM reader_accounts WHERE username = ? AND id <> ?
                   UNION ALL
                   SELECT 1 FROM reader_invitations WHERE username = ? AND expires_at > ?
                   LIMIT 1""",
                    (username, account_id, username, int(time())),
                ).fetchone()
            ):
                message = (
                    "That username is already in use or reserved by an invitation."
                )
                raise AccountsError(message)
            db.execute(statement, (*parameters, account_id))
            _revoke_account_sessions(db, account_id)
            _clear_attempts(db, user, client)
            return _issue_session(db, _read_account(db, _account_row(db, account_id)))

    def set_person_link(
        self, token: str | None, tree_slug: str, person_id: int | None
    ) -> None:
        """Set the signed-in account's own person in an authorized tree."""
        slug = _tree_slug(tree_slug)
        if person_id is not None and (
            type(person_id) is not int or not 0 < person_id < 2**63
        ):
            message = "Choose a valid RootsMagic person."
            raise AccountsError(message)
        token_hash = _token_hash(token)
        if token_hash is None:
            message = "Sign in again to update account settings."
            raise AccountsError(message)
        with self._connect(write=True) as db:
            account = _read_account(db, _require_session_row(db, token_hash))
            if account.role_for(slug) not in (Role.READER, Role.ADMINISTRATOR):
                message = "This account does not have access to the selected tree."
                raise AccountsError(message)
            if person_id is None:
                db.execute(
                    "DELETE FROM reader_person_links WHERE account_id = ? AND tree_slug = ?",
                    (account.id, slug),
                )
            else:
                db.execute(
                    """INSERT INTO reader_person_links VALUES (?, ?, ?)
                       ON CONFLICT(account_id, tree_slug) DO UPDATE SET person_id = excluded.person_id""",
                    (account.id, slug, person_id),
                )

    def logout(self, token: str | None) -> None:
        """Revoke the session associated with the browser token."""
        token_hash = _token_hash(token)
        if token_hash is not None:
            with self._connect(write=True) as db:
                db.execute(
                    "DELETE FROM reader_sessions WHERE token_hash = ?", (token_hash,)
                )

    def invite(
        self, actor_id: AccountID, username: str, tree_slug: str | None, role: Role
    ) -> str:
        """Issue an expiring token for a new tree member or administrator."""
        username = _username(username)
        if role == Role.ADMINISTRATOR:
            if tree_slug is not None:
                message = "Administrators have access to all trees."
                raise AccountsError(message)
        elif tree_slug is None:
            message = "Choose a tree for the invitation."
            raise AccountsError(message)
        else:
            _tree_slug(tree_slug)
        now = int(time())
        with self._connect(write=True) as db:
            _require_admin(db, actor_id)
            if db.execute(
                "SELECT 1 FROM reader_accounts WHERE username = ?", (username,)
            ).fetchone():
                message = "That username already belongs to an account."
                raise AccountsError(message)
            db.execute("DELETE FROM reader_invitations WHERE expires_at <= ?", (now,))
            return _issue_invitation(
                db, username, tree_slug, role, now + INVITATION_SECONDS
            )

    def invitation(self, token: str) -> Invitation | None:
        """Read pending invitation details for a valid activation link."""
        token_hash = _token_hash(token)
        if token_hash is None:
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM reader_invitations WHERE token_hash = ? AND expires_at > ?",
                (token_hash, int(time())),
            ).fetchone()
            return _read_invitation(row) if row is not None else None

    def redeem(self, token: str, password: str) -> IssuedSession:
        """Set the invited user's password and consume the link in one transaction."""
        if self.invitation(token) is None:
            message = "The invitation is invalid or has expired."
            raise AccountsError(message)
        salt, password_hash = _new_password(password)
        with self._connect(write=True) as db:
            row = db.execute(
                "DELETE FROM reader_invitations WHERE token_hash = ? AND expires_at > ? RETURNING *",
                (_digest(token), int(time())),
            ).fetchone()
            if row is None:
                message = "The invitation is invalid or has expired."
                raise AccountsError(message)
            invitation = _read_invitation(row)
            account = db.execute(
                "INSERT INTO reader_accounts(username, salt, password_hash, administrator) VALUES (?, ?, ?, ?) RETURNING *",
                (
                    invitation.username,
                    salt,
                    password_hash,
                    invitation.role == Role.ADMINISTRATOR,
                ),
            ).fetchone()
            if invitation.tree_slug is not None:
                db.execute(
                    "INSERT INTO reader_memberships VALUES (?, ?, ?)",
                    (account["id"], invitation.tree_slug, invitation.role),
                )
            return _issue_session(db, _read_account(db, account))

    def list_accounts(self, actor_id: AccountID) -> tuple[Account, ...]:
        """Return account identities and memberships for an administrator."""
        with self._connect() as db:
            _require_admin(db, actor_id)
            rows = db.execute("SELECT * FROM reader_accounts ORDER BY username")
            return tuple(_read_account(db, row) for row in rows)

    def list_invitations(self, actor_id: AccountID) -> tuple[Invitation, ...]:
        """Return unexpired invitations without their activation credentials."""
        with self._connect() as db:
            _require_admin(db, actor_id)
            rows = db.execute(
                "SELECT * FROM reader_invitations WHERE expires_at > ? ORDER BY username",
                (int(time()),),
            )
            return tuple(_read_invitation(row) for row in rows)

    def revoke_invitation(self, actor_id: AccountID, invitation_id: int) -> None:
        """Invalidate an invitation before it is redeemed."""
        with self._connect(write=True) as db:
            _require_admin(db, actor_id)
            db.execute("DELETE FROM reader_invitations WHERE id = ?", (invitation_id,))

    def set_membership(
        self,
        actor_id: AccountID,
        account_id: AccountID,
        tree_slug: str,
        role: Role | None,
    ) -> None:
        """Assign, change or remove access to one tree."""
        tree_slug = _tree_slug(tree_slug)
        if role == Role.ADMINISTRATOR:
            message = "Administrator access is assigned to the account."
            raise AccountsError(message)
        with self._connect(write=True) as db:
            _require_admin(db, actor_id)
            _account_row(db, account_id)
            if role is None:
                db.execute(
                    "DELETE FROM reader_memberships WHERE account_id = ? AND tree_slug = ?",
                    (account_id, tree_slug),
                )
            else:
                db.execute(
                    """INSERT INTO reader_memberships VALUES (?, ?, ?)
                       ON CONFLICT(account_id, tree_slug) DO UPDATE SET role = excluded.role""",
                    (account_id, tree_slug, role),
                )

    def set_disabled(
        self, actor_id: AccountID, account_id: AccountID, disabled: bool
    ) -> None:
        """Enable or disable an account and revoke sessions when disabled."""
        with self._connect(write=True) as db:
            _require_admin(db, actor_id)
            account = _account_row(db, account_id)
            if disabled:
                _keep_admin(db, account)
                _revoke_account_sessions(db, account_id)
            db.execute(
                "UPDATE reader_accounts SET disabled = ? WHERE id = ?",
                (disabled, account_id),
            )

    def set_administrator(
        self, actor_id: AccountID, account_id: AccountID, administrator: bool
    ) -> None:
        """Change global administrator access and rotate existing sessions."""
        with self._connect(write=True) as db:
            _require_admin(db, actor_id)
            account = _account_row(db, account_id)
            if not administrator:
                _keep_admin(db, account)
            if administrator != bool(account["administrator"]):
                _revoke_account_sessions(db, account_id)
                db.execute(
                    "UPDATE reader_accounts SET administrator = ? WHERE id = ?",
                    (administrator, account_id),
                )


def main() -> None:
    """Set up the reader or recover an account from the terminal."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "bootstrap",
            "bootstrap-invite",
            "reset-password",
            "reset-mfa",
            "migrate",
        ],
    )
    parser.add_argument("database", type=Path)
    parser.add_argument("username", nargs="?")
    options = parser.parse_args()
    accounts = Accounts(options.database)
    try:
        if options.command not in {"reset-password", "reset-mfa"}:
            accounts.initialize()
        if options.command == "migrate":
            print("Reader accounts database is current.")
            return
        if options.username is None:
            parser.error("A username is required.")
        if options.command == "bootstrap-invite":
            print(accounts.bootstrap_invitation(options.username))
            return
        if options.command == "reset-mfa":
            account = accounts.reset_mfa(options.username)
            print(
                f"Two-step verification reset for {account.username}. The account must sign in again."
            )
            return
        password = getpass.getpass("New password: ")
        if password != getpass.getpass("Repeat password: "):
            parser.error("The passwords differ.")
        if options.command == "reset-password":
            account = accounts.reset_password(options.username, password)
            print(
                f"Password reset for {account.username}. The account must sign in again."
            )
            return
        account = accounts.bootstrap_admin(options.username, password)
    except (AccountsError, OSError, sqlite3.Error) as error:
        parser.error(str(error))
    print(f"Administrator {account.username} is ready to sign in.")


if __name__ == "__main__":
    main()
