"""Sign-in, sessions, cookies, CSRF checks, role choice, password changes and rate limits.

All accounts and passwords here are synthetic. Time is controlled with a fake clock.
"""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from helpers import (
    APP_HEADERS,
    CLINICIAN,
    FAST_HASHER,
    PASSWORD,
    FakeRecorder,
    add_user,
    record_once,
    running_app,
    wait_for_status,
)

from airsoap.auth.deps import SESSION_COOKIE
from airsoap.auth.passwords import Passwords
from airsoap.config import Settings

NEW_PASSWORD = "another-synthetic-pass-2"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def token(client: TestClient) -> str:
    return client.cookies[SESSION_COOKIE]


def with_cookie(value: str) -> dict[str, str]:
    return {"Cookie": f"{SESSION_COOKIE}={value}"}


def login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def audit(client: TestClient, event: str | None = None) -> list[dict]:
    with client.app.state.services.db.transaction() as conn:
        rows = conn.execute("SELECT * FROM audit_events ORDER BY id").fetchall()
    return [dict(row) for row in rows if event is None or row["event_type"] == event]


def error_code(response) -> str:
    return response.json()["error"]["code"]


# --- sign-in ------------------------------------------------------------------------------


def test_sign_in_sets_an_httponly_strict_session_cookie(settings: Settings) -> None:
    with running_app(settings, login_as=None) as client:
        add_user(client, CLINICIAN)
        response = login(client, CLINICIAN)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    attributes = {part.strip().split("=")[0].lower() for part in cookie.split(";")}
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "httponly" in attributes
    assert "samesite=strict" in cookie.lower()
    assert "path=/api" in cookie.lower()
    assert "max-age" not in attributes and "expires" not in attributes  # ends with the browser session
    assert "secure" not in attributes  # this test talks plain HTTP; see the next test
    value = cookie.split(";")[0].split("=", 1)[1]
    assert len(value) >= 43  # 256 random bits
    assert value not in response.text  # the token is only ever in the cookie


def test_the_cookie_is_secure_when_configured(settings: Settings) -> None:
    with running_app(settings.model_copy(update={"cookie_secure": True}), login_as=None) as client:
        add_user(client, CLINICIAN)
        cookie = login(client, CLINICIAN).headers["set-cookie"]
    assert "secure" in {part.strip().lower() for part in cookie.split(";")}


def test_a_single_role_account_goes_straight_in(settings: Settings) -> None:
    with running_app(settings, login_as=None) as client:
        add_user(client, CLINICIAN, name="Dr Test")
        body = login(client, CLINICIAN).json()
        me = client.get("/api/auth/me").json()
        assert client.get("/api/system/status").status_code == 200
    assert body["activeRole"] == "clinician"
    assert body["mustChangePassword"] is False
    assert me["user"] == {
        "id": me["user"]["id"],
        "email": CLINICIAN,
        "displayName": "Dr Test",
        "roles": ["clinician"],
        "isActive": True,
        "mustChangePassword": False,
        "createdAt": me["user"]["createdAt"],
        "lastLoginAt": me["user"]["lastLoginAt"],
    }
    assert me["activeRole"] == "clinician"


def test_email_is_matched_without_regard_to_case_or_spaces(settings: Settings) -> None:
    with running_app(settings, login_as=None) as client:
        add_user(client, CLINICIAN)
        assert login(client, "  Clinician@EXAMPLE.test ").status_code == 200


def test_wrong_password_unknown_account_and_disabled_account_look_the_same(settings: Settings) -> None:
    with running_app(settings, login_as=None) as client:
        add_user(client, CLINICIAN)
        disabled = add_user(client, "disabled@example.test")
        with client.app.state.services.db.transaction() as conn:
            conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (disabled.id,))
        answers = [
            login(client, CLINICIAN, "wrong-password-entirely"),
            login(client, "nobody@example.test"),
            login(client, "disabled@example.test"),
            login(client, "not an email address"),
        ]
        reasons = [row["detail"] for row in audit(client, "login_failed")]
    for answer in answers:
        assert answer.status_code == 401
        assert answer.json() == answers[0].json()
        assert "set-cookie" not in answer.headers
    assert error_code(answers[0]) == "INVALID_CREDENTIALS"
    assert reasons == ["bad_credentials", "bad_credentials", "inactive", "bad_credentials"]


