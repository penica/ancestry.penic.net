"""WebAuthn credentials and one-time ceremonies for the hosted reader."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from secrets import token_bytes, token_urlsafe
from time import time
from typing import TYPE_CHECKING, Final, Literal
from urllib.parse import urlsplit

from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url, parse_authentication_credential_json
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from reader_accounts import (
    AccountsError,
    _clear_attempts,
    _digest,
    _issue_session,
    _mfa_enabled,
    _read_account,
    _require_same_credentials,
    _require_session_row,
    _required_token_hash,
    client_bucket,
)
from reader_mfa import consume_factor
from reader_settings import settings_path, site_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from reader_accounts import AccountID, Accounts, IssuedSession


CEREMONY_SECONDS: Final = 120
PASSKEY_LIMIT: Final = 10
REQUIRED: Final = UserVerificationRequirement.REQUIRED
Purpose = Literal["register", "login"]


class PasskeyError(AccountsError):
    """A WebAuthn failure that can be shown without exposing credential data."""


@dataclass(frozen=True, slots=True)
class Ceremony:
    token: str
    options: str


@dataclass(frozen=True, slots=True)
class Passkey:
    id: str
    name: str


def passkey_name(name: str) -> str:
    """Accept a short, printable device label."""
    name = name.strip()
    if not 1 <= len(name) <= 80 or not name.isprintable():
        raise PasskeyError("Enter a passkey name between 1 and 80 characters.")
    return name


def save_challenge(
    db: sqlite3.Connection,
    purpose: Purpose,
    binding: bytes,
    challenge: bytes,
    account_id: int | None,
) -> str:
    """Store one pending ceremony per browser binding and action."""
    now = int(time())
    token = token_urlsafe(32)
    db.execute("DELETE FROM reader_passkey_challenges WHERE expires_at <= ?", (now,))
    db.execute(
        """INSERT INTO reader_passkey_challenges VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (purpose, binding) DO UPDATE SET
             token_hash = excluded.token_hash, challenge = excluded.challenge,
             account_id = excluded.account_id, expires_at = excluded.expires_at""",
        (
            _digest(token),
            purpose,
            binding,
            challenge,
            account_id,
            now + CEREMONY_SECONDS,
        ),
    )
    return token


@dataclass(frozen=True, slots=True)
class Passkeys:
    """Manage credentials scoped to the public HTTPS hostname."""

    accounts: Accounts
    origin: str

    @property
    def rp_id(self) -> str:
        """Use the reader hostname as the WebAuthn relying party identifier."""
        return urlsplit(self.origin).hostname or ""

    @contextmanager
    def confirmed(
        self, session: str | None, visitor: str | None, password: str, code: str
    ) -> Iterator[tuple[sqlite3.Connection, sqlite3.Row]]:
        """Check the current password and configured second factor before a change."""
        binding = _required_token_hash(session)
        original, user, client = self.accounts._verify_current_password(
            binding, visitor, password
        )
        with self.accounts._connect(write=True) as db:
            current = _require_same_credentials(db, binding, original)
            if _mfa_enabled(db, current["id"]):
                with self.accounts._mfa_cipher() as cipher:
                    if not consume_factor(db, cipher, current["id"], code, int(time())):
                        raise PasskeyError(
                            "Enter a current authenticator code or a recovery code."
                        )
            yield db, current
            _clear_attempts(db, user, client)

    def listing(self, account_id: AccountID) -> tuple[Passkey, ...]:
        """List only the authenticated account's passkeys."""
        with self.accounts._connect() as db:
            rows = db.execute(
                "SELECT credential_id, name FROM reader_passkeys WHERE account_id = ? ORDER BY rowid",
                (account_id,),
            ).fetchall()
        return tuple(
            Passkey(bytes_to_base64url(row["credential_id"]), row["name"])
            for row in rows
        )

    def registration(
        self, session: str | None, visitor: str | None, password: str, code: str
    ) -> Ceremony:
        """Confirm identity and issue a resident credential challenge."""
        binding = _required_token_hash(session)
        with self.confirmed(session, visitor, password, code) as (db, current):
            account_id = current["id"]
            credentials = db.execute(
                "SELECT credential_id FROM reader_passkeys WHERE account_id = ?",
                (account_id,),
            ).fetchall()
            if len(credentials) >= PASSKEY_LIMIT:
                raise PasskeyError("Remove an existing passkey before adding another.")
            db.execute(
                "INSERT OR IGNORE INTO reader_passkey_users VALUES (?, ?)",
                (account_id, token_bytes(32)),
            )
            handle = db.execute(
                "SELECT user_handle FROM reader_passkey_users WHERE account_id = ?",
                (account_id,),
            ).fetchone()[0]
            options = generate_registration_options(
                rp_id=self.rp_id,
                rp_name="RootsMagic Reader",
                user_id=handle,
                user_name=current["username"],
                user_display_name=current["display_name"] or current["username"],
                timeout=CEREMONY_SECONDS * 1000,
                authenticator_selection=AuthenticatorSelectionCriteria(
                    resident_key=ResidentKeyRequirement.REQUIRED,
                    require_resident_key=True,
                    user_verification=REQUIRED,
                ),
                exclude_credentials=[
                    PublicKeyCredentialDescriptor(id=row["credential_id"])
                    for row in credentials
                ],
            )
            token = save_challenge(
                db, "register", binding, options.challenge, account_id
            )
        return Ceremony(token, options_to_json(options))

    def authentication(self, browser: str, visitor: str | None) -> Ceremony:
        """Issue a discoverable passkey challenge without looking up a username."""
        binding = _required_token_hash(browser)
        security = site_settings(settings_path(self.accounts.path)).security
        self.accounts._reserve_attempt(
            _digest(f"passkey:browser:{browser}"),
            client_bucket("passkey:visitor", visitor),
            limits=(security.address_attempts, 12),
        )
        options = generate_authentication_options(
            rp_id=self.rp_id,
            timeout=CEREMONY_SECONDS * 1000,
            user_verification=REQUIRED,
        )
        with self.accounts._connect(write=True) as db:
            token = save_challenge(db, "login", binding, options.challenge, None)
        return Ceremony(token, options_to_json(options))

    def take_challenge(
        self, purpose: Purpose, token: str, binding: bytes
    ) -> sqlite3.Row:
        """Consume the challenge before checking a browser response."""
        with self.accounts._connect(write=True) as db:
            row = db.execute(
                """DELETE FROM reader_passkey_challenges
                   WHERE purpose = ? AND binding = ? AND token_hash = ? AND expires_at > ?
                   RETURNING challenge, account_id""",
                (purpose, binding, _digest(token), int(time())),
            ).fetchone()
        if row is None:
            raise PasskeyError("This passkey request expired. Try again.")
        return row

    def register(
        self, session: str | None, token: str, credential: str, name: str
    ) -> None:
        """Verify a registration response and save its public key."""
        binding = _required_token_hash(session)
        challenge = self.take_challenge("register", token, binding)
        name = passkey_name(name)
        try:
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=challenge["challenge"],
                expected_rp_id=self.rp_id,
                expected_origin=self.origin,
                require_user_verification=True,
            )
        except (WebAuthnException, ValueError, TypeError, KeyError) as error:
            raise PasskeyError(
                "This passkey could not be registered. Try again."
            ) from error
        try:
            with self.accounts._connect(write=True) as db:
                current = _require_session_row(db, binding)
                account_id = current["id"]
                if account_id != challenge["account_id"]:
                    raise PasskeyError("Start passkey setup again.")
                count = db.execute(
                    "SELECT count(*) FROM reader_passkeys WHERE account_id = ?",
                    (account_id,),
                ).fetchone()[0]
                if count >= PASSKEY_LIMIT:
                    raise PasskeyError(
                        "Remove an existing passkey before adding another."
                    )
                db.execute(
                    "INSERT INTO reader_passkeys VALUES (?, ?, ?, ?, ?)",
                    (
                        verified.credential_id,
                        account_id,
                        verified.credential_public_key,
                        verified.sign_count,
                        name,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise PasskeyError("This passkey is already registered.") from error

    def login(self, browser: str, token: str, credential: str) -> IssuedSession:
        """Verify a passkey assertion, update its counter, and issue a session."""
        challenge = self.take_challenge("login", token, _required_token_hash(browser))
        message = "This passkey could not sign you in. Try again or use your password."
        try:
            assertion = parse_authentication_credential_json(credential)
            with self.accounts._connect(write=True) as db:
                stored = db.execute(
                    """SELECT reader_accounts.*, reader_passkeys.public_key, reader_passkeys.sign_count,
                              reader_passkey_users.user_handle
                       FROM reader_passkeys JOIN reader_passkey_users USING (account_id)
                       JOIN reader_accounts ON reader_accounts.id = reader_passkeys.account_id
                       WHERE credential_id = ? AND NOT disabled""",
                    (assertion.raw_id,),
                ).fetchone()
                if (
                    stored is None
                    or assertion.response.user_handle != stored["user_handle"]
                ):
                    raise PasskeyError(message)
                result = verify_authentication_response(
                    credential=assertion,
                    expected_challenge=challenge["challenge"],
                    expected_rp_id=self.rp_id,
                    expected_origin=self.origin,
                    credential_public_key=stored["public_key"],
                    credential_current_sign_count=stored["sign_count"],
                    require_user_verification=True,
                )
                db.execute(
                    "UPDATE reader_passkeys SET sign_count = ? WHERE credential_id = ?",
                    (result.new_sign_count, assertion.raw_id),
                )
                return _issue_session(db, _read_account(db, stored))
        except (WebAuthnException, ValueError, TypeError, KeyError) as error:
            raise PasskeyError(message) from error

    def manage(
        self,
        session: str | None,
        visitor: str | None,
        password: str,
        code: str,
        credential: str,
        name: str | None,
    ) -> None:
        """Rename or remove a passkey owned by the confirmed account."""
        try:
            credential_id = base64url_to_bytes(credential)
        except ValueError as error:
            raise PasskeyError("Choose a saved passkey.") from error
        with self.confirmed(session, visitor, password, code) as (db, current):
            if name is None:
                cursor = db.execute(
                    "DELETE FROM reader_passkeys WHERE account_id = ? AND credential_id = ?",
                    (current["id"], credential_id),
                )
            else:
                cursor = db.execute(
                    "UPDATE reader_passkeys SET name = ? WHERE account_id = ? AND credential_id = ?",
                    (passkey_name(name), current["id"], credential_id),
                )
            if cursor.rowcount != 1:
                raise PasskeyError("Choose a saved passkey.")
            if name is None:
                db.execute(
                    "DELETE FROM reader_sessions WHERE account_id = ? AND token_hash <> ?",
                    (current["id"], _required_token_hash(session)),
                )
