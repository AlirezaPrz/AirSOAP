"""The operator CLI (python -m airsoap.admin). Passwords come from a patched getpass."""

import sqlite3
from pathlib import Path

import pytest
from helpers import FAST_HASHER

from airsoap import admin
from airsoap.auth.passwords import Passwords
from airsoap.auth.service import AuthService
from airsoap.config import Settings
from airsoap.db import Database

ADMIN_EMAIL = "operator@example.test"
ADMIN_PASSWORD = "synthetic-admin-pass-9"


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "state" / "airsoap.db"
    monkeypatch.setenv("AIRSOAP_DATABASE_PATH", str(path))
    # The CLI's real Argon2 settings are slow by design; tests use the cheapest.
    monkeypatch.setattr(
        admin, "AuthService", lambda **kw: AuthService(**kw, passwords=Passwords(FAST_HASHER))
    )
    return path


def answers(monkeypatch: pytest.MonkeyPatch, *, passwords: list[str], typed: list[str] | None = None) -> None:
    hidden = iter(passwords)
    visible = iter(typed or [])
    monkeypatch.setattr(admin.getpass, "getpass", lambda _prompt="": next(hidden))
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(visible))


def run(*argv: str) -> int:
    return admin.main(["--no-env-file", *argv])


def read_users(db_path: Path) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT u.email, u.is_active, u.must_change_password, group_concat(r.role) FROM users u"
            " LEFT JOIN user_roles r ON r.user_id = u.id GROUP BY u.id ORDER BY u.email"
        ).fetchall()
    finally:
        conn.close()


def create_admin(monkeypatch: pytest.MonkeyPatch, *extra: str) -> int:
    answers(monkeypatch, passwords=[ADMIN_PASSWORD, ADMIN_PASSWORD])
    return run("create-admin", "--email", ADMIN_EMAIL, "--name", "Dr Operator", *extra)


def test_create_admin_makes_the_first_administrator(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert create_admin(monkeypatch) == 0
    out = capsys.readouterr()
    assert read_users(db_path) == [(ADMIN_EMAIL, 1, 0, "admin")]
    assert ADMIN_PASSWORD not in out.out + out.err
    conn = sqlite3.connect(db_path)
    try:
        events = conn.execute("SELECT event_type, detail FROM audit_events").fetchall()
    finally:
        conn.close()
    assert events == [("admin_bootstrapped", "cli.admin")]


def test_create_admin_can_add_the_clinician_role(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert create_admin(monkeypatch, "--also-clinician") == 0
    assert read_users(db_path)[0][3] in {"admin,clinician", "clinician,admin"}


def test_a_second_administrator_needs_to_be_asked_for(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    create_admin(monkeypatch)
    answers(monkeypatch, passwords=[ADMIN_PASSWORD, ADMIN_PASSWORD])
    assert run("create-admin", "--email", "second@example.test", "--name", "Dr Two") == 1
    assert "already exists" in capsys.readouterr().err
    answers(monkeypatch, passwords=[ADMIN_PASSWORD, ADMIN_PASSWORD])
    assert run("create-admin", "--email", "second@example.test", "--name", "Dr Two", "--additional") == 0
    assert len(read_users(db_path)) == 2


def test_mismatched_or_weak_passwords_create_nothing(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answers(monkeypatch, passwords=[ADMIN_PASSWORD, ADMIN_PASSWORD + "x"])
    assert run("create-admin", "--email", ADMIN_EMAIL, "--name", "Dr Operator") == 1
    answers(monkeypatch, passwords=["short", "short"])
    assert run("create-admin", "--email", ADMIN_EMAIL, "--name", "Dr Operator") == 1
    answers(monkeypatch, passwords=[ADMIN_EMAIL, ADMIN_EMAIL])
    assert run("create-admin", "--email", ADMIN_EMAIL, "--name", "Dr Operator") == 1
    assert read_users(db_path) == []


def test_an_existing_email_is_refused(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    create_admin(monkeypatch)
    answers(monkeypatch, passwords=[ADMIN_PASSWORD, ADMIN_PASSWORD])
    assert run("create-admin", "--email", ADMIN_EMAIL.upper(), "--name", "Dr Again", "--additional") == 1


def test_list_users_shows_no_secrets(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("list-users") == 0
    assert "No accounts yet" in capsys.readouterr().out
    create_admin(monkeypatch)
    capsys.readouterr()
    assert run("list-users") == 0
    out = capsys.readouterr().out
    assert ADMIN_EMAIL in out and "admin" in out and "never" in out
    assert "argon2" not in out


def test_reset_password_needs_the_email_typed_again(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    create_admin(monkeypatch)
    new = "a-brand-new-synthetic-pass"
    answers(monkeypatch, passwords=[new, new], typed=["someone-else@example.test"])
    assert run("reset-password", ADMIN_EMAIL) == 1
    answers(monkeypatch, passwords=[new, new], typed=[ADMIN_EMAIL])
    assert run("reset-password", ADMIN_EMAIL) == 0

    db = Database(db_path)
    db.open()
    try:
        auth = AuthService(db=db, settings=Settings(database_path=db_path), passwords=Passwords(FAST_HASHER))
        assert auth.login(ADMIN_EMAIL, new, None).context.active_role == "admin"
    finally:
        db.close()


def test_enable_and_grant_role_restore_access(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    create_admin(monkeypatch)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE users SET is_active = 0")
    conn.commit()
    conn.close()
    assert run("enable", ADMIN_EMAIL) == 0
    assert run("grant-role", ADMIN_EMAIL, "clinician") == 0
    assert read_users(db_path)[0][1] == 1
    assert set(read_users(db_path)[0][3].split(",")) == {"admin", "clinician"}
    assert run("enable", "nobody@example.test") == 1


def test_backup_writes_an_owner_only_copy(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    create_admin(monkeypatch)
    destination = tmp_path / "airsoap-backup.db"
    assert run("backup", str(destination)) == 0
    assert destination.stat().st_mode & 0o077 == 0
    assert run("backup", str(destination)) == 1  # never overwrites


def test_migrate_reports_the_schema_version(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("migrate") == 0
    assert "version 1" in capsys.readouterr().out


def test_the_cli_refuses_to_run_as_root(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(admin.os, "geteuid", lambda: 0)
    assert run("list-users") == 2
    assert "not as root" in capsys.readouterr().err
    assert not db_path.exists()


def test_the_cli_refuses_an_ephemeral_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AIRSOAP_DATABASE_PATH", str(tmp_path / "airsoap.db"))
    monkeypatch.setenv("AIRSOAP_ALLOW_EPHEMERAL_DATABASE", "false")
    assert run("list-users") == 2
    assert "persistent storage" in capsys.readouterr().err


def test_the_cli_reads_the_service_settings_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "from-env-file" / "airsoap.db"
    env_file = tmp_path / "airsoap.env"
    env_file.write_text(f"AIRSOAP_DATABASE_PATH={target}\n")
    monkeypatch.delenv("AIRSOAP_DATABASE_PATH", raising=False)
    assert admin.main(["--env-file", str(env_file), "migrate"]) == 0
    assert str(target) in capsys.readouterr().out
    assert target.exists()


def test_cancelling_at_a_prompt_changes_nothing(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(_prompt: str = "") -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(admin.getpass, "getpass", interrupted)
    assert run("create-admin", "--email", ADMIN_EMAIL, "--name", "Dr Operator") == 130
    assert read_users(db_path) == []