def test_passwords_are_stored_as_argon2id_hashes(settings: Settings) -> None:
    with running_app(settings, login_as=None) as client:
        add_user(client, CLINICIAN)
        with client.app.state.services.db.transaction() as conn:
            stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored


def test_the_production_hasher_uses_the_rfc_9106_low_memory_profile() -> None:
    hashed = Passwords().hash("synthetic-password-for-hash")
    assert hashed.startswith("$argon2id$v=19$m=65536,t=3,p=4$")


def test_an_outdated_hash_is_upgraded_at_the_next_sign_in(settings: Settings) -> None:
    with running_app(settings, login_as=None) as client:
        add_user(client, CLINICIAN)
    stronger = PasswordHasher(time_cost=2, memory_cost=8 * 1024, parallelism=1)
    with running_app(settings, login_as=None, password_hasher=stronger) as client:
        assert login(client, CLINICIAN).status_code == 200
        with client.app.state.services.db.transaction() as conn:
            stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    assert "m=8192,t=2,p=1" in stored


# --- sessions -----------------------------------------------------------------------------


def test_sign_out_ends_the_session_on_the_server(settings: Settings) -> None:
    with running_app(settings) as client:
        old = token(client)
        response = client.post("/api/auth/logout")
        assert response.status_code == 204
        assert "max-age=0" in response.headers["set-cookie"].lower()
        client.cookies.clear()
        replay = client.get("/api/auth/me", headers=with_cookie(old))
        assert client.post("/api/auth/logout").status_code == 204  # harmless when signed out
    assert replay.status_code == 401
    assert error_code(replay) == "SESSION_EXPIRED"


def test_signing_in_again_ends_the_previous_session_from_this_browser(settings: Settings) -> None:
    with running_app(settings) as client:
        old = token(client)
        login(client, CLINICIAN)
        new = token(client)
        client.cookies.clear()
        assert client.get("/api/auth/me", headers=with_cookie(old)).status_code == 401
        assert client.get("/api/auth/me", headers=with_cookie(new)).status_code == 200


def test_a_made_up_session_token_is_refused(settings: Settings) -> None:
    with running_app(settings, login_as=None) as client:
        response = client.get("/api/auth/me", headers=with_cookie("x" * 43))
    assert response.status_code == 401
    assert error_code(response) == "SESSION_EXPIRED"


def test_a_session_expires_after_30_idle_minutes(settings: Settings) -> None:
    clock = Clock()
    with running_app(settings, auth_clock=clock) as client:
        clock.advance(minutes=29)
        assert client.get("/api/auth/me").status_code == 200
        clock.advance(minutes=30, seconds=1)
        response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert error_code(response) == "SESSION_EXPIRED"


def test_activity_keeps_a_session_alive_until_the_12_hour_limit(settings: Settings) -> None:
    clock = Clock()
    with running_app(settings, auth_clock=clock) as client:
        for _ in range(35):  # 35 x 20 minutes = 11 h 40 min
            clock.advance(minutes=20)
            assert client.get("/api/auth/me").status_code == 200
        clock.advance(minutes=20, seconds=1)  # past 12 hours since sign-in
        response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert error_code(response) == "SESSION_EXPIRED"


def test_me_reports_when_the_session_will_end(settings: Settings) -> None:
    clock = Clock()
    with running_app(settings, auth_clock=clock) as client:
        expires = datetime.fromisoformat(client.get("/api/auth/me").json()["sessionExpiresAt"])
    assert expires == clock.now + timedelta(minutes=30)


# --- roles --------------------------------------------------------------------------------


