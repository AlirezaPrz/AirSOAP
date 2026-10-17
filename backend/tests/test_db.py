"""The account database: location rules, file permissions, migrations, backups and pruning."""

import os
import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from helpers import FAST_HASHER, PASSWORD

from airsoap import config
from airsoap.auth.service import AuthService
from airsoap.config import Settings
from airsoap.db import Database, DatabaseError, migrations
from airsoap.db import connection as db_connection


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "state" / "airsoap.db")
    database.open()
    yield database
    database.close()


# --- where the database may live ----------------------------------------------------------


def test_the_database_path_must_be_absolute() -> None:
    with pytest.raises(ValueError, match="absolute"):
        Settings(database_path=Path("relative/airsoap.db"))


def test_a_temporary_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="persistent storage"):
        Settings(database_path=tmp_path / "airsoap.db", allow_ephemeral_database=False)
    with pytest.raises(ValueError, match="persistent storage"):
        Settings(database_path=Path("/tmp/airsoap/airsoap.db"), allow_ephemeral_database=False)  # noqa: S108


def test_the_audio_work_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="work_dir"):
        Settings(
            work_dir=Path.home() / "airsoap-work",
            database_path=Path.home() / "airsoap-work" / "airsoap.db",
            allow_ephemeral_database=False,
        )


def test_a_home_directory_path_is_accepted() -> None:
    target = Path.home() / ".local" / "state" / "airsoap" / "airsoap.db"
    assert Settings(database_path=target, allow_ephemeral_database=False).database_path == target


def test_the_default_location_is_persistent_per_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # the real default, not the test guard's (nothing is created here)
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    assert config.default_database_path() == Path.home() / ".local" / "state" / "airsoap" / "airsoap.db"
    monkeypatch.setattr(config.sys, "platform", "darwin")
    assert "Application Support" in str(config.default_database_path())


def test_linux_defaults_to_secure_cookies_and_the_pi_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_is_mac", lambda: False)
    settings = Settings()
    assert settings.resolved_cookie_secure is True
    assert settings.resolved_allowed_origins == {"https://raspberrypi.local"}


def test_allowed_origins_must_be_bare_origins() -> None:
    assert Settings(
        allowed_origins="https://pi.example, https://raspberrypi.local/"
    ).resolved_allowed_origins == {
        "https://pi.example",
        "https://raspberrypi.local",
    }
    for bad in ("raspberrypi.local", "https://raspberrypi.local/app", "ftp://x.example"):
        with pytest.raises(ValueError):
            Settings(allowed_origins=bad)


# --- files and permissions ----------------------------------------------------------------


def test_files_are_owner_only(db: Database) -> None:
    with db.transaction() as conn:
        conn.execute("INSERT INTO meta (key, value) VALUES ('probe', 'x')")
    assert mode(db.path) == 0o600
    assert mode(db.path.parent) == 0o700
    for sidecar in db.path.parent.iterdir():
        assert mode(sidecar) & 0o077 == 0, sidecar


def test_an_existing_directory_is_tightened(tmp_path: Path) -> None:
    directory = tmp_path / "loose"
    directory.mkdir(mode=0o755)
    database = Database(directory / "airsoap.db")
    database.open()
    database.close()
    assert mode(directory) == 0o700


def test_a_symlinked_database_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real.db"
    real.touch()
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "airsoap.db").symlink_to(real)
    with pytest.raises(DatabaseError, match="symlink"):
        Database(tmp_path / "state" / "airsoap.db").open()


def test_a_database_owned_by_someone_else_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "state").mkdir()
    someone_else = os.getuid() + 1
    monkeypatch.setattr(db_connection.os, "getuid", lambda: someone_else)
    with pytest.raises(DatabaseError, match="owned by another user"):
        Database(tmp_path / "state" / "airsoap.db").open()


