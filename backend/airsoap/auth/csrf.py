"""Cross-site request forgery protection for a same-origin single-page app.

Three layers, no per-request tokens to manage:
1. The session cookie is SameSite=Strict, so other sites' pages never send it.
2. State-changing requests must carry an Origin (or Referer) from ``allowed_origins``.
3. They must also carry ``X-AirSOAP-Request: 1``. A page on another origin cannot add a
   custom header without a CORS preflight, and this server never allows CORS.
"""

from urllib.parse import urlsplit

from fastapi import Request

from ..errors import ApiError
from .deps import client_ip

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
REQUEST_HEADER = "x-airsoap-request"


def _request_origin(request: Request) -> str | None:
    origin = request.headers.get("origin")
    if origin and origin != "null":
        return origin.strip().lower().rstrip("/")
    referer = request.headers.get("referer")
    if referer:
        parts = urlsplit(referer)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}".lower()
    return None


def require_same_origin(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    services = request.app.state.services
    origin = _request_origin(request)
    if origin is None or origin not in services.settings.resolved_allowed_origins:
        detail = "missing_origin" if origin is None else "foreign_origin"
    elif request.headers.get(REQUEST_HEADER) != "1":
        detail = "missing_header"
    else:
        return
    services.auth.record_rejected_request(client_ip(request), detail)
    raise ApiError(
        403, "REQUEST_REJECTED", "This request was blocked because it did not come from the AirSOAP app."
    )
