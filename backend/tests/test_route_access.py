"""Every route is classified here, and the classification is checked against the running app.

Adding a route without adding it to ROUTE_ACCESS fails ``test_every_route_is_classified``,
so no endpoint can ship without someone deciding who may call it.
"""

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from helpers import APP_HEADERS, PASSWORD, add_user, running_app, sign_in

from airsoap.config import Settings

PUBLIC = "public"  # no session needed
ANY_SESSION = "any-session"  # signed in, even before changing a temporary password or choosing a role
ACTIVE = "active"  # password changed and a role chosen; either role
CLINICIAN = "clinician"
ADMIN = "admin"

ROUTE_ACCESS: dict[tuple[str, str], str] = {
    ("GET", "/api/health"): PUBLIC,
    ("POST", "/api/auth/login"): PUBLIC,
    ("POST", "/api/auth/logout"): PUBLIC,
    ("GET", "/api/auth/me"): ANY_SESSION,
    ("POST", "/api/auth/select-role"): ANY_SESSION,
    ("POST", "/api/auth/password"): ANY_SESSION,
    ("GET", "/api/system/status"): ACTIVE,
    ("GET", "/api/admin/users"): ADMIN,
    ("POST", "/api/admin/users"): ADMIN,
    ("PATCH", "/api/admin/users/{user_id}"): ADMIN,
    ("POST", "/api/admin/users/{user_id}/reset-password"): ADMIN,
    ("POST", "/api/recording/start"): CLINICIAN,
    ("POST", "/api/recording/stop"): CLINICIAN,
    ("POST", "/api/recording/cancel"): CLINICIAN,
    ("GET", "/api/recording/{recording_id}/live"): CLINICIAN,
    ("GET", "/api/transcriptions/{job_id}"): CLINICIAN,
    ("POST", "/api/transcriptions/{job_id}/cancel"): CLINICIAN,
    ("POST", "/api/ai/soap"): CLINICIAN,
    ("POST", "/api/ai/clinical-review"): CLINICIAN,
    ("GET", "/api/ai/jobs/{job_id}"): CLINICIAN,
    ("POST", "/api/ai/jobs/{job_id}/cancel"): CLINICIAN,
    ("DELETE", "/api/ai/jobs/{job_id}"): CLINICIAN,
}

PATH_VALUES = {"{user_id}": "0" * 32, "{job_id}": "0" * 32, "{recording_id}": "0" * 32}


def concrete(path: str) -> str:
    for placeholder, value in PATH_VALUES.items():
        path = path.replace(placeholder, value)
    return path


def routes_with(*levels: str) -> list[tuple[str, str]]:
    return [route for route, level in ROUTE_ACCESS.items() if level in levels]


def call(client: TestClient, method: str, path: str, **kwargs):
    # No body: the access checks must answer before the request body is even looked at.
    return client.request(method, concrete(path), **kwargs)


def app_routes(client: TestClient) -> set[tuple[str, str]]:
    found = set()
    for route in iter_route_contexts(client.app.routes):
        for method in route.methods or ():
            if method != "HEAD":
                found.add((method, route.path))
    return found


def test_every_route_is_classified(settings: Settings) -> None:
    with running_app(settings, login_as=None) as client:
        assert app_routes(client) == set(ROUTE_ACCESS)


