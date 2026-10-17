"""Operator commands, run on the Raspberry Pi as the service account (never over the network).

    cd ~/AirSOAP/backend
    .venv/bin/python -m airsoap.admin create-admin --email you@clinic.example --name "Dr Jane Doe"

Reads the same settings as the service (``~/.config/airsoap/airsoap.env`` by default).
Passwords are typed at a hidden prompt; they never appear on screen, in shell history,
in logs or in the audit table. There is deliberately no network endpoint for any of this.
"""

import argparse
import getpass
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from .auth.models import ROLES
from .auth.passwords import MIN_PASSWORD_LENGTH
from .auth.service import AuthService
from .config import Settings
from .db import Database, DatabaseError
from .db.migrations import SchemaTooNewError, current_version
from .errors import ApiError

DEFAULT_ENV_FILE = Path.home() / ".config" / "airsoap" / "airsoap.env"


class CommandError(Exception):
    """A problem to report to the operator, then exit non-zero."""


def _new_password(label: str = "New password") -> str:
    first = getpass.getpass(f"{label} (at least {MIN_PASSWORD_LENGTH} characters, hidden): ")
    second = getpass.getpass("Type it again: ")
    if first != second:
        raise CommandError("The two passwords did not match. Nothing was changed.")
    return first


def _confirm(expected: str, prompt: str) -> None:
    if input(prompt).strip().lower() != expected.lower():
        raise CommandError("Not confirmed. Nothing was changed.")


def _open(args: argparse.Namespace) -> tuple[Database, AuthService, Settings]:
    env_file = None if args.no_env_file else Path(args.env_file)
    settings = Settings(_env_file=env_file if env_file and env_file.exists() else None)  # type: ignore[call-arg]
    db = Database(settings.database_path)
    db.open()
    return db, AuthService(db=db, settings=settings), settings


def _user_or_fail(auth: AuthService, email: str):
    user = auth.find_user_by_email(email)
    if user is None:
        raise CommandError("No account has that email address.")
    return user


def cmd_create_admin(args: argparse.Namespace, auth: AuthService) -> str:
    admins = [u for u in auth.list_users() if "admin" in u.roles and u.is_active]
    if admins and not args.additional:
        raise CommandError(
            "An active administrator already exists. Nothing was changed. "
            "To add another administrator on purpose, repeat the command with --additional. "
            "To regain access, use reset-password, enable or grant-role."
        )
    password = _new_password("Password for the new administrator")
    user = auth.bootstrap_admin(args.email, args.name, password, also_clinician=args.also_clinician)
    return f"Created administrator {user.email} with roles: {', '.join(user.roles)}."


def cmd_list_users(_args: argparse.Namespace, auth: AuthService) -> str:
    users = auth.list_users()
    if not users:
        return "No accounts yet. Create the first administrator with: create-admin --email ... --name ..."
    lines = [f"{'EMAIL':40} {'ROLES':18} {'ACTIVE':6}  LAST SIGN-IN"]
    for user in users:
        last = user.last_login_at.strftime("%Y-%m-%d %H:%M UTC") if user.last_login_at else "never"
        lines.append(
            f"{user.email:40} {','.join(user.roles):18} {'yes' if user.is_active else 'no':6}  {last}"
        )
    return "\n".join(lines)


def cmd_reset_password(args: argparse.Namespace, auth: AuthService) -> str:
    user = _user_or_fail(auth, args.email)
    _confirm(user.email, f"Type the email address again to reset the password for {user.email}: ")
    auth.operator_set_password(user.id, _new_password())
    return f"Password for {user.email} changed. All of that account's sessions were ended."


def cmd_enable(args: argparse.Namespace, auth: AuthService) -> str:
    user = _user_or_fail(auth, args.email)
    auth.operator_enable(user.id)
    return f"{user.email} is active again."


def cmd_grant_role(args: argparse.Namespace, auth: AuthService) -> str:
    user = _user_or_fail(auth, args.email)
    auth.operator_grant_role(user.id, args.role)
    return f"{user.email} now has the {args.role} role. Its sessions were ended; it must sign in again."


def cmd_backup(args: argparse.Namespace, db: Database) -> str:
    destination = Path(args.destination).expanduser().resolve()
    db.backup(destination)
    return f"Backup written to {destination} (owner-only). Store it as carefully as the database itself."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m airsoap.admin", description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--env-file", default=str(DEFAULT_ENV_FILE), help="settings file (default: %(default)s)"
    )
    parser.add_argument("--no-env-file", action="store_true", help="use only environment variables")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-admin", help="create the first administrator (prompts for the password)")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True, help='display name, e.g. "Dr Jane Doe"')
    create.add_argument(
        "--also-clinician", action="store_true", help="also give this account the clinician role"
    )
    create.add_argument("--additional", action="store_true", help="allow creating another administrator")

    sub.add_parser("list-users", help="list accounts (no passwords or secrets)")

    reset = sub.add_parser("reset-password", help="set a new password for an account (ends its sessions)")
    reset.add_argument("email")

    enable = sub.add_parser("enable", help="re-enable a disabled account")
    enable.add_argument("email")

    grant = sub.add_parser("grant-role", help="add a role to an account, e.g. to restore an administrator")
    grant.add_argument("email")
    grant.add_argument("role", choices=ROLES)

    backup = sub.add_parser("backup", help="write a consistent copy of the database (safe while running)")
    backup.add_argument("destination")

    sub.add_parser("migrate", help="apply any pending database migrations and show the schema version")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if os.geteuid() == 0:
        print(
            "Run this as the AirSOAP service account, not as root (root would own the database).",
            file=sys.stderr,
        )
        return 2
    os.umask(0o077)  # anything created (database, backups) is owner-only
    try:
        db, auth, _settings = _open(args)
    except (DatabaseError, SchemaTooNewError, ValueError) as exc:
        print(f"Cannot open the account database: {exc}", file=sys.stderr)
        return 2
    try:
        if args.command == "backup":
            message = cmd_backup(args, db)
        elif args.command == "migrate":
            with db.transaction() as conn:
                message = f"Database schema is at version {current_version(conn)}: {db.path}"
        else:
            handler = {
                "create-admin": cmd_create_admin,
                "list-users": cmd_list_users,
                "reset-password": cmd_reset_password,
                "enable": cmd_enable,
                "grant-role": cmd_grant_role,
            }[args.command]
            message = handler(args, auth)
    except CommandError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ApiError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    except (DatabaseError, OSError) as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled. Nothing was changed.", file=sys.stderr)
        return 130
    finally:
        db.close()
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
