import socket
from pathlib import Path

import pytest

from airsoap.config import Settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        work_dir=tmp_path / "work",
        recorder="mock",
        transcriber="mock",
        mock_transcribe_seconds=0,
        # An isolated database per test; never the developer's or the Pi's real one.
        database_path=tmp_path / "state" / "airsoap.db",
        allow_ephemeral_database=True,
        cookie_secure=False,  # TestClient talks plain HTTP
        allowed_origins="http://testserver",
        session_sweep_seconds=3600,
    )


LOOPBACK = {"127.0.0.1", "::1", "localhost"}
OLLAMA_PORT = 11434


@pytest.fixture(autouse=True)
def _never_reach_a_live_ollama_or_remote_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests use fakes. Fail loudly if one tries Ollama's real port or any non-loopback address,
    so no test can contact a live model or send even synthetic text off this machine."""
    real_connect = socket.socket.connect

    def guarded(self: socket.socket, address: object) -> None:
        if isinstance(address, tuple) and (address[0] not in LOOPBACK or address[1] == OLLAMA_PORT):
            raise AssertionError(f"test tried to connect to {address[0]}:{address[1]}")
        return real_connect(self, address)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture(autouse=True)
def _never_touch_a_real_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Any Settings() built without an explicit database path points into this test's temp dir,
    so no test can create or change the developer's or the Pi's real account database."""
    from airsoap import config

    monkeypatch.setattr(config, "default_database_path", lambda: tmp_path / "default-db" / "airsoap.db")
    monkeypatch.delenv("AIRSOAP_DATABASE_PATH", raising=False)
    monkeypatch.setenv("AIRSOAP_ALLOW_EPHEMERAL_DATABASE", "true")
