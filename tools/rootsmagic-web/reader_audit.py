"""Record security outcomes without storing request data or personal information."""

import logging
from hashlib import sha256
from secrets import token_bytes
from typing import Final, Literal

AuditEvent = Literal[
    "sign_in",
    "sign_out",
    "invitation_created",
    "invitation_redeemed",
    "invitation_revoked",
    "account_access_changed",
    "account_status_changed",
    "account_deleted",
    "profile_changed",
    "mfa_changed",
    "passkey_changed",
    "mail_settings_changed",
    "system_settings_changed",
    "test_mail",
    "invitation_mail",
    "api_key_created",
    "api_key_renamed",
    "api_key_rotated",
    "api_key_revoked",
]
AuditOutcome = Literal["ok", "denied", "limited", "failed"]
LOG: Final = logging.getLogger("uvicorn.error")
VISITOR_SALT: Final = token_bytes(32)


def audit(
    event: AuditEvent,
    outcome: AuditOutcome,
    *,
    actor: int | None = None,
    target: int | None = None,
    visitor: str | None = None,
) -> None:
    """Log only allowlisted event fields and internal account or invitation IDs."""
    visitor_id = sha256(VISITOR_SALT + visitor.encode()).hexdigest()[:16] if visitor else None
    LOG.info(
        "reader_audit event=%s outcome=%s actor=%s target=%s visitor=%s",
        event,
        outcome,
        actor,
        target,
        visitor_id,
    )
