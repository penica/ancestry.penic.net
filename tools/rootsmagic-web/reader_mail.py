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
    sender: str

    @classmethod
    def parse(
        cls, host: str, port: str, security: str, username: str, sender: str
    ) -> SMTPSettings:
        """Validate the settings submitted by the administrator."""
        host, username = host.strip().lower(), username.strip()
        if not HOST.fullmatch(host) or not valid_host(host):
            raise MailError("Enter an SMTP hostname or IP address.")
        if (
            len(username) > 254
            or not username.isascii()
            or any(ord(character) < 32 for character in username)
        ):
            raise MailError("Enter a valid SMTP username.")
        try:
            return cls.model_validate(
                {
                    "host": host,
                    "port": port,
                    "security": security,
                    "username": username,
                    "sender": mail_address(sender),
                }
            )
        except ValidationError as error:
            raise MailError("Choose a valid SMTP port and encryption mode.") from error

    def authentication_target(self) -> tuple[str, int, str, str]:
        """Identify the server and account permitted to reuse a saved password."""
        return self.host, self.port, self.security, self.username


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
        if (
            len(password) > 512
            or not password.isascii()
            or any(ord(character) < 32 for character in password)
        ):
            raise MailError("Enter a valid SMTP password.")
        if password and not settings.username:
            raise MailError("Enter an SMTP username when saving a password.")
        with self.accounts._connect(write=True) as db:
            _require_admin(db, actor)
            existing = settings_row(db)
            secret = None
            if password:
                secret = self.cipher_password(password)
            elif (
                settings.username
                and existing is not None
                and existing["password"] is not None
            ):
                previous = SMTPSettings.model_validate_json(existing["configuration"])
                if previous.authentication_target() == settings.authentication_target():
                    secret = existing["password"]
            if settings.username and secret is None:
                raise MailError("Enter the SMTP password for this server and username.")
            db.execute(
                """INSERT INTO reader_mail_settings VALUES (1, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET configuration = excluded.configuration,
                       password = excluded.password""",
                (settings.model_dump_json(), secret),
            )

    def send(self, actor: AccountID, recipient: str, subject: str, text: str) -> None:
        """Send a single email over verified TLS with a bounded connection timeout."""
        recipient = mail_address(recipient)
        with self.accounts._connect() as db:
            _require_admin(db, actor)
            row = settings_row(db)
        if row is None:
            raise MailError("Configure the email server first.")
        settings = SMTPSettings.model_validate_json(row["configuration"])
        password = (
            self.open_password(row["password"]) if row["password"] is not None else ""
        )
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
                if settings.username:
                    smtp.login(settings.username, password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as error:
            raise MailError(
                "The email could not be delivered. Check the email server settings."
            ) from error
        finally:
            SMTP_WORK.release()
