"""Accounts, sign-in, sessions and their audit trail.

Sessions are opaque random tokens (``secrets.token_urlsafe(32)``, 256 bits). The
database stores only each token's SHA-256 hash, so a copy of the database cannot be
replayed as live sessions. Every request re-checks the session and the account, so
revoking a session or disabling an account takes effect on the next request.

Argon2 hashing is slow on purpose, so it never runs while the database lock is held.
"""

import hashlib
import hmac
import secrets
import sqlite3
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

from ..config import Settings
from ..db import Database
from ..errors import ApiError
from . import audit
from .audit import AuditEvent
from .models import ROLES, IssuedSession, Role, SessionContext, User, normalize_email
from .passwords import Passwords, generate_temporary_password, policy_problem

# Refresh "last seen" at most this often, to keep writes to the Pi's SD card low.
TOUCH_INTERVAL = timedelta(seconds=60)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def invalid_credentials() -> ApiError:
    # One message for unknown accounts, wrong passwords and disabled accounts alike.
    return ApiError(401, "INVALID_CREDENTIALS", "Incorrect email or password.")


def _roles_code(roles: Iterable[str]) -> str:
    return ",".join(sorted(roles)) or "none"


def _clean_display_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not 1 <= len(name) <= 100 or any(not ch.isprintable() for ch in name):
        raise ApiError(400, "INVALID_DISPLAY_NAME", "Enter a name between 1 and 100 characters.")
    return name


def _clean_roles(roles: Iterable[str]) -> tuple[Role, ...]:
    chosen = tuple(role for role in ROLES if role in set(roles))
    if not chosen:
        raise ApiError(400, "ROLE_REQUIRED", "Choose at least one role.")
    return chosen


