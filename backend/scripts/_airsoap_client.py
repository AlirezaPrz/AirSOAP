"""A signed-in client for the smoke scripts. Standard library only.

Asks for the email and password at the terminal (the password is hidden), keeps the
session cookie in memory only, and signs out when done. Nothing is written to disk.
"""

import getpass
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ENV_FILE = Path.home() / ".config" / "airsoap" / "airsoap.env"


def _configured_origin() -> str | None:
    """The first allowed origin from the environment or the service's env file, if set."""
    value = os.environ.get("AIRSOAP_ALLOWED_ORIGINS")
    if value is None and ENV_FILE.is_file():
        for line in ENV_FILE.read_text().splitlines():
            if line.strip().startswith("AIRSOAP_ALLOWED_ORIGINS="):
                value = line.split("=", 1)[1]
    first = (value or "").split(",")[0].strip().rstrip("/")
    return first or None


def default_origin(base: str) -> str:
    """The origin the backend accepts. Calling 127.0.0.1 directly on the Pi still needs the app's origin."""
    if configured := _configured_origin():
        return configured
    if sys.platform != "darwin":
        return "https://raspberrypi.local"
    parts = urlsplit(base)
    return f"{parts.scheme}://{parts.netloc}"


class Client:
    def __init__(self, base: str, origin: str, cafile: str | None = None) -> None:
        self.base = base.rstrip("/")
        self.origin = origin
        self._cookie: str | None = None
        self._context = ssl.create_default_context(cafile=cafile) if cafile else None

    def call(self, method: str, path: str, body: dict | None = None, *, allow: tuple[int, ...] = ()) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json", "Origin": self.origin, "X-AirSOAP-Request": "1"}
        if self._cookie:
            headers["Cookie"] = self._cookie
        request = urllib.request.Request(  # noqa: S310 - the backend URL given on the command line
            f"{self.base}{path}", data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=30, context=self._context) as response:  # noqa: S310
                self._remember_cookie(response.headers.get_all("Set-Cookie") or [])
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read() or b"{}")
            if exc.code in allow:
                return {"_status": exc.code, **payload}
            error = payload.get("error", payload)
            sys.exit(f"{method} {path} failed with HTTP {exc.code}: {error.get('message', error)}")
        except urllib.error.URLError as exc:
            sys.exit(f"Cannot reach the backend at {self.base}: {exc.reason}")

    def _remember_cookie(self, headers: list[str]) -> None:
        for header in headers:
            pair = header.split(";", 1)[0].strip()
            if pair.startswith("airsoap_session="):
                self._cookie = pair if pair != "airsoap_session=" else None

    def sign_in(self, email: str | None, role: str) -> None:
        email = email or input("AirSOAP email: ").strip()
        password = getpass.getpass("Password (hidden): ")
        me = self.call("POST", "/api/auth/login", {"email": email, "password": password})
        del password
        if me["mustChangePassword"]:
            self.sign_out()
            sys.exit("This account still has a temporary password. Sign in once in the browser to change it.")
        if me["activeRole"] is None:
            if role not in me["user"]["roles"]:
                self.sign_out()
                sys.exit(f"This account does not have the {role} role.")
            me = self.call("POST", "/api/auth/select-role", {"role": role})
        if me["activeRole"] != role:
            self.sign_out()
            sys.exit(f"Signed in as {me['activeRole']}, but this check needs the {role} role.")
        print(f"signed in as {me['user']['email']} ({role})")

    def sign_out(self) -> None:
        if self._cookie:
            self.call("POST", "/api/auth/logout", allow=(401, 403))
            self._cookie = None


def add_connection_arguments(parser) -> None:
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8000", help="backend URL (default: %(default)s)"
    )
    parser.add_argument("--origin", help="Origin header to send (default: the first AIRSOAP_ALLOWED_ORIGINS)")
    parser.add_argument("--cafile", help="CA certificate to trust, for an https:// --base-url via Caddy")
    parser.add_argument(
        "--email", help="account email (asked for if omitted); the password is always asked for"
    )


def connect(args) -> Client:
    return Client(args.base_url, args.origin or default_origin(args.base_url), args.cafile)
