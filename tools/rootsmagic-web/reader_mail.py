"""Administrator-managed SMTP settings and invitation delivery."""

from __future__ import annotations

import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from ipaddress import ip_address
from threading import BoundedSemaphore
from typing import TYPE_CHECKING, Annotated, Final, Literal

from cryptography.fernet import InvalidToken
from pydantic import Field, ValidationError

from reader_accounts import EMAIL_LABEL, AccountsError, _email, _require_admin
from reader_mfa import MFAError, load_cipher
from store import Record

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping

    from reader_accounts import AccountID, Accounts

SECRET_PREFIX: Final = b"rootsmagic-reader:smtp\x00"
SMTP_WORK: Final = BoundedSemaphore(2)
HOST: Final = re.compile(r"[A-Za-z0-9.:-]{1,253}", re.ASCII)


class MailError(AccountsError):
    """An SMTP setup or delivery error safe to show to an administrator."""


class SMTPSettings(Record):
    """Non-secret SMTP connection settings and the envelope sender."""

    host: str
    port: Annotated[int, Field(ge=1, le=65535)]
    security: Literal["starttls", "tls"]
    username: str = ""
    saved_username: str = ""
    sender: str

    @classmethod
    def parse(cls, form: Mapping[str, str]) -> SMTPSettings:
        """Validate the settings submitted by the administrator."""
        host = form.get("host", "").strip().lower()
        username = form.get("username", "").strip()
        authentication = form.get("authentication")
        match authentication:
            case "enabled":
                authenticate = True
            case "disabled":
                authenticate = False
            case None:
                authenticate = bool(username)
            case _:
                raise MailError("Choose whether to use SMTP authentication.")
        if not HOST.fullmatch(host) or not valid_host(host):
            raise MailError("Enter an SMTP hostname or IP address.")
        if authenticate and (
            not username
            or len(username) > 254
            or not username.isascii()
            or any(ord(character) < 32 for character in username)
        ):
            raise MailError("Enter a valid SMTP username.")
        try:
            return cls.model_validate(
                {
                    "host": host,
                    "port": form.get("port", ""),
                    "security": form.get("security", ""),
                    "username": username if authenticate else "",
                    "saved_username": "" if authenticate else username,
                    "sender": mail_address(form.get("sender", "")),
                }
            )
        except ValidationError as error:
            raise MailError("Choose a valid SMTP port and encryption mode.") from error

    def authentication_target(self) -> tuple[str, int, str, str]:
        """Identify the server and account permitted to reuse a saved password."""
        return self.host, self.port, self.security, self.username or self.saved_username

    @property
    def uses_authentication(self) -> bool:
        """Use a login only when the active username is set."""
        return bool(self.username)


def valid_host(host: str) -> bool:
    """Accept an IP literal or a hostname with valid DNS labels."""
    try:
        ip_address(host)
    except ValueError:
        return all(EMAIL_LABEL.fullmatch(label) for label in host.split("."))
    return True


def mail_address(value: str) -> str:
    """Accept one envelope address with no display name or line breaks."""
    if address := _email(value):
        return address
    raise MailError("Enter an email address.")


def settings_row(db: sqlite3.Connection) -> sqlite3.Row | None:
    """Read the single SMTP configuration."""
    return db.execute(
        "SELECT configuration, password FROM reader_mail_settings WHERE id = 1"
    ).fetchone()


def stored_password(settings: SMTPSettings, saved: sqlite3.Row | None) -> bytes | None:
    """Return a stored credential only for its original connection and username."""
    if saved is not None and saved["password"] is not None:
        previous = SMTPSettings.model_validate_json(saved["configuration"])
        if previous.authentication_target() == settings.authentication_target():
            return saved["password"]
    return None


def smtp_password(
    settings: SMTPSettings, submitted: str, saved: sqlite3.Row | None
) -> str | bytes | None:
    """Select a submitted password or the encrypted password for the same account."""
    if not settings.uses_authentication:
        return None
    if submitted:
        if (
            len(submitted) > 512
            or not submitted.isascii()
            or any(ord(character) < 32 for character in submitted)
        ):
            raise MailError("Enter a valid SMTP password.")
        return submitted
    if credential := stored_password(settings, saved):
        return credential
    raise MailError("Enter the SMTP password for this server and username.")


