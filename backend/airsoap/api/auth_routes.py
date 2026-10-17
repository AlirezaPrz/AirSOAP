"""Sign-in, sign-out, the current session, role choice and password change.

All are plain ``def`` handlers: password hashing is deliberately slow, and FastAPI runs
sync handlers in its thread pool, away from the event loop.
"""

from fastapi import APIRouter, Depends, Request, Response, status

from ..auth.csrf import require_same_origin
from ..auth.deps import (
    SESSION_COOKIE,
    AuthDep,
    SessionDep,
    clear_session_cookie,
    client_ip,
    set_session_cookie,
)
from .auth_schemas import ChangePasswordRequest, LoginRequest, MeResponse, SelectRoleRequest
from .deps import ServicesDep

router = APIRouter(prefix="/api/auth", tags=["auth"], dependencies=[Depends(require_same_origin)])


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, services: ServicesDep) -> MeResponse:
    """Wrong email, wrong password and disabled account all get the same answer."""
    previous = request.cookies.get(SESSION_COOKIE)
    if previous:
        services.auth.logout(previous, client_ip(request))
    issued = services.auth.login(body.email, body.password, client_ip(request))
    set_session_cookie(response, issued.token, secure=services.settings.resolved_cookie_secure)
    return MeResponse.from_context(issued.context)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, services: ServicesDep) -> Response:
    """Ends the session and discards this account's recording and jobs. Safe to call when signed out."""
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        user_id = services.auth.logout(token, client_ip(request))
        if user_id:
            await services.discard_owner(user_id)
    clear_session_cookie(response, secure=services.settings.resolved_cookie_secure)
    return response


@router.get("/me")
def me(ctx: SessionDep) -> MeResponse:
    return MeResponse.from_context(ctx)


@router.post("/select-role")
def select_role(
    body: SelectRoleRequest, ctx: SessionDep, request: Request, response: Response, services: ServicesDep
) -> MeResponse:
    issued = services.auth.select_role(ctx, body.role, client_ip(request))
    set_session_cookie(response, issued.token, secure=services.settings.resolved_cookie_secure)
    return MeResponse.from_context(issued.context)


@router.post("/password")
def change_password(
    body: ChangePasswordRequest,
    ctx: SessionDep,
    auth: AuthDep,
    request: Request,
    response: Response,
    services: ServicesDep,
) -> MeResponse:
    """Ends every other session of this account and issues a fresh one to this browser."""
    issued = auth.change_password(ctx, body.current_password, body.new_password, client_ip(request))
    set_session_cookie(response, issued.token, secure=services.settings.resolved_cookie_secure)
    return MeResponse.from_context(issued.context)