class AuthService:
    def __init__(
        self,
        *,
        db: Database,
        settings: Settings,
        passwords: Passwords | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._db = db
        self._settings = settings
        self._passwords = passwords or Passwords()
        self._clock = clock
        self._subject_key: bytes | None = None

    # --- helpers ------------------------------------------------------------------------

    def _now(self) -> datetime:
        return self._clock()

    def _subject_hash(self, email: str) -> str:
        if self._subject_key is None:
            with self._db.transaction() as conn:
                row = conn.execute("SELECT value FROM meta WHERE key = 'subject_key'").fetchone()
            self._subject_key = bytes.fromhex(row["value"])
        return hmac.new(self._subject_key, email.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _user_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> User:
        roles = tuple(
            r["role"]
            for r in conn.execute("SELECT role FROM user_roles WHERE user_id = ? ORDER BY role", (row["id"],))
        )
        return User(
            id=row["id"],
            email=row["email"],
            display_name=row["display_name"],
            roles=tuple(role for role in ROLES if role in roles),
            is_active=bool(row["is_active"]),
            must_change_password=bool(row["must_change_password"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_login_at=_parse(row["last_login_at"]),
        )

    def _get_user(self, conn: sqlite3.Connection, user_id: str) -> User | None:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._user_from_row(conn, row) if row else None

    def _require_user(self, conn: sqlite3.Connection, user_id: str) -> User:
        user = self._get_user(conn, user_id)
        if user is None:
            raise ApiError(404, "USER_NOT_FOUND", "This account does not exist.")
        return user

    @staticmethod
    def _initial_role(user: User) -> Role | None:
        """A single-role account goes straight in; otherwise the user chooses (after any password change)."""
        if user.must_change_password or len(user.roles) != 1:
            return None
        return user.roles[0]

    def _create_session(
        self, conn: sqlite3.Connection, user: User, active_role: Role | None
    ) -> IssuedSession:
        now = self._now()
        if active_role is None:
            # Only good for changing a temporary password or choosing a role.
            absolute = now + timedelta(minutes=self._settings.role_selection_minutes)
        else:
            absolute = now + timedelta(hours=self._settings.session_absolute_hours)
        idle = min(now + timedelta(minutes=self._settings.session_idle_minutes), absolute)
        token = secrets.token_urlsafe(32)
        hashed = token_hash(token)
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, active_role, created_at, last_seen_at, idle_expires_at,"
            " absolute_expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (hashed, user.id, active_role, _iso(now), _iso(now), _iso(idle), _iso(absolute)),
        )
        return IssuedSession(token=token, context=SessionContext(hashed, user, active_role, idle))

    def _revoke_user_sessions(self, conn: sqlite3.Connection, user_id: str, reason: str) -> None:
        conn.execute(
            "UPDATE sessions SET revoked_at = ?, revoke_reason = ? WHERE user_id = ? AND revoked_at IS NULL",
            (_iso(self._now()), reason, user_id),
        )

    def _active_admins(self, conn: sqlite3.Connection) -> set[str]:
        rows = conn.execute(
            "SELECT u.id FROM users u JOIN user_roles r ON r.user_id = u.id WHERE r.role = 'admin' AND u.is_active = 1"
        )
        return {row["id"] for row in rows}

    def _record(self, conn: sqlite3.Connection, event: AuditEvent, **fields: str | None) -> None:
        audit.record(conn, event, at=_iso(self._now()), **fields)

    # --- sign-in ------------------------------------------------------------------------

    def _rate_limited(self, conn: sqlite3.Connection, subject: str | None, client_ip: str | None) -> bool:
        window_start = _iso(self._now() - timedelta(minutes=self._settings.login_failure_window_minutes))
        if client_ip is not None:
            per_ip = conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE event_type = ? AND client_ip = ? AND occurred_at >= ?",
                (AuditEvent.LOGIN_FAILED.value, client_ip, window_start),
            ).fetchone()[0]
            if per_ip >= self._settings.login_max_failures_per_ip:
                return True
        if subject is not None:
            last_success = conn.execute(
                "SELECT MAX(occurred_at) FROM audit_events WHERE event_type = ? AND subject_hash = ?",
                (AuditEvent.LOGIN_SUCCEEDED.value, subject),
            ).fetchone()[0]
            since = max(window_start, last_success or "")
            per_account = conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE event_type = ? AND subject_hash = ? AND occurred_at > ?",
                (AuditEvent.LOGIN_FAILED.value, subject, since),
            ).fetchone()[0]
            if per_account >= self._settings.login_max_failures_per_account:
                return True
        return False

    def login(self, raw_email: str, password: str, client_ip: str | None) -> IssuedSession:
        email = normalize_email(raw_email)
        # Something that is not an email (for example a password typed into the email box) is never hashed
        # into the audit table; it only counts against the client's IP address.
        subject = self._subject_hash(email) if email else None

        with self._db.transaction() as conn:
            limited = self._rate_limited(conn, subject, client_ip)
            if limited:
                self._record(conn, AuditEvent.LOGIN_RATE_LIMITED, client_ip=client_ip, subject_hash=subject)
            row = (
                None
                if limited or email is None
                else conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            )
            user = self._user_from_row(conn, row) if row else None
            stored_hash = row["password_hash"] if row else None
        if limited:
            raise ApiError(
                429,
                "TOO_MANY_ATTEMPTS",
                "Too many sign-in attempts. Wait 15 minutes and try again.",
                retryable=True,
            )

        # The slow part, outside the database lock. Unknown accounts do the same amount of work.
        if stored_hash is None:
            self._passwords.burn(password)
            verified = False
        else:
            verified = self._passwords.verify(stored_hash, password)
        refreshed_hash = (
            self._passwords.hash(password)
            if verified and stored_hash and self._passwords.needs_rehash(stored_hash)
            else None
        )

        failure = None
        if user is None or not verified:
            failure = "bad_credentials"
        elif not user.is_active:
            failure = "inactive"
        elif not user.roles:
            failure = "no_role"

        with self._db.transaction() as conn:
            if failure is not None:
                self._record(
                    conn,
                    AuditEvent.LOGIN_FAILED,
                    target=user.id if user else None,
                    client_ip=client_ip,
                    subject_hash=subject,
                    detail=failure,
                )
            else:
                assert user is not None  # noqa: S101
                now = _iso(self._now())
                conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, user.id))
                if refreshed_hash:
                    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (refreshed_hash, user.id))
                issued = self._create_session(conn, user, self._initial_role(user))
                self._record(
                    conn,
                    AuditEvent.LOGIN_SUCCEEDED,
                    target=user.id,
                    client_ip=client_ip,
                    subject_hash=subject,
                )
        if failure is not None:
            raise invalid_credentials()
        return issued

    # --- sessions -----------------------------------------------------------------------

    def authenticate(self, token: str, client_ip: str | None) -> tuple[SessionContext | None, str]:
        """The session for this cookie value, or (None, reason). Reasons: unknown, revoked, expired, disabled."""
        hashed = token_hash(token)
        now = self._now()
        with self._db.transaction() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE token_hash = ?", (hashed,)).fetchone()
            if row is None:
                return None, "unknown"
            user = self._get_user(conn, row["user_id"])
            reason = None
            if user is None or not user.is_active:
                # Checked first so the person is told why, even though disabling already revoked this session.
                self._revoke_user_sessions(conn, row["user_id"], "disabled")
                reason = "disabled"
            elif row["revoked_at"] is not None:
                reason = "revoked"
            elif now >= datetime.fromisoformat(row["idle_expires_at"]) or now >= datetime.fromisoformat(
                row["absolute_expires_at"]
            ):
                reason = "expired"
            elif row["active_role"] is not None and row["active_role"] not in user.roles:
                conn.execute(
                    "UPDATE sessions SET revoked_at = ?, revoke_reason = 'role_removed' WHERE token_hash = ?",
                    (_iso(now), hashed),
                )
                reason = "revoked"
            if reason is not None:
                self._record(
                    conn,
                    AuditEvent.SESSION_REJECTED,
                    target=row["user_id"],
                    client_ip=client_ip,
                    detail=reason,
                )
                return None, reason

            assert user is not None  # noqa: S101
            absolute = datetime.fromisoformat(row["absolute_expires_at"])
            idle = datetime.fromisoformat(row["idle_expires_at"])
            if now - datetime.fromisoformat(row["last_seen_at"]) >= TOUCH_INTERVAL:
                idle = min(now + timedelta(minutes=self._settings.session_idle_minutes), absolute)
                conn.execute(
                    "UPDATE sessions SET last_seen_at = ?, idle_expires_at = ? WHERE token_hash = ?",
                    (_iso(now), _iso(idle), hashed),
                )
            return SessionContext(hashed, user, row["active_role"], min(idle, absolute)), "ok"

    def select_role(self, ctx: SessionContext, role: Role, client_ip: str | None) -> IssuedSession:
        """Choose admin or clinician for this sign-in. Issues a fresh session token (privilege change)."""
        if ctx.user.must_change_password:
            raise ApiError(403, "PASSWORD_CHANGE_REQUIRED", "Change your temporary password to continue.")
        if ctx.active_role is not None:
            raise ApiError(409, "ROLE_ALREADY_SELECTED", "Sign out to switch roles.")
        if role not in ctx.user.roles:
            raise ApiError(403, "FORBIDDEN", "Your account does not have this role.")
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE sessions SET revoked_at = ?, revoke_reason = 'role_selected' WHERE token_hash = ?",
                (_iso(self._now()), ctx.token_hash),
            )
            issued = self._create_session(conn, ctx.user, role)
            self._record(conn, AuditEvent.ROLE_SELECTED, target=ctx.user.id, client_ip=client_ip, detail=role)
        return issued

    def logout(self, token: str, client_ip: str | None) -> str | None:
        """Revoke the session. Returns the account ID it belonged to, if it was live."""
        hashed = token_hash(token)
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT user_id FROM sessions WHERE token_hash = ? AND revoked_at IS NULL", (hashed,)
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE sessions SET revoked_at = ?, revoke_reason = 'logout' WHERE token_hash = ?",
                (_iso(self._now()), hashed),
            )
            self._record(conn, AuditEvent.LOGOUT, target=row["user_id"], client_ip=client_ip)
            return row["user_id"]

    def user_has_live_session(self, user_id: str) -> bool:
        now = _iso(self._now())
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT 1 FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.user_id = ? AND u.is_active = 1"
                " AND s.revoked_at IS NULL AND s.idle_expires_at > ? AND s.absolute_expires_at > ? LIMIT 1",
                (user_id, now, now),
            ).fetchone()
        return row is not None

    def record_access_denied(self, ctx: SessionContext, client_ip: str | None, detail: str) -> None:
        with self._db.transaction() as conn:
            self._record(
                conn, AuditEvent.ACCESS_DENIED, actor=ctx.user.id, client_ip=client_ip, detail=detail
            )

    def record_rejected_request(self, client_ip: str | None, detail: str) -> None:
        with self._db.transaction() as conn:
            self._record(conn, AuditEvent.REQUEST_REJECTED, client_ip=client_ip, detail=detail)

    # --- passwords ----------------------------------------------------------------------

    def change_password(
        self, ctx: SessionContext, current: str, new: str, client_ip: str | None
    ) -> IssuedSession:
        with self._db.transaction() as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (ctx.user.id,)).fetchone()
        if not self._passwords.verify(row["password_hash"], current):
            with self._db.transaction() as conn:
                self._record(conn, AuditEvent.PASSWORD_CHANGE_FAILED, target=ctx.user.id, client_ip=client_ip)
            raise ApiError(400, "WRONG_PASSWORD", "The current password is incorrect.")
        if (problem := policy_problem(new, ctx.user.email)) is not None:
            raise ApiError(400, "WEAK_PASSWORD", problem)
        if new == current:
            raise ApiError(400, "WEAK_PASSWORD", "Choose a password different from the current one.")
        new_hash = self._passwords.hash(new)
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0, updated_at = ? WHERE id = ?",
                (new_hash, _iso(self._now()), ctx.user.id),
            )
            # Every other session of this account ends; this browser gets a fresh one.
            self._revoke_user_sessions(conn, ctx.user.id, "password_changed")
            user = self._require_user(conn, ctx.user.id)
            issued = self._create_session(conn, user, self._initial_role(user))
            self._record(conn, AuditEvent.PASSWORD_CHANGED, target=user.id, client_ip=client_ip)
        return issued

    # --- administration -----------------------------------------------------------------

    def list_users(self) -> list[User]:
        with self._db.transaction() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY display_name COLLATE NOCASE, email").fetchall()
            return [self._user_from_row(conn, row) for row in rows]

    def _insert_user(
        self,
        conn: sqlite3.Connection,
        email: str,
        display_name: str,
        roles: tuple[Role, ...],
        password_hash: str,
        must_change_password: bool,
    ) -> User:
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise ApiError(409, "EMAIL_TAKEN", "An account with this email already exists.")
        now = _iso(self._now())
        user_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO users (id, email, display_name, password_hash, is_active, must_change_password, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
            (user_id, email, display_name, password_hash, int(must_change_password), now, now),
        )
        conn.executemany(
            "INSERT INTO user_roles (user_id, role) VALUES (?, ?)", [(user_id, r) for r in roles]
        )
        return self._require_user(conn, user_id)

    def create_user(
        self,
        actor: SessionContext,
        raw_email: str,
        display_name: str,
        roles: Iterable[str],
        client_ip: str | None,
    ) -> tuple[User, str]:
        """Create an account with a temporary password (returned once; it must be changed at first sign-in)."""
        email = normalize_email(raw_email)
        if email is None:
            raise ApiError(400, "INVALID_EMAIL", "Enter a valid email address.")
        name = _clean_display_name(display_name)
        chosen = _clean_roles(roles)
        temporary = generate_temporary_password()
        hashed = self._passwords.hash(temporary)
        with self._db.transaction() as conn:
            user = self._insert_user(conn, email, name, chosen, hashed, must_change_password=True)
            self._record(
                conn,
                AuditEvent.USER_CREATED,
                actor=actor.user.id,
                target=user.id,
                client_ip=client_ip,
                detail=_roles_code(chosen),
            )
        return user, temporary

    def update_user(
        self,
        actor: SessionContext,
        user_id: str,
        *,
        display_name: str | None = None,
        roles: Iterable[str] | None = None,
        is_active: bool | None = None,
        client_ip: str | None = None,
    ) -> User:
        name = _clean_display_name(display_name) if display_name is not None else None
        new_roles = _clean_roles(roles) if roles is not None else None
        with self._db.transaction() as conn:
            user = self._require_user(conn, user_id)
            if is_active is False and user.id == actor.user.id:
                raise ApiError(409, "CANNOT_DISABLE_SELF", "You cannot disable your own account.")

            will_be_active = user.is_active if is_active is None else is_active
            will_be_admin = "admin" in (new_roles if new_roles is not None else user.roles)
            admins = self._active_admins(conn)
            if user.id in admins and not (will_be_active and will_be_admin) and len(admins) <= 1:
                raise ApiError(
                    409,
                    "LAST_ADMIN",
                    "This is the last active administrator. Make another account an administrator first.",
                )

            now = _iso(self._now())
            if name is not None and name != user.display_name:
                conn.execute(
                    "UPDATE users SET display_name = ?, updated_at = ? WHERE id = ?", (name, now, user.id)
                )
                self._record(
                    conn, AuditEvent.USER_UPDATED, actor=actor.user.id, target=user.id, client_ip=client_ip
                )
            if new_roles is not None and set(new_roles) != set(user.roles):
                conn.execute("DELETE FROM user_roles WHERE user_id = ?", (user.id,))
                conn.executemany(
                    "INSERT INTO user_roles (user_id, role) VALUES (?, ?)", [(user.id, r) for r in new_roles]
                )
                conn.execute("UPDATE users SET updated_at = ? WHERE id = ?", (now, user.id))
                self._revoke_user_sessions(conn, user.id, "roles_changed")
                self._record(
                    conn,
                    AuditEvent.ROLES_CHANGED,
                    actor=actor.user.id,
                    target=user.id,
                    client_ip=client_ip,
                    detail=f"{_roles_code(user.roles)}>{_roles_code(new_roles)}",
                )
            if is_active is not None and is_active != user.is_active:
                conn.execute(
                    "UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?",
                    (int(is_active), now, user.id),
                )
                if not is_active:
                    self._revoke_user_sessions(conn, user.id, "disabled")
                self._record(
                    conn,
                    AuditEvent.USER_ENABLED if is_active else AuditEvent.USER_DISABLED,
                    actor=actor.user.id,
                    target=user.id,
                    client_ip=client_ip,
                )
            return self._require_user(conn, user.id)

    def reset_password(self, actor: SessionContext, user_id: str, client_ip: str | None) -> tuple[User, str]:
        temporary = generate_temporary_password()
        hashed = self._passwords.hash(temporary)
        with self._db.transaction() as conn:
            user = self._require_user(conn, user_id)
            conn.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 1, updated_at = ? WHERE id = ?",
                (hashed, _iso(self._now()), user.id),
            )
            self._revoke_user_sessions(conn, user.id, "password_reset")
            self._record(
                conn, AuditEvent.PASSWORD_RESET, actor=actor.user.id, target=user.id, client_ip=client_ip
            )
            return self._require_user(conn, user.id), temporary

    # --- operator commands (run on the Pi with the CLI; no network access) ----------------

    def operator_create_user(
        self,
        raw_email: str,
        display_name: str,
        password: str,
        roles: Iterable[str],
        *,
        must_change_password: bool = False,
        event: AuditEvent = AuditEvent.USER_CREATED,
    ) -> User:
        """Create an account with a password chosen by the operator (CLI on the Pi; also used by tests)."""
        email = normalize_email(raw_email)
        if email is None:
            raise ApiError(400, "INVALID_EMAIL", "Enter a valid email address.")
        if (problem := policy_problem(password, email)) is not None:
            raise ApiError(400, "WEAK_PASSWORD", problem)
        name = _clean_display_name(display_name)
        chosen = _clean_roles(roles)
        hashed = self._passwords.hash(password)
        with self._db.transaction() as conn:
            user = self._insert_user(
                conn, email, name, chosen, hashed, must_change_password=must_change_password
            )
            self._record(conn, event, target=user.id, detail=f"cli.{_roles_code(chosen)}")
        return user

    def bootstrap_admin(
        self, raw_email: str, display_name: str, password: str, *, also_clinician: bool
    ) -> User:
        roles: tuple[Role, ...] = ("admin", "clinician") if also_clinician else ("admin",)
        return self.operator_create_user(
            raw_email, display_name, password, roles, event=AuditEvent.ADMIN_BOOTSTRAPPED
        )

    def find_user_by_email(self, raw_email: str) -> User | None:
        email = normalize_email(raw_email)
        if email is None:
            return None
        with self._db.transaction() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            return self._user_from_row(conn, row) if row else None

    def operator_set_password(self, user_id: str, password: str) -> None:
        with self._db.transaction() as conn:
            user = self._require_user(conn, user_id)
        if (problem := policy_problem(password, user.email)) is not None:
            raise ApiError(400, "WEAK_PASSWORD", problem)
        hashed = self._passwords.hash(password)
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0, updated_at = ? WHERE id = ?",
                (hashed, _iso(self._now()), user_id),
            )
            self._revoke_user_sessions(conn, user_id, "password_reset")
            self._record(conn, AuditEvent.PASSWORD_RESET, target=user_id, detail="cli")

    def operator_enable(self, user_id: str) -> None:
        with self._db.transaction() as conn:
            self._require_user(conn, user_id)
            conn.execute(
                "UPDATE users SET is_active = 1, updated_at = ? WHERE id = ?", (_iso(self._now()), user_id)
            )
            self._record(conn, AuditEvent.USER_ENABLED, target=user_id, detail="cli")

    def operator_grant_role(self, user_id: str, role: Role) -> None:
        with self._db.transaction() as conn:
            user = self._require_user(conn, user_id)
            if role in user.roles:
                return
            conn.execute("INSERT INTO user_roles (user_id, role) VALUES (?, ?)", (user_id, role))
            self._revoke_user_sessions(conn, user_id, "roles_changed")
            self._record(
                conn,
                AuditEvent.ROLES_CHANGED,
                target=user_id,
                detail=f"{_roles_code(user.roles)}>{_roles_code((*user.roles, role))}",
            )

    # --- housekeeping -------------------------------------------------------------------

    def prune(self) -> None:
        now = self._now()
        stale = _iso(now - timedelta(days=1))
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE (revoked_at IS NOT NULL AND revoked_at < ?) OR absolute_expires_at < ?",
                (stale, stale),
            )
            conn.execute(
                "DELETE FROM audit_events WHERE occurred_at < ?",
                (_iso(now - timedelta(days=self._settings.audit_retention_days)),),
            )