def test_a_dual_role_account_chooses_a_role_and_gets_a_new_session(settings: Settings) -> None:
    with running_app(settings, login_as=None) as client:
        add_user(client, "both@example.test", ("admin", "clinician"))
        body = login(client, "both@example.test").json()
        assert body["activeRole"] is None
        assert body["user"]["roles"] == ["admin", "clinician"]
        pending = token(client)

        chosen = client.post("/api/auth/select-role", json={"role": "admin"})
        assert chosen.status_code == 200
        assert chosen.json()["activeRole"] == "admin"
        assert token(client) != pending
        assert client.get("/api/admin/users").status_code == 200

        again = client.post("/api/auth/select-role", json={"role": "clinician"})
        assert again.status_code == 409
        assert error_code(again) == "ROLE_ALREADY_SELECTED"

        client.cookies.clear()
        assert client.get("/api/auth/me", headers=with_cookie(pending)).status_code == 401
        assert [row["detail"] for row in audit(client, "role_selected")] == ["admin"]


def test_an_unknown_role_is_rejected(settings: Settings) -> None:
    with running_app(settings, login_as=None) as client:
        add_user(client, "both@example.test", ("admin", "clinician"))
        login(client, "both@example.test")
        assert client.post("/api/auth/select-role", json={"role": "superuser"}).status_code == 422


def test_an_unchosen_sign_in_lapses_after_five_minutes(settings: Settings) -> None:
    clock = Clock()
    with running_app(settings, login_as=None, auth_clock=clock) as client:
        add_user(client, "both@example.test", ("admin", "clinician"))
        login(client, "both@example.test")
        clock.advance(minutes=5, seconds=1)
        response = client.post("/api/auth/select-role", json={"role": "clinician"})
    assert response.status_code == 401


# --- passwords ----------------------------------------------------------------------------


def test_a_temporary_password_must_be_replaced_before_anything_else(settings: Settings) -> None:
    with running_app(settings, login_as=None) as client:
        add_user(client, CLINICIAN, must_change_password=True)
        body = login(client, CLINICIAN).json()
        assert body["mustChangePassword"] is True
        assert body["activeRole"] is None
        refused = client.post("/api/auth/select-role", json={"role": "clinician"})
        assert error_code(refused) == "PASSWORD_CHANGE_REQUIRED"

        changed = client.post(
            "/api/auth/password", json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD}
        )
        assert changed.status_code == 200
        assert changed.json()["mustChangePassword"] is False
        assert changed.json()["activeRole"] == "clinician"  # a single role goes straight in
        assert client.get("/api/system/status").status_code == 200

        client.post("/api/auth/logout")
        assert login(client, CLINICIAN).status_code == 401
        assert login(client, CLINICIAN, NEW_PASSWORD).status_code == 200


def test_a_dual_role_account_chooses_its_role_after_the_password_change(settings: Settings) -> None:
    with running_app(settings, login_as=None) as client:
        add_user(client, "both@example.test", ("admin", "clinician"), must_change_password=True)
        login(client, "both@example.test")
        changed = client.post(
            "/api/auth/password", json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD}
        )
        assert changed.json()["activeRole"] is None
        assert client.post("/api/auth/select-role", json={"role": "clinician"}).status_code == 200


@pytest.mark.parametrize(
    ("current", "new", "code"),
    [
        ("wrong-current-password", NEW_PASSWORD, "WRONG_PASSWORD"),
        (PASSWORD, "short", "WEAK_PASSWORD"),
        (PASSWORD, "x" * 129, "WEAK_PASSWORD"),
        (PASSWORD, CLINICIAN, "WEAK_PASSWORD"),
        (PASSWORD, "clinician", "WEAK_PASSWORD"),
        (PASSWORD, PASSWORD, "WEAK_PASSWORD"),
    ],
)
def test_password_changes_are_checked(settings: Settings, current: str, new: str, code: str) -> None:
    with running_app(settings) as client:
        response = client.post("/api/auth/password", json={"currentPassword": current, "newPassword": new})
        still_signed_in = client.get("/api/auth/me").status_code
    assert response.status_code == 400
    assert error_code(response) == code
    assert still_signed_in == 200


