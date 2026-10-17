import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Role = Literal["admin", "clinician"]
ROLES: tuple[Role, ...] = ("admin", "clinician")

_EMAIL = re.compile(r"^[a-z0-9._%+-]{1,64}@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")


def normalize_email(raw: str) -> str | None:
    """Lowercase, NFKC-normalized email, or None if it does not look like an email address."""
    email = unicodedata.normalize("NFKC", raw).strip().lower()
    if len(email) > 254 or not _EMAIL.fullmatch(email):
        return None
    return email


@dataclass(frozen=True)
class User:
    id: str
    email: str
    display_name: str
    roles: tuple[Role, ...]
    is_active: bool
    must_change_password: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None


@dataclass(frozen=True)
class SessionContext:
    token_hash: str
    user: User
    active_role: Role | None
    expires_at: datetime


@dataclass(frozen=True)
class IssuedSession:
    """A new session. ``token`` goes into the cookie and is never stored or logged."""

    token: str
    context: SessionContext
