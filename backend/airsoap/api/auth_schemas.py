from datetime import datetime
from typing import Annotated

from pydantic import Field

from ..auth.models import Role, SessionContext, User
from ..models import CamelModel

# Generous request bounds; the real password policy is enforced when a password is set.
EmailField = Annotated[str, Field(min_length=1, max_length=320)]
SecretField = Annotated[str, Field(min_length=1, max_length=1024)]
UserIdPath = r"^[0-9a-f]{32}$"


class LoginRequest(CamelModel):
    email: EmailField
    password: SecretField


class SelectRoleRequest(CamelModel):
    role: Role


class ChangePasswordRequest(CamelModel):
    current_password: SecretField
    new_password: SecretField


class UserOut(CamelModel):
    id: str
    email: str
    display_name: str
    roles: list[Role]
    is_active: bool
    must_change_password: bool
    created_at: datetime
    last_login_at: datetime | None

    @classmethod
    def from_user(cls, user: User) -> "UserOut":
        return cls(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            roles=list(user.roles),
            is_active=user.is_active,
            must_change_password=user.must_change_password,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )


class MeResponse(CamelModel):
    """Who is signed in, and what they still have to do (change a temporary password, choose a role)."""

    user: UserOut
    active_role: Role | None
    must_change_password: bool
    session_expires_at: datetime

    @classmethod
    def from_context(cls, ctx: SessionContext) -> "MeResponse":
        return cls(
            user=UserOut.from_user(ctx.user),
            active_role=ctx.active_role,
            must_change_password=ctx.user.must_change_password,
            session_expires_at=ctx.expires_at,
        )


class CreateUserRequest(CamelModel):
    email: EmailField
    display_name: Annotated[str, Field(min_length=1, max_length=200)]
    roles: Annotated[list[Role], Field(min_length=1, max_length=2)]


class UpdateUserRequest(CamelModel):
    display_name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    roles: Annotated[list[Role], Field(min_length=1, max_length=2)] | None = None
    is_active: bool | None = None


class TemporaryPasswordResponse(CamelModel):
    """Shown to the administrator once. It is never stored in plain text or logged."""

    user: UserOut
    temporary_password: str