@pytest.mark.parametrize(("method", "path"), routes_with(ANY_SESSION, ACTIVE, CLINICIAN, ADMIN))
def test_signed_out_requests_are_refused(settings: Settings, method: str, path: str) -> None:
    with running_app(settings, login_as=None) as client:
        response = call(client, method, path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize(("method", "path"), routes_with(CLINICIAN))
def test_admin_role_cannot_use_clinical_routes(settings: Settings, method: str, path: str) -> None:
    with running_app(settings, login_as=("admin",)) as client:
        response = call(client, method, path)
        denied = client.app.state.services.db
        with denied.transaction() as conn:
            events = [
                r[0]
                for r in conn.execute("SELECT detail FROM audit_events WHERE event_type = 'access_denied'")
            ]
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert events == ["needs_clinician"]


@pytest.mark.parametrize(("method", "path"), routes_with(ADMIN))
def test_clinician_role_cannot_use_admin_routes(settings: Settings, method: str, path: str) -> None:
    with running_app(settings) as client:
        response = call(client, method, path)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize(("method", "path"), routes_with(CLINICIAN, ADMIN))
def test_a_dual_role_account_gets_only_the_role_it_chose(settings: Settings, method: str, path: str) -> None:
    chosen = "admin" if ROUTE_ACCESS[(method, path)] == CLINICIAN else "clinician"
    with running_app(settings, login_as=("admin", "clinician")) as client:
        client.post("/api/auth/logout")
        sign_in(client, "clinician@example.test", role=chosen)
        response = call(client, method, path)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize(("method", "path"), routes_with(ACTIVE, CLINICIAN, ADMIN))
def test_a_session_without_a_chosen_role_can_do_nothing_else(
    settings: Settings, method: str, path: str
) -> None:
    with running_app(settings, login_as=None) as client:
        add_user(client, "both@example.test", ("admin", "clinician"))
        assert sign_in(client, "both@example.test").json()["activeRole"] is None
        response = call(client, method, path)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_SELECTION_REQUIRED"


@pytest.mark.parametrize(("method", "path"), routes_with(ACTIVE, CLINICIAN, ADMIN))
def test_a_temporary_password_must_be_changed_first(settings: Settings, method: str, path: str) -> None:
    with running_app(settings, login_as=None) as client:
        add_user(client, "new@example.test", ("clinician",), must_change_password=True)
        sign_in(client, "new@example.test")
        response = call(client, method, path)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"


@pytest.mark.parametrize(
    ("method", "path"), [route for route in ROUTE_ACCESS if route[0] not in {"GET", "HEAD", "OPTIONS"}]
)
def test_state_changing_routes_refuse_requests_from_outside_the_app(
    settings: Settings, method: str, path: str
) -> None:
    with running_app(settings, login_as=("admin", "clinician")) as client:
        bare = {"Origin": "", "X-AirSOAP-Request": ""}
        for headers in (
            {**bare},  # neither
            {**bare, "X-AirSOAP-Request": "1"},  # header but no origin
            {"Origin": "https://evil.example", "X-AirSOAP-Request": "1"},  # foreign origin
            {"Origin": APP_HEADERS["Origin"], "X-AirSOAP-Request": ""},  # origin but no header
        ):
            response = call(client, method, path, headers=headers)
            assert response.status_code == 403, (headers, response.text)
            assert response.json()["error"]["code"] == "REQUEST_REJECTED"


def test_signed_in_requests_reach_the_route(settings: Settings) -> None:
    """The flip side: the right role gets past the checks (to a 404 or 422 from the route itself)."""
    with running_app(settings, login_as=("clinician", "admin")) as client:
        for method, path in routes_with(CLINICIAN):
            response = call(client, method, path)
            assert response.status_code not in {401, 403}, (method, path, response.text)
        client.post("/api/auth/logout")
        sign_in(client, "clinician@example.test", PASSWORD, role="admin")
        for method, path in routes_with(ADMIN):
            response = call(client, method, path)
            assert response.status_code not in {401, 403}, (method, path, response.text)


def test_api_docs_are_off_by_default(settings: Settings) -> None:
    with running_app(settings, login_as=None) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404


def test_api_docs_can_be_switched_on_for_development(settings: Settings) -> None:
    with running_app(settings.model_copy(update={"api_docs": True}), login_as=None) as client:
        assert client.get("/openapi.json").status_code == 200


def test_built_frontend_is_served_but_unknown_api_paths_are_not(settings: Settings, tmp_path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>AirSOAP</title>")
    with running_app(settings.model_copy(update={"frontend_dist": dist}), login_as=None) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "AirSOAP" in page.text
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert client.get("/api/not-a-route").status_code == 404
        assert "AirSOAP" not in client.get("/api/not-a-route").text
