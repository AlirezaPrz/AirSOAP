"""``python -m airsoap``: serve the API on AIRSOAP_HOST:AIRSOAP_PORT (127.0.0.1:8000 by default)."""

import logging
import os

import uvicorn

from .config import LOOPBACK_HOSTS, Settings
from .main import create_app


def main() -> None:
    # Every file this process creates (database, audio) is readable by its owner only.
    os.umask(0o077)
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if settings.host not in LOOPBACK_HOSTS:
        logging.getLogger("airsoap").warning(
            "binding to %s exposes the API beyond this device; use 127.0.0.1 unless you need otherwise",
            settings.host,
        )
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        # Trust X-Forwarded-For/-Proto only from the local reverse proxy (Caddy), so sign-in limits
        # and audit events see the browser's real address.
        proxy_headers=True,
        forwarded_allow_ips=settings.trusted_proxies,
        server_header=False,
    )


if __name__ == "__main__":
    main()
