"""Record security outcomes without storing request data or personal information."""

import logging
from typing import Final, Literal

AuditEvent = Literal[
    "sign_in",
    "sign_out",
    "invitation_created",
    "invitation_redeemed",
    "invitation_revoked",
    "account_access_changed",
    "account_status_changed",
    "profile_changed",
    "mfa_changed",
    "passkey_changed",
    "mail_settings_changed",
    "system_settings_changed",
    "test_mail",
    "invitation_mail",
]
AuditOutcome = Literal["ok", "denied", "limited", "failed"]
LOG: Final = logging.getLogger("uvicorn.error")


def audit(
    event: AuditEvent,
    outcome: AuditOutcome,
    *,
    actor: int | None = None,
    target: int | None = None,
) -> None:
    """Log only allowlisted event fields and internal account or invitation IDs."""
    LOG.info(
        "reader_audit event=%s outcome=%s actor=%s target=%s",
        event,
        outcome,
        actor,
        target,
    )
