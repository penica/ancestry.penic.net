"""Authenticator secrets, time steps and recovery codes for VM accounts."""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import pyotp
from cryptography.fernet import Fernet, InvalidToken

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

MFA_LOGIN_SECONDS: Final = 300
MFA_SETUP_SECONDS: Final = 600
MFA_PERIOD: Final = 30
RECOVERY_COUNT: Final = 10
ISSUER: Final = "RootsMagic Reader"
OTP: Final = re.compile(r"[0-9]{6}")
RECOVERY: Final = re.compile(r"[0-9A-F]{20}")
KEY: Final = re.compile(rb"[A-Za-z0-9_-]{43}=")
SECRET: Final = re.compile(r"[A-Z2-7]{32}")
MFA_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS reader_mfa_factors (
    account_id INTEGER PRIMARY KEY REFERENCES reader_accounts(id) ON DELETE CASCADE,
    secret BLOB NOT NULL, last_step INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS reader_mfa_setups (
    session_hash BLOB PRIMARY KEY REFERENCES reader_sessions(token_hash) ON DELETE CASCADE,
    secret BLOB NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS reader_mfa_challenges (
    account_id INTEGER PRIMARY KEY REFERENCES reader_accounts(id) ON DELETE CASCADE,
    token_hash BLOB NOT NULL UNIQUE, password_salt BLOB NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS reader_mfa_recovery (
    account_id INTEGER NOT NULL REFERENCES reader_mfa_factors(account_id) ON DELETE CASCADE,
    code_hash BLOB NOT NULL,
    PRIMARY KEY (account_id, code_hash)
);
"""


class MFAError(RuntimeError):
    """An authenticator key is unavailable to the reader."""


@dataclass(frozen=True, slots=True)
class MFASetup:
    """The short-lived setup key and its authenticator provisioning URI."""

    secret: str
    provisioning_uri: str


def load_cipher(path: Path | None) -> Fernet:
    """Open the VM's separately stored authenticator encryption key."""
    message = "Two-step verification is unavailable. Contact the reader administrator."
    if path is None:
        raise MFAError(message)
    try:
        with path.open("rb") as source:
            key = source.read(128).strip()
        if KEY.fullmatch(key) is None:
            raise MFAError(message)
        return Fernet(key)
    except (OSError, ValueError) as error:
        raise MFAError(message) from error


def new_secret() -> str:
    """Generate a 160-bit secret for an authenticator app."""
    return pyotp.random_base32()


def setup_for(secret: str, username: str) -> MFASetup:
    """Create the local QR payload and manual key for a pending setup."""
    uri = pyotp.TOTP(secret, interval=MFA_PERIOD).provisioning_uri(
        name=username, issuer_name=ISSUER
    )
    return MFASetup(secret, uri)


def seal_secret(cipher: Fernet, account_id: int, secret: str) -> bytes:
    """Encrypt the authenticator secret and bind it to the account."""
    return cipher.encrypt(f"{account_id}:{secret}".encode("ascii"))


def open_secret(cipher: Fernet, account_id: int, ciphertext: bytes) -> str:
    """Decrypt an authenticator secret only for the matching account."""
    message = "The authenticator key is unavailable. Contact the reader administrator."
    try:
        owner, separator, secret = (
            cipher.decrypt(ciphertext).decode("ascii").partition(":")
        )
    except (InvalidToken, UnicodeDecodeError) as error:
        raise MFAError(message) from error
    if not separator or owner != str(account_id) or SECRET.fullmatch(secret) is None:
        raise MFAError(message)
    return secret


def valid_step(secret: str, code: str, now: int, last_step: int) -> int | None:
    """Find a matching fresh code within one 30-second step of the VM clock."""
    code = code.strip()
    if OTP.fullmatch(code) is None:
        return None
    authenticator = pyotp.TOTP(secret, interval=MFA_PERIOD)
    current = now // MFA_PERIOD
    return next(
        (
            step
            for step in (current, current - 1, current + 1)
            if step > last_step
            and step >= 0
            and secrets.compare_digest(authenticator.generate_otp(step), code)
        ),
        None,
    )


def recovery_hash(value: str) -> bytes | None:
    """Read a recovery code with or without its printed separators."""
    if len(value) > 32 or not value.isascii():
        return None
    value = value.strip().replace("-", "").upper()
    if RECOVERY.fullmatch(value) is None:
        return None
    return hashlib.sha256(b"rootsmagic recovery v1:" + value.encode("ascii")).digest()


def replace_recovery_codes(db: sqlite3.Connection, account_id: int) -> tuple[str, ...]:
    """Store one fresh set of 80-bit recovery codes as hashes."""
    codes = tuple(secrets.token_hex(10).upper() for _ in range(RECOVERY_COUNT))
    db.execute("DELETE FROM reader_mfa_recovery WHERE account_id = ?", (account_id,))
    db.executemany(
        "INSERT INTO reader_mfa_recovery VALUES (?, ?)",
        ((account_id, recovery_hash(code)) for code in codes),
    )
    return tuple(
        "-".join(code[index : index + 5] for index in range(0, 20, 5)) for code in codes
    )


def consume_factor(
    db: sqlite3.Connection, cipher: Fernet, account_id: int, code: str, now: int
) -> bool:
    """Verify and consume a code inside the same transaction as its action."""
    factor = db.execute(
        "SELECT secret, last_step FROM reader_mfa_factors WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    if factor is None:
        return False
    secret = open_secret(cipher, account_id, factor["secret"])
    step = valid_step(secret, code, now, factor["last_step"])
    if step is not None:
        db.execute(
            "UPDATE reader_mfa_factors SET last_step = ? WHERE account_id = ?",
            (step, account_id),
        )
        return True
    code_hash = recovery_hash(code)
    if code_hash is None:
        return False
    deleted = db.execute(
        "DELETE FROM reader_mfa_recovery WHERE account_id = ? AND code_hash = ?",
        (account_id, code_hash),
    )
    return deleted.rowcount == 1