def test_the_app_refuses_to_start_on_an_unsafe_database(settings: Settings, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from airsoap.main import create_app

    real = tmp_path / "elsewhere.db"
    real.touch()
    settings.database_path.parent.mkdir(parents=True)
    settings.database_path.symlink_to(real)
    with pytest.raises(DatabaseError), TestClient(create_app(settings, password_hasher=FAST_HASHER)):
        pass


# --- migrations ---------------------------------------------------------------------------


def test_a_new_database_gets_the_current_schema(db: Database) -> None:
    with db.transaction() as conn:
        assert migrations.current_version(conn) == migrations.SCHEMA_VERSION
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert {"users", "user_roles", "sessions", "audit_events", "meta"} <= tables


def test_reopening_changes_nothing(db: Database) -> None:
    with db.transaction() as conn:
        key = conn.execute("SELECT value FROM meta WHERE key = 'subject_key'").fetchone()[0]
    db.close()
    db.open()
    with db.transaction() as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'subject_key'").fetchone()[0] == key
        assert migrations.migrate(conn) == 0


def test_a_database_from_a_newer_version_is_refused(db: Database) -> None:
    with db.transaction() as conn:
        conn.execute(f"PRAGMA user_version = {migrations.SCHEMA_VERSION + 1}")
    db.close()
    with pytest.raises(migrations.SchemaTooNewError):
        db.open()


def test_a_failing_migration_leaves_the_database_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE half_done (x INTEGER)")
        raise RuntimeError("synthetic migration failure")

    database = Database(tmp_path / "state" / "airsoap.db")
    database.open()
    database.close()
    monkeypatch.setattr(migrations, "MIGRATIONS", [*migrations.MIGRATIONS, broken])
    monkeypatch.setattr(migrations, "SCHEMA_VERSION", len(migrations.MIGRATIONS))
    with pytest.raises(RuntimeError, match="synthetic"):
        database.open()
    conn = sqlite3.connect(database.path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'half_done'").fetchone() is None
    finally:
        conn.close()


def test_roles_are_limited_by_the_schema(db: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
        conn.execute(
            "INSERT INTO users (id, email, display_name, password_hash, is_active, must_change_password,"
            " created_at, updated_at) VALUES ('u1', 'a@example.test', 'A', 'h', 1, 0, 'now', 'now')"
        )
        conn.execute("INSERT INTO user_roles (user_id, role) VALUES ('u1', 'superuser')")


def test_a_failed_transaction_is_rolled_back(db: Database) -> None:
    with pytest.raises(RuntimeError), db.transaction() as conn:
        conn.execute("INSERT INTO meta (key, value) VALUES ('rolled', 'back')")
        raise RuntimeError
    with db.transaction() as conn:
        assert conn.execute("SELECT 1 FROM meta WHERE key = 'rolled'").fetchone() is None


# --- backup and housekeeping --------------------------------------------------------------


def test_backup_is_a_consistent_owner_only_copy(db: Database, tmp_path: Path, settings: Settings) -> None:
    from airsoap.auth.passwords import Passwords

    auth = AuthService(db=db, settings=settings, passwords=Passwords(FAST_HASHER))
    auth.operator_create_user("doc@example.test", "Dr Doc", PASSWORD, ["clinician"])
    destination = tmp_path / "backups" / "airsoap-backup.db"
    destination.parent.mkdir()
    db.backup(destination)
    assert mode(destination) == 0o600
    copy = sqlite3.connect(destination)
    try:
        assert copy.execute("SELECT email FROM users").fetchall() == [("doc@example.test",)]
    finally:
        copy.close()
    with pytest.raises(DatabaseError, match="already exists"):
        db.backup(destination)


def test_old_sessions_and_audit_events_are_pruned(db: Database, settings: Settings) -> None:
    from airsoap.auth.passwords import Passwords

    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    auth = AuthService(db=db, settings=settings, passwords=Passwords(FAST_HASHER), clock=lambda: now[0])
    auth.operator_create_user("doc@example.test", "Dr Doc", PASSWORD, ["clinician"])
    auth.login("doc@example.test", PASSWORD, "127.0.0.1")
    now[0] += timedelta(days=settings.audit_retention_days + 1)
    auth.prune()
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