def test_changing_the_password_ends_the_accounts_other_sessions(settings: Settings) -> None:
    with running_app(settings) as client:
        other = token(client)
        client.cookies.clear()
        login(client, CLINICIAN)  # a second browser
        client.post("/api/auth/password", json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD})
        mine = token(client)
        client.cookies.clear()
        assert client.get("/api/auth/me", headers=with_cookie(other)).status_code == 401
        assert client.get("/api/auth/me", headers=with_cookie(mine)).status_code == 200


# --- rate limiting ------------------------------------------------------------------------


def test_an_account_is_locked_for_15_minutes_after_5_failures(settings: Settings) -> None:
    clock = Clock()
    with running_app(settings, login_as=None, auth_clock=clock) as client:
        add_user(client, CLINICIAN)
        for _ in range(5):
            assert login(client, CLINICIAN, "wrong-password-entirely").status_code == 401
        locked = login(client, CLINICIAN)  # even the right password
        assert locked.status_code == 429
        assert error_code(locked) == "TOO_MANY_ATTEMPTS"
        assert "set-cookie" not in locked.headers
        # Another account from the same address is not affected by this account's lock.
        add_user(client, "other@example.test")
        assert login(client, "other@example.test").status_code == 200

        clock.advance(minutes=15, seconds=1)
        assert login(client, CLINICIAN).status_code == 200
        assert len(audit(client, "login_rate_limited")) == 1


def test_a_successful_sign_in_resets_the_account_count(settings: Settings) -> None:
    with running_app(settings, login_as=None) as client:
        add_user(client, CLINICIAN)
        for _ in range(2):
            for _ in range(4):
                login(client, CLINICIAN, "wrong-password-entirely")
            assert login(client, CLINICIAN).status_code == 200


def test_one_address_is_limited_across_many_accounts(settings: Settings) -> None:
    with running_app(settings, login_as=None) as client:
        add_user(client, CLINICIAN)
        for n in range(20):
            assert login(client, f"guess{n}@example.test").status_code == 401
        response = login(client, CLINICIAN)
    assert response.status_code == 429


def test_typing_something_that_is_not_an_email_counts_only_against_the_address(settings: Settings) -> None:
    settings = settings.model_copy(update={"login_max_failures_per_ip": 3})
    with running_app(settings, login_as=None) as client:
        for _ in range(3):
            login(client, "my secret password typed in the wrong box")
        failures = audit(client, "login_failed")
        assert login(client, "anything@example.test").status_code == 429
    assert all(row["subject_hash"] is None for row in failures)


def test_failure_counts_survive_a_restart(settings: Settings) -> None:
    with running_app(settings, login_as=None) as client:
        add_user(client, CLINICIAN)
        for _ in range(5):
            login(client, CLINICIAN, "wrong-password-entirely")
    with running_app(settings, login_as=None) as client:
        assert login(client, CLINICIAN).status_code == 429


# --- cross-site request checks ------------------------------------------------------------


def test_requests_from_the_app_are_accepted_with_a_referer_when_origin_is_absent(settings: Settings) -> None:
    with running_app(settings, login_as=None) as client:
        add_user(client, CLINICIAN)
        response = client.post(
            "/api/auth/login",
            json={"email": CLINICIAN, "password": PASSWORD},
            headers={"Origin": "", "Referer": "http://testserver/some/page", "X-AirSOAP-Request": "1"},
        )
    assert response.status_code == 200


