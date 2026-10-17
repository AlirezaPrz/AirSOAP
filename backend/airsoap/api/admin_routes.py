"""Account management for administrators. Every route requires an admin session."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, status

from ..auth.csrf import require_same_origin
from ..auth.deps import AdminDep, AuthDep, client_ip, require_admin
from .auth_schemas import (
    CreateUserRequest,
    TemporaryPasswordResponse,
    UpdateUserRequest,
    UserIdPath,
    UserOut,
)

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_same_origin), Depends(require_admin)],
)

UserId = Annotated[str, Path(pattern=UserIdPath, description="Account ID")]


@router.get("/users")
def list_users(_admin: AdminDep, auth: AuthDep) -> list[UserOut]:
    return [UserOut.from_user(user) for user in auth.list_users()]


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    body: CreateUserRequest, admin: AdminDep, auth: AuthDep, request: Request
) -> TemporaryPasswordResponse:
    user, temporary = auth.create_user(admin, body.email, body.display_name, body.roles, client_ip(request))
    return TemporaryPasswordResponse(user=UserOut.from_user(user), temporary_password=temporary)


@router.patch("/users/{user_id}")
def update_user(
    user_id: UserId, body: UpdateUserRequest, admin: AdminDep, auth: AuthDep, request: Request
) -> UserOut:
    """Changing roles or disabling an account ends that account's sessions at once."""
    user = auth.update_user(
        admin,
        user_id,
        display_name=body.display_name,
        roles=body.roles,
        is_active=body.is_active,
        client_ip=client_ip(request),
    )
    return UserOut.from_user(user)


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: UserId, admin: AdminDep, auth: AuthDep, request: Request
) -> TemporaryPasswordResponse:
    user, temporary = auth.reset_password(admin, user_id, client_ip(request))
    return TemporaryPasswordResponse(user=UserOut.from_user(user), temporary_password=temporary)
