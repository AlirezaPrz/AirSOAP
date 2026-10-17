"""Account management by administrators. All accounts are synthetic."""

from fastapi.testclient import TestClient
from helpers import CLINICIAN, PASSWORD, add_user, running_app

from airsoap.auth.deps import SESSION_COOKIE
from airsoap.config import Settings

ADMIN = CLINICIAN  # running_app's account; here it signs in with the admin role


def admin_app(settings: Settings):
    return running_app(settings, login_as=("admin",))


def create(client: TestClient, email: str = "new@example.test", roles=("clinician",), name="Dr New"):
    return client.post("/api/admin/users", json={"email": email, "displayName": name, "roles": list(roles)})


def users(client: TestClient) -> dict[str, dict]:
    return {user["email"]: user for user in client.get("/api/admin/users").json()}


def session_of(client: TestClient, email: str, password: str = PASSWORD) -> str:
    """Sign ``email`` in from a 'separate browser' and return its token; the admin's cookie is kept."""
    admin_cookie = client.cookies[SESSION_COOKIE]
    client.cookies.clear()
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    token = client.cookies[SESSION_COOKIE]
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, admin_cookie, path="/api")
    return token


def me_with(client: TestClient, token: str):
    admin_cookie = client.cookies[SESSION_COOKIE]
    client.cookies.clear()
    response = client.get("/api/auth/me", headers={"Cookie": f"{SESSION_COOKIE}={token}"})
    client.cookies.set(SESSION_COOKIE, admin_cookie, path="/api")
    return response


def audit(client: TestClient, event: str) -> list[dict]:
    with client.app.state.services.db.transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE event_type = ? ORDER BY id", (event,)
        ).fetchall()
    return [dict(row) for row in rows]


def test_creating_an_account_returns_a_one_time_temporary_password(settings: Settings) -> None:
    with admin_app(settings) as client:
        response = create(client, "New.Doctor@Example.test")
        assert response.status_code == 201
        body = response.json()
        temporary = body["temporaryPassword"]
        assert len(temporary) >= 16
        assert body["user"]["email"] == "new.doctor@example.test"
        assert body["user"]["mustChangePassword"] is True
        assert body["user"]["roles"] == ["clinician"]

        listed = client.get("/api/admin/users")
        assert temporary not in listed.text
        assert "passwordHash" not in listed.text and "argon2" not in listed.text

        token = session_of(client, "new.doctor@example.test", temporary)
        assert me_with(client, token).json()["mustChangePassword"] is True
        admin_id = client.get("/api/auth/me").json()["user"]["id"]
        created = audit(client, "user_created")[-1]  # the first is the test admin, created by the CLI path
    assert created["detail"] == "clinician"
    assert created["actor_user_id"] == admin_id
    assert created["target_user_id"] == body["user"]["id"]


def test_temporary_passwords_differ_each_time(settings: Settings) -> None:
    with admin_app(settings) as client:
        first = create(client, "a@example.test").json()["temporaryPassword"]
        second = create(client, "b@example.test").json()["temporaryPassword"]
    assert first != second


def test_an_email_can_only_be_used_once(settings: Settings) -> None:
    with admin_app(settings) as client:
        create(client, "new@example.test")
        again = create(client, "NEW@example.test")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "EMAIL_TAKEN"


def test_account_details_are_validated(settings: Settings) -> None:
    with admin_app(settings) as client:
        assert create(client, roles=()).status_code == 422
        assert create(client, roles=("superuser",)).status_code == 422
        assert create(client, email="not-an-email").json()["error"]["code"] == "INVALID_EMAIL"
        assert create(client, name="   ").json()["error"]["code"] == "INVALID_DISPLAY_NAME"
        assert create(client, name="x" * 101).status_code in {400, 422}


def test_an_account_can_hold_both_roles(settings: Settings) -> None:
    with admin_app(settings) as client:
        body = create(client, roles=("clinician", "admin")).json()
    assert body["user"]["roles"] == ["admin", "clinician"]


