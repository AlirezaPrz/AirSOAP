"""FastAPI dependencies that enforce sign-in and roles, and the session cookie.

Every protected router declares one of these at router level, so a route cannot be
added to a protected router without inheriting its check. Use them as aliases:

    @router.get("/thing")
    def thing(user: ClinicianDep): ...
"""

from typing import Annotated

from fastapi import Depends, Request, Response

from ..errors import ApiError
from .models import Role, SessionContext
from .service import AuthService

SESSION_COOKIE = "airsoap_session"
COOKIE_PATH = "/api"


def client_ip(request: Request) -> str | None:
    # With uvicorn's proxy headers enabled for the local Caddy, this is the browser's address.
    return request.client.host if request.client else None


def get_auth(request: Request) -> AuthService:
    return request.app.state.services.auth


AuthDep = Annotated[AuthService, Depends(get_auth)]


def session_context(request: Request, auth: AuthDep) -> SessionContext:
    """Any live session, including one that still has to change its password or choose a role."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise ApiError(401, "AUTH_REQUIRED", "Please sign in.")
    ctx, reason = auth.authenticate(token, client_ip(request))
    if ctx is None:
        if reason == "disabled":
            raise ApiError(
                401, "ACCOUNT_DISABLED", "Your account has been disabled. Contact your administrator."
            )
        raise ApiError(401, "SESSION_EXPIRED", "Your session has ended. Please sign in again.")
    return ctx


SessionDep = Annotated[SessionContext, Depends(session_context)]


def active_session(ctx: SessionDep) -> SessionContext:
    """A session that may use the application: password changed and a role chosen."""
    if ctx.user.must_change_password:
        raise ApiError(403, "PASSWORD_CHANGE_REQUIRED", "Change your temporary password to continue.")
    if ctx.active_role is None:
        raise ApiError(403, "ROLE_SELECTION_REQUIRED", "Choose how you want to sign in to continue.")
    return ctx


ActiveSessionDep = Annotated[SessionContext, Depends(active_session)]


def _require(role: Role):
    def check(request: Request, ctx: ActiveSessionDep, auth: AuthDep) -> SessionContext:
        if ctx.active_role != role:
            auth.record_access_denied(ctx, client_ip(request), f"needs_{role}")
            raise ApiError(403, "FORBIDDEN", "Your current role does not allow this.")
        return ctx

    check.__name__ = f"require_{role}"
    return check


require_clinician = _require("clinician")
require_admin = _require("admin")

ClinicianDep = Annotated[SessionContext, Depends(require_clinician)]
AdminDep = Annotated[SessionContext, Depends(require_admin)]


def set_session_cookie(response: Response, token: str, *, secure: bool) -> None:
    # No max-age: the cookie ends with the browser session; the server enforces idle and absolute expiry.
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, secure=secure, samesite="strict", path=COOKIE_PATH
    )


def clear_session_cookie(response: Response, *, secure: bool) -> None:
    response.delete_cookie(SESSION_COOKIE, httponly=True, secure=secure, samesite="strict", path=COOKIE_PATH)
