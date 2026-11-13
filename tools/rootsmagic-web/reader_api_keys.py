"""Lifecycle and authentication for read-only integration credentials."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from secrets import token_urlsafe
from sqlite3 import IntegrityError
from threading import Lock
from time import monotonic, time
from typing import TYPE_CHECKING, Final, Literal, NewType

from reader_accounts import AccountsError, _require_admin

if TYPE_CHECKING:
    from sqlite3 import Row

    from reader_accounts import AccountID, Accounts

KeyID = NewType("KeyID", int)
KeyScope = Literal["deceased", "family"]
KeyLifetime = Literal[30, 90, 365]
LOOKUP_PATH: Final = "/api/v1/integrations/people"
KEY_PATTERN: Final = re.compile(r"rmr_[A-Za-z0-9_-]{43}")
SECONDS_PER_DAY: Final = 86400
REQUEST_LIMIT: Final = 120
WINDOW_SECONDS: Final = 60
USAGE_LOCK: Final = Lock()
USAGE: Final[dict[KeyID, int]] = {}
usage_window = 0


@dataclass(frozen=True, slots=True)
class KeyInfo:
    id: KeyID
    label: str
    tree_slug: str
    scope: KeyScope
    prefix: str
    created_at: int
    expires_at: int
    lifetime_days: KeyLifetime
    last_used_at: int | None
    revoked_at: int | None
    version: int


@dataclass(frozen=True, slots=True)
class IssuedKey:
    info: KeyInfo
    secret: str


class KeyConflict(AccountsError):
    """The key was changed by another request or revoked."""


def key_info(row: Row) -> KeyInfo:
    return KeyInfo(
        id=KeyID(row["id"]),
        label=row["label"],
        tree_slug=row["tree_slug"],
        scope=row["scope"],
        prefix=row["prefix"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        lifetime_days=row["lifetime_days"],
        last_used_at=row["last_used_at"],
        revoked_at=row["revoked_at"],
        version=row["version"],
    )


def key_digest(secret: str) -> bytes:
    return sha256(secret.encode("ascii")).digest()


def retry_after(key_id: KeyID) -> int:
    """Allow up to 120 lookup requests per key in each one-minute window."""
    global usage_window
    now = int(monotonic())
    window, elapsed = divmod(now, WINDOW_SECONDS)
    with USAGE_LOCK:
        if window != usage_window:
            USAGE.clear()
            usage_window = window
        count = USAGE.get(key_id, 0)
        if count >= REQUEST_LIMIT:
            return WINDOW_SECONDS - elapsed
        USAGE[key_id] = count + 1
    return 0


@dataclass(frozen=True, slots=True)
class ApiKeys:
    accounts: Accounts

    def list(self, actor: AccountID) -> tuple[KeyInfo, ...]:
        with self.accounts._connect() as db:
            _require_admin(db, actor)
            rows = db.execute(
                "SELECT * FROM reader_api_keys ORDER BY revoked_at IS NOT NULL, id DESC"
            )
            return tuple(map(key_info, rows))

    def create(
        self,
        actor: AccountID,
        label: str,
        tree_slug: str,
        scope: KeyScope,
        days: KeyLifetime,
    ) -> IssuedKey:
        secret = "rmr_" + token_urlsafe(32)
        now = int(time())
        with self.accounts._connect(write=True) as db:
            _require_admin(db, actor)
            try:
                row = db.execute(
                    """INSERT INTO reader_api_keys
                       (label, tree_slug, scope, token_hash, prefix, created_at, expires_at, lifetime_days)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING *""",
                    (
                        label,
                        tree_slug,
                        scope,
                        key_digest(secret),
                        secret[:12],
                        now,
                        now + days * SECONDS_PER_DAY,
                        days,
                    ),
                ).fetchone()
            except IntegrityError as error:
                raise AccountsError(
                    "Choose a different name for this API key."
                ) from error
            return IssuedKey(key_info(row), secret)

    def rename(self, actor: AccountID, key_id: KeyID, version: int, label: str) -> None:
        with self.accounts._connect(write=True) as db:
            _require_admin(db, actor)
            try:
                result = db.execute(
                    """UPDATE reader_api_keys SET label = ?, version = version + 1
                       WHERE id = ? AND version = ? AND revoked_at IS NULL""",
                    (label, key_id, version),
                )
            except IntegrityError as error:
                raise AccountsError(
                    "Choose a different name for this API key."
                ) from error
            if result.rowcount != 1:
                raise KeyConflict(
                    "This API key changed. Review the current key and try again."
                )

    def rotate(self, actor: AccountID, key_id: KeyID, version: int) -> IssuedKey:
        secret = "rmr_" + token_urlsafe(32)
        now = int(time())
        with self.accounts._connect(write=True) as db:
            _require_admin(db, actor)
            row = db.execute(
                """UPDATE reader_api_keys SET token_hash = ?, prefix = ?,
                       expires_at = ? + lifetime_days * ?, last_used_at = NULL, version = version + 1
                   WHERE id = ? AND version = ? AND revoked_at IS NULL RETURNING *""",
                (
                    key_digest(secret),
                    secret[:12],
                    now,
                    SECONDS_PER_DAY,
                    key_id,
                    version,
                ),
            ).fetchone()
            if row is None:
                raise KeyConflict(
                    "This API key changed. Review the current key and try again."
                )
            return IssuedKey(key_info(row), secret)

    def revoke(self, actor: AccountID, key_id: KeyID, version: int) -> None:
        with self.accounts._connect(write=True) as db:
            _require_admin(db, actor)
            result = db.execute(
                """UPDATE reader_api_keys SET revoked_at = ?, version = version + 1
                   WHERE id = ? AND version = ? AND revoked_at IS NULL""",
                (int(time()), key_id, version),
            )
            if result.rowcount != 1:
                raise KeyConflict(
                    "This API key changed. Review the current key and try again."
                )

    def authenticate(self, secret: str) -> KeyInfo | None:
        if not KEY_PATTERN.fullmatch(secret):
            return None
        now = int(time())
        with self.accounts._connect() as db:
            row = db.execute(
                """SELECT * FROM reader_api_keys WHERE token_hash = ?
                   AND revoked_at IS NULL AND expires_at > ?""",
                (key_digest(secret), now),
            ).fetchone()
            return key_info(row) if row is not None else None

    def record_use(self, key: KeyInfo) -> None:
        """Keep a minute-resolution usage timestamp without writing on every lookup."""
        now = int(time())
        cutoff = now - WINDOW_SECONDS
        if key.last_used_at is not None and key.last_used_at > cutoff:
            return
        with self.accounts._connect(write=True) as db:
            db.execute(
                """UPDATE reader_api_keys SET last_used_at = ?
                   WHERE id = ? AND prefix = ? AND revoked_at IS NULL AND expires_at > ?
                   AND (last_used_at IS NULL OR last_used_at <= ?)""",
                (now, key.id, key.prefix, now, cutoff),
            )