@dataclass(frozen=True, slots=True)
class Mail:
    """Persist SMTP settings with the private account state."""

    accounts: Accounts

    def configuration(self, actor: AccountID) -> tuple[SMTPSettings | None, bool]:
        """Return the visible settings and whether a password is stored."""
        with self.accounts._connect() as db:
            _require_admin(db, actor)
            row = settings_row(db)
        if row is None:
            return None, False
        return SMTPSettings.model_validate_json(row["configuration"]), row[
            "password"
        ] is not None

    def cipher_password(self, password: str) -> bytes:
        try:
            return load_cipher(self.accounts.mfa_key_file).encrypt(
                SECRET_PREFIX + password.encode("ascii")
            )
        except MFAError as error:
            raise MailError("The server credential key is unavailable.") from error

    def open_password(self, password: bytes) -> str:
        try:
            secret = load_cipher(self.accounts.mfa_key_file).decrypt(password)
            if not secret.startswith(SECRET_PREFIX):
                raise InvalidToken
            return secret[len(SECRET_PREFIX) :].decode("ascii")
        except (MFAError, InvalidToken, UnicodeError) as error:
            raise MailError("The saved SMTP password could not be opened.") from error

    def save(self, actor: AccountID, settings: SMTPSettings, password: str) -> None:
        """Replace SMTP settings while preserving credentials only for the same server."""
        with self.accounts._connect(write=True) as db:
            _require_admin(db, actor)
            saved = settings_row(db)
            credential = (
                smtp_password(settings, password, saved)
                if settings.uses_authentication
                else stored_password(settings, saved)
            )
            if credential is None:
                settings = settings.model_copy(update={"saved_username": ""})
            match credential:
                case str(plain):
                    secret = self.cipher_password(plain)
                case bytes() | None:
                    secret = credential
            db.execute(
                """INSERT INTO reader_mail_settings VALUES (1, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET configuration = excluded.configuration,
                       password = excluded.password""",
                (settings.model_dump_json(), secret),
            )

    def send(
        self,
        actor: AccountID,
        recipient: str,
        subject: str,
        text: str,
        *,
        draft: SMTPSettings | None = None,
        password: str = "",
    ) -> None:
        """Send one email over verified TLS using saved settings or a test draft."""
        recipient = mail_address(recipient)
        with self.accounts._connect() as db:
            _require_admin(db, actor)
            row = settings_row(db)
        if draft is None:
            if row is None:
                raise MailError("Configure the email server first.")
            draft = SMTPSettings.model_validate_json(row["configuration"])
        settings = draft
        credential = smtp_password(settings, password, row)
        match credential:
            case bytes(secret):
                plain = self.open_password(secret)
            case str() | None:
                plain = credential
        message = EmailMessage()
        message["From"] = settings.sender
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(text)
        if not SMTP_WORK.acquire(blocking=False):
            raise MailError("The email server is busy. Try again shortly.")
        try:
            context = ssl.create_default_context()
            smtp = (
                smtplib.SMTP_SSL(
                    settings.host, settings.port, timeout=8, context=context
                )
                if settings.security == "tls"
                else smtplib.SMTP(settings.host, settings.port, timeout=8)
            )
            with smtp:
                if settings.security == "starttls":
                    smtp.starttls(context=context)
                if plain is not None:
                    smtp.login(settings.username, plain)
                smtp.send_message(message)
        except smtplib.SMTPAuthenticationError as error:
            raise MailError("The mail server rejected the SMTP credentials.") from error
        except ssl.SSLCertVerificationError as error:
            raise MailError(
                "The mail server’s TLS certificate could not be verified."
            ) from error
        except (OSError, smtplib.SMTPException) as error:
            raise MailError(
                "The email could not be delivered. Check the email server settings."
            ) from error
        finally:
            SMTP_WORK.release()
