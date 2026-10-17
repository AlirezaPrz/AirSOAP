"""Schema versions, tracked with SQLite's ``PRAGMA user_version``.

Each entry in ``MIGRATIONS`` upgrades the schema by one version and runs inside
a transaction together with the version bump, so a failed migration leaves the
database at the previous version. Never edit a released migration: append a new one.

The database holds staff accounts, sessions and security audit events only.
There are deliberately no patient, encounter, transcript or note tables.
"""

import secrets
import sqlite3
from collections.abc import Callable

Migration = Callable[[sqlite3.Connection], None]


# token_hash is the SHA-256 of the cookie value; the raw token is never stored.
# active_role is NULL until the user has chosen a role (and changed a temporary password).
# audit_events.event_type and detail hold fixed codes only, never free text or user input.
_V1_STATEMENTS = (
    """
    CREATE TABLE users (
        id                   TEXT PRIMARY KEY,
        email                TEXT NOT NULL UNIQUE,
        display_name         TEXT NOT NULL,
        password_hash        TEXT NOT NULL,
        is_active            INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
        must_change_password INTEGER NOT NULL DEFAULT 1 CHECK (must_change_password IN (0, 1)),
        created_at           TEXT NOT NULL,
        updated_at           TEXT NOT NULL,
        last_login_at        TEXT
    )
    """,
    """
    CREATE TABLE user_roles (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role    TEXT NOT NULL CHECK (role IN ('admin', 'clinician')),
        PRIMARY KEY (user_id, role)
    )
    """,
    """
    CREATE TABLE sessions (
        token_hash          TEXT PRIMARY KEY,
        user_id             TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        active_role         TEXT CHECK (active_role IN ('admin', 'clinician')),
        created_at          TEXT NOT NULL,
        last_seen_at        TEXT NOT NULL,
        idle_expires_at     TEXT NOT NULL,
        absolute_expires_at TEXT NOT NULL,
        revoked_at          TEXT,
        revoke_reason       TEXT
    )
    """,
    """
    CREATE INDEX sessions_by_user ON sessions(user_id)
    """,
    """
    CREATE TABLE audit_events (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        occurred_at    TEXT NOT NULL,
        event_type     TEXT NOT NULL,
        actor_user_id  TEXT,
        target_user_id TEXT,
        client_ip      TEXT,
        subject_hash   TEXT,
        detail         TEXT
    )
    """,
    """
    CREATE INDEX audit_by_time ON audit_events(occurred_at)
    """,
    """
    CREATE INDEX audit_by_subject ON audit_events(event_type, subject_hash, occurred_at)
    """,
    """
    CREATE INDEX audit_by_ip ON audit_events(event_type, client_ip, occurred_at)
    """,
    """
    CREATE TABLE meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
)


def _v1_accounts_sessions_audit(conn: sqlite3.Connection) -> None:
    # One statement at a time: executescript() would commit the surrounding transaction.
    for statement in _V1_STATEMENTS:
        conn.execute(statement)
    # Key for hashing attempted sign-in emails (rate limiting without storing what was typed).
    conn.execute("INSERT INTO meta (key, value) VALUES ('subject_key', ?)", (secrets.token_hex(32),))


MIGRATIONS: list[Migration] = [_v1_accounts_sessions_audit]

SCHEMA_VERSION = len(MIGRATIONS)


class SchemaTooNewError(RuntimeError):
    """The database was written by a newer AirSOAP than this one."""


def current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> int:
    """Bring the schema up to date. Returns the number of migrations applied."""
    version = current_version(conn)
    if version > SCHEMA_VERSION:
        raise SchemaTooNewError(f"database schema {version} is newer than this code ({SCHEMA_VERSION})")
    applied = 0
    for target in range(version + 1, SCHEMA_VERSION + 1):
        conn.execute("BEGIN IMMEDIATE")
        try:
            MIGRATIONS[target - 1](conn)
            conn.execute(f"PRAGMA user_version = {target}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        applied += 1
    return applied