def test_disabling_an_account_ends_its_sessions_at_once(settings: Settings) -> None:
    with admin_app(settings) as client:
        target = add_user(client, "doc@example.test")
        token = session_of(client, "doc@example.test")
        assert me_with(client, token).status_code == 200

        response = client.patch(f"/api/admin/users/{target.id}", json={"isActive": False})
        assert response.status_code == 200
        assert response.json()["isActive"] is False

        ended = me_with(client, token)
        assert ended.status_code == 401
        assert ended.json()["error"]["code"] == "ACCOUNT_DISABLED"
        admin_cookie = client.cookies[SESSION_COOKIE]
        client.cookies.clear()
        refused = client.post("/api/auth/login", json={"email": "doc@example.test", "password": PASSWORD})
        assert refused.status_code == 401  # with the same message as a wrong password
        assert refused.json()["error"]["code"] == "INVALID_CREDENTIALS"
        client.cookies.set(SESSION_COOKIE, admin_cookie, path="/api")

        assert client.patch(f"/api/admin/users/{target.id}", json={"isActive": True}).status_code == 200
        assert me_with(client, session_of(client, "doc@example.test")).status_code == 200
        assert len(audit(client, "user_disabled")) == 1
        assert len(audit(client, "user_enabled")) == 1


def test_changing_roles_ends_the_accounts_sessions(settings: Settings) -> None:
    with admin_app(settings) as client:
        target = add_user(client, "doc@example.test")
        token = session_of(client, "doc@example.test")
        response = client.patch(f"/api/admin/users/{target.id}", json={"roles": ["admin", "clinician"]})
        assert response.json()["roles"] == ["admin", "clinician"]
        assert me_with(client, token).status_code == 401
        [event] = audit(client, "roles_changed")
    assert event["detail"] == "clinician>admin,clinician"


def test_renaming_keeps_the_accounts_sessions(settings: Settings) -> None:
    with admin_app(settings) as client:
        target = add_user(client, "doc@example.test")
        token = session_of(client, "doc@example.test")
        response = client.patch(f"/api/admin/users/{target.id}", json={"displayName": "Dr Renamed"})
        assert response.json()["displayName"] == "Dr Renamed"
        assert me_with(client, token).status_code == 200


def test_resetting_a_password_ends_sessions_and_requires_a_change(settings: Settings) -> None:
    with admin_app(settings) as client:
        target = add_user(client, "doc@example.test")
        token = session_of(client, "doc@example.test")
        response = client.post(f"/api/admin/users/{target.id}/reset-password")
        assert response.status_code == 200
        temporary = response.json()["temporaryPassword"]
        assert response.json()["user"]["mustChangePassword"] is True
        assert me_with(client, token).status_code == 401

        client.cookies.clear()
        old = client.post("/api/auth/login", json={"email": "doc@example.test", "password": PASSWORD})
        assert old.status_code == 401
        new = client.post("/api/auth/login", json={"email": "doc@example.test", "password": temporary})
        assert new.json()["mustChangePassword"] is True


def test_an_administrator_cannot_disable_themselves(settings: Settings) -> None:
    with admin_app(settings) as client:
        me = client.get("/api/auth/me").json()["user"]
        add_user(client, "second-admin@example.test", ("admin",))  # not the last admin, still refused
        response = client.patch(f"/api/admin/users/{me['id']}", json={"isActive": False})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CANNOT_DISABLE_SELF"


def test_the_last_active_administrator_cannot_be_removed(settings: Settings) -> None:
    with admin_app(settings) as client:
        me = client.get("/api/auth/me").json()["user"]
        response = client.patch(f"/api/admin/users/{me['id']}", json={"roles": ["clinician"]})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "LAST_ADMIN"
        assert users(client)[ADMIN]["roles"] == ["admin"]

        other = add_user(client, "second-admin@example.test", ("admin",))
        disabled = client.patch(f"/api/admin/users/{other.id}", json={"isActive": False})
        assert disabled.status_code == 200  # another admin remains (this one)
        assert client.patch(f"/api/admin/users/{me['id']}", json={"roles": ["clinician"]}).status_code == 409

        client.patch(f"/api/admin/users/{other.id}", json={"isActive": True})
        demoted = client.patch(f"/api/admin/users/{me['id']}", json={"roles": ["clinician"]})
        assert demoted.status_code == 200
        # Demoting yourself ends your own admin session.
        assert client.get("/api/admin/users").status_code == 401


def test_unknown_and_malformed_account_ids(settings: Settings) -> None:
    with admin_app(settings) as client:
        missing = client.patch(f"/api/admin/users/{'a' * 32}", json={"displayName": "X"})
        malformed = client.patch("/api/admin/users/../../etc", json={"displayName": "X"})
        reset = client.post(f"/api/admin/users/{'a' * 32}/reset-password")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "USER_NOT_FOUND"
    assert malformed.status_code in {404, 422}
    assert reset.status_code == 404


def test_admin_responses_are_never_cached(settings: Settings) -> None:
    with admin_app(settings) as client:
        response = create(client)
    assert response.headers["cache-control"] == "no-store"