def test_rejected_cross_site_requests_are_audited_without_their_content(settings: Settings) -> None:
    with running_app(settings, login_as=None) as client:
        add_user(client, CLINICIAN)
        responses = [
            client.post(
                "/api/auth/login",
                json={"email": CLINICIAN, "password": PASSWORD},
                headers={"Origin": origin, "X-AirSOAP-Request": "1"},
            )
            for origin in ("null", "https://evil.example", "http://testserver.evil.example")
        ]
        events = audit(client, "request_rejected")
        accounts = audit(client, "login_succeeded")
    assert {r.status_code for r in responses} == {403}
    assert [e["detail"] for e in events] == ["missing_origin", "foreign_origin", "foreign_origin"]
    assert accounts == []  # the sign-in itself never ran


def test_reading_needs_no_app_header(settings: Settings) -> None:
    with running_app(settings) as client:
        response = client.get("/api/auth/me", headers={"Origin": "", "X-AirSOAP-Request": ""})
    assert response.status_code == 200


def test_the_server_never_grants_cross_origin_access(settings: Settings) -> None:
    with running_app(settings) as client:
        preflight = client.options(
            "/api/recording/start",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
        response = client.get("/api/auth/me", headers={"Origin": "https://evil.example"})
    for answer in (preflight, response):
        assert "access-control-allow-origin" not in answer.headers
        assert "access-control-allow-credentials" not in answer.headers


# --- ownership and clean-up ---------------------------------------------------------------


def second_clinician(client: TestClient) -> tuple[str, str]:
    """Sign in a second clinician; returns (first clinician's token, second's). Leaves the jar empty."""
    first = token(client)
    client.cookies.clear()
    add_user(client, "second@example.test")
    login(client, "second@example.test")
    second = token(client)
    client.cookies.clear()
    return first, second


def test_one_clinician_cannot_reach_anothers_transcription(settings: Settings) -> None:
    with running_app(settings, recorder=FakeRecorder()) as client:
        job_id = record_once(client)["jobId"]
        mine, theirs = second_clinician(client)
        seen = client.get(f"/api/transcriptions/{job_id}", headers=with_cookie(theirs))
        cancel = client.post(f"/api/transcriptions/{job_id}/cancel", headers=with_cookie(theirs))
        own = client.get(f"/api/transcriptions/{job_id}", headers=with_cookie(mine))
    assert seen.status_code == 404
    assert cancel.status_code == 404
    assert seen.json() == {
        "error": {
            "code": "JOB_NOT_FOUND",
            "message": seen.json()["error"]["message"],
            "retryable": False,
        }
    }
    assert own.status_code == 200


def test_one_clinician_cannot_stop_or_cancel_anothers_recording(settings: Settings) -> None:
    with running_app(settings) as client:
        recording_id = client.post("/api/recording/start").json()["recordingId"]
        _mine, theirs = second_clinician(client)
        for action in ("stop", "cancel"):
            response = client.post(
                f"/api/recording/{action}", json={"recordingId": recording_id}, headers=with_cookie(theirs)
            )
            # The same answer as for a recording that does not exist.
            assert response.status_code == 409, response.text
            assert error_code(response) == "NO_ACTIVE_RECORDING"
        assert client.app.state.services.sessions.active_owner is not None


def test_one_clinician_cannot_reach_anothers_ai_job(settings: Settings) -> None:
    from helpers import soap_payload

    with running_app(settings) as client:
        job_id = client.post("/api/ai/soap", json=soap_payload()).json()["jobId"]
        mine, theirs = second_clinician(client)
        seen = client.get(f"/api/ai/jobs/{job_id}", headers=with_cookie(theirs))
        cancel = client.post(f"/api/ai/jobs/{job_id}/cancel", headers=with_cookie(theirs))
        delete = client.delete(f"/api/ai/jobs/{job_id}", headers=with_cookie(theirs))
        own = client.get(f"/api/ai/jobs/{job_id}", headers=with_cookie(mine))
    assert seen.status_code == 404
    assert cancel.status_code == 404
    assert delete.status_code == 204
    assert own.status_code == 200  # the other account's delete did nothing


def test_signing_out_discards_the_recording_and_results(settings: Settings) -> None:
    from helpers import audio_files, soap_payload

    with running_app(settings, recorder=FakeRecorder()) as client:
        job_id = record_once(client)["jobId"]
        wait_for_status(client, job_id, {"completed"})
        ai_job = client.post("/api/ai/soap", json=soap_payload()).json()["jobId"]
        client.post("/api/recording/start")
        services = client.app.state.services
        assert services.sessions.active_owner is not None

        client.post("/api/auth/logout")
        assert services.sessions.active_owner is None
        assert services.jobs.owners() == set()
        assert services.ai_jobs.owners() == set()
        assert audio_files(settings) == []

        login(client, CLINICIAN)
        assert client.get(f"/api/transcriptions/{job_id}").status_code == 404
        assert client.get(f"/api/ai/jobs/{ai_job}").status_code == 404


def test_work_of_an_expired_session_is_discarded_in_the_background(settings: Settings) -> None:
    import time

    clock = Clock()
    settings = settings.model_copy(update={"session_sweep_seconds": 0.02})
    with running_app(settings, auth_clock=clock) as client:
        client.post("/api/recording/start")
        services = client.app.state.services
        time.sleep(0.1)
        assert services.sessions.active_owner is not None  # still signed in: kept

        clock.advance(minutes=31)
        deadline = time.monotonic() + 3
        while services.sessions.active_owner is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert services.sessions.active_owner is None


# --- what must never be written down ------------------------------------------------------


def test_no_password_token_or_clinical_text_reaches_logs_audit_or_the_database(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    from helpers import NOTE, VISIT, review_payload, soap_payload, wait_for_ai_job

    caplog.set_level(logging.DEBUG)
    with running_app(settings, login_as=None, recorder=FakeRecorder()) as client:
        add_user(client, CLINICIAN, must_change_password=True)
        login(client, CLINICIAN, "a-wrong-synthetic-guess")
        login(client, CLINICIAN)
        tokens = [token(client)]
        client.post("/api/auth/password", json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD})
        tokens.append(token(client))
        job_id = record_once(client)["jobId"]
        transcript = wait_for_status(client, job_id, {"completed"})["transcript"]["text"]
        soap = client.post("/api/ai/soap", json=soap_payload()).json()["jobId"]
        wait_for_ai_job(client, soap, {"completed"})
        review = client.post("/api/ai/clinical-review", json=review_payload()).json()["jobId"]
        wait_for_ai_job(client, review, {"completed"})
        client.post("/api/auth/logout")
        rows = audit(client)
        db_path = client.app.state.services.db.path

    secrets_and_content = [
        PASSWORD,
        NEW_PASSWORD,
        "a-wrong-synthetic-guess",
        *tokens,
        transcript,
        *(segment["text"] for segment in VISIT),
        *NOTE.values(),
    ]
    stored = b"".join(p.read_bytes() for p in db_path.parent.iterdir() if p.name.startswith(db_path.name))
    audit_text = repr(rows)
    for value in secrets_and_content:
        assert value not in caplog.text, value
        assert value not in audit_text, value
        assert value.encode() not in stored, value
    assert CLINICIAN not in audit_text  # attempted emails are kept only as keyed hashes
    assert {row["event_type"] for row in rows} >= {
        "user_created",
        "login_failed",
        "login_succeeded",
        "password_changed",
        "logout",
    }


def test_api_responses_carry_security_headers_and_are_never_cached(settings: Settings) -> None:
    with running_app(settings) as client:
        response = client.get("/api/auth/me")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "same-origin"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_public_health_says_nothing_about_the_system(settings: Settings) -> None:
    with running_app(settings, login_as=None) as client:
        response = client.get("/api/health", headers={"Origin": "", "X-AirSOAP-Request": ""})
    assert response.json() == {"status": "ok"}


def test_app_headers_constant_matches_the_test_origin(settings: Settings) -> None:
    # Guards the fixtures themselves: tests must exercise the same origin check the app enforces.
    assert APP_HEADERS["Origin"] in settings.resolved_allowed_origins
    assert FAST_HASHER.time_cost == 1
