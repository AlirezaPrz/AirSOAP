"""Security audit events. Fixed event types and fixed detail codes only.

Never recorded: passwords, session tokens, attempted emails (only a keyed hash),
patient details, transcripts, notes or clinical review content.
"""

import re
import sqlite3
from enum import StrEnum


class AuditEvent(StrEnum):
    LOGIN_SUCCEEDED = "login_succeeded"
    LOGIN_FAILED = "login_failed"
    LOGIN_RATE_LIMITED = "login_rate_limited"
    LOGOUT = "logout"
    ROLE_SELECTED = "role_selected"
    SESSION_REJECTED = "session_rejected"
    ACCESS_DENIED = "access_denied"
    REQUEST_REJECTED = "request_rejected"  # failed the same-origin (CSRF) check
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    USER_DISABLED = "user_disabled"
    USER_ENABLED = "user_enabled"
    ROLES_CHANGED = "roles_changed"
    PASSWORD_CHANGED = "password_changed"  # noqa: S105 - an event name, not a password
    PASSWORD_CHANGE_FAILED = "password_change_failed"  # noqa: S105 - an event name, not a password
    PASSWORD_RESET = "password_reset"  # noqa: S105 - an event name, not a password
    ADMIN_BOOTSTRAPPED = "admin_bootstrapped"


# Details are short machine codes such as "inactive" or "admin,clinician>clinician".
_DETAIL = re.compile(r"^[a-z0-9_,>.-]{1,64}$")


def record(
    conn: sqlite3.Connection,
    event: AuditEvent,
    *,
    at: str,
    actor: str | None = None,
    target: str | None = None,
    client_ip: str | None = None,
    subject_hash: str | None = None,
    detail: str | None = None,
) -> None:
    if detail is not None and not _DETAIL.fullmatch(detail):
        raise ValueError("audit details must be short fixed codes")
    conn.execute(
        "INSERT INTO audit_events (occurred_at, event_type, actor_user_id, target_user_id, client_ip, subject_hash,"
        " detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (at, event.value, actor, target, client_ip, subject_hash, detail),
    )
