"""Runtime configuration, read from AIRSOAP_* environment variables."""

import os
import re
import sys
import tempfile
from enum import StrEnum
from functools import cached_property
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# ALSA device names such as "default", "plughw:CARD=MICROPHONE,DEV=0" or "hw:2,0".
# Never starts with "-" so it cannot be read as an arecord option.
_DEVICE_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_:,.=]*$")


class RecorderKind(StrEnum):
    AUTO = "auto"
    MOCK = "mock"
    ARECORD = "arecord"


class AiKind(StrEnum):
    AUTO = "auto"
    MOCK = "mock"
    OLLAMA = "ollama"


class WhisperLifecycle(StrEnum):
    EXTERNAL = "external"  # whisper-server is run separately (e.g. its own systemd service)
    ON_DEMAND = "on-demand"  # the backend starts it for a job and stops it afterwards


class TranscriberKind(StrEnum):
    AUTO = "auto"
    MOCK = "mock"
    WHISPER_SERVER = "whisper-server"


def default_work_dir() -> Path:
    """RAM-backed runtime directory on Linux, so audio never reaches the SD card."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir and sys.platform.startswith("linux"):
        return Path(runtime_dir) / "airsoap"
    return Path(tempfile.gettempdir()) / f"airsoap-{os.getuid()}"


def default_database_path() -> Path:
    """Persistent storage for the account database, never a temporary directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AirSOAP" / "airsoap.db"
    state_home = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state_home) / "airsoap" / "airsoap.db"


def _ephemeral_roots() -> list[Path]:
    # Listed so that a database placed in any of them is refused.
    roots = [Path("/tmp"), Path("/var/tmp"), Path("/run"), Path("/dev/shm")]  # noqa: S108
    roots.append(Path(tempfile.gettempdir()))
    if runtime := os.environ.get("XDG_RUNTIME_DIR"):
        roots.append(Path(runtime))
    resolved = []
    for root in roots:
        try:
            resolved.append(root.resolve())
        except OSError:
            continue
    return resolved


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


_ORIGIN = re.compile(r"^https?://[a-z0-9.-]+(:\d{1,5})?$")
# On a Mac (development): the Vite dev server, plus the backend's own port (added below).
DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def _is_mac() -> bool:
    return sys.platform == "darwin"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIRSOAP_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"

    recorder: RecorderKind = RecorderKind.AUTO
    transcriber: TranscriberKind = TranscriberKind.AUTO

    arecord_path: str = "arecord"
    arecord_device: str = "default"
    work_dir: Path = Field(default_factory=default_work_dir)
    max_recording_seconds: int = Field(default=1800, ge=5, le=4 * 3600)

    whisper_url: str = "http://127.0.0.1:8178"
    whisper_timeout_seconds: float = Field(default=900, gt=0)
    whisper_language: str = Field(default="en", pattern=r"^[a-z]{2}$")
    allow_remote_whisper: bool = False
    whisper_lifecycle: WhisperLifecycle = WhisperLifecycle.EXTERNAL
    # Used only with whisper_lifecycle=on-demand:
    whisper_server_path: Path | None = None
    whisper_model_path: Path | None = None
    whisper_threads: int = Field(default=4, ge=1, le=64)
    whisper_idle_seconds: float = Field(default=20, ge=0)
    whisper_startup_timeout_seconds: float = Field(default=90, gt=0)

    ai: AiKind = AiKind.AUTO
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = Field(
        default="qwen2.5:3b-instruct", min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/:-]*$"
    )
    ollama_timeout_seconds: float = Field(default=600, gt=0)
    # How long Ollama keeps the model loaded after a task: "5m", "30s", "0" (unload at once) or "-1".
    ollama_keep_alive: str = Field(default="5m", pattern=r"^(-1|0|\d+[smh])$")
    ollama_num_ctx: int = Field(default=8192, ge=2048, le=32768)
    ai_max_attempts: int = Field(default=2, ge=1, le=3)
    max_pending_ai_jobs: int = Field(default=2, ge=1)
    mock_ai_seconds: float = Field(default=1.0, ge=0)

    job_ttl_seconds: int = Field(default=600, ge=10)
    max_pending_jobs: int = Field(default=4, ge=1)
    mock_transcribe_seconds: float = Field(default=2.0, ge=0)

    frontend_dist: Path | None = None

    # --- accounts and sessions (Phase 5) ---
    database_path: Path = Field(default_factory=lambda: default_database_path())
    # Tests only: lets the database live in a temporary directory.
    allow_ephemeral_database: bool = False
    # None means: Secure on Linux (served over HTTPS by Caddy), not Secure on macOS (local HTTP development).
    cookie_secure: bool | None = None
    # Comma-separated origins allowed to make state-changing requests, e.g. "https://raspberrypi.local".
    allowed_origins: str = ""
    session_idle_minutes: int = Field(default=30, ge=1, le=24 * 60)
    session_absolute_hours: int = Field(default=12, ge=1, le=7 * 24)
    role_selection_minutes: int = Field(default=5, ge=1, le=60)
    login_max_failures_per_account: int = Field(default=5, ge=1)
    login_max_failures_per_ip: int = Field(default=20, ge=1)
    login_failure_window_minutes: int = Field(default=15, ge=1)
    audit_retention_days: int = Field(default=365, ge=1)
    api_docs: bool = False
    # How often to end the recording and jobs of accounts whose sessions have lapsed.
    session_sweep_seconds: float = Field(default=60, gt=0)
    # Addresses whose X-Forwarded-For header is trusted (the local Caddy reverse proxy).
    trusted_proxies: str = "127.0.0.1"

    @field_validator("arecord_device")
    @classmethod
    def _check_device(cls, value: str) -> str:
        if not _DEVICE_PATTERN.fullmatch(value):
            raise ValueError("arecord device must be an ALSA name such as plughw:CARD=MICROPHONE,DEV=0")
        return value

    @field_validator("whisper_server_path", "whisper_model_path")
    @classmethod
    def _absolute_paths(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("whisper paths must be absolute")
        return value

    @field_validator("database_path")
    @classmethod
    def _absolute_database_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("database_path must be an absolute path")
        return value

    @field_validator("allowed_origins")
    @classmethod
    def _check_origins(cls, value: str) -> str:
        for origin in filter(None, (part.strip().lower().rstrip("/") for part in value.split(","))):
            if not _ORIGIN.fullmatch(origin):
                raise ValueError("allowed_origins must be like https://raspberrypi.local (scheme and host)")
        return value

    @model_validator(mode="after")
    def _database_is_persistent(self) -> "Settings":
        if self.allow_ephemeral_database:
            return self
        if _is_within(self.database_path, self.work_dir):
            raise ValueError("database_path must not be inside the temporary audio directory (work_dir)")
        if any(_is_within(self.database_path, root) for root in _ephemeral_roots()):
            raise ValueError("database_path must be on persistent storage, not in a temporary directory")
        return self

    @field_validator("work_dir")
    @classmethod
    def _absolute_work_dir(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("work_dir must be an absolute path")
        return value

    @model_validator(mode="after")
    def _whisper_stays_local(self) -> "Settings":
        parts = urlsplit(self.whisper_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("whisper_url must be an http(s) URL")
        if parts.hostname not in LOOPBACK_HOSTS and not self.allow_remote_whisper:
            raise ValueError("whisper_url must point at this device (127.0.0.1); audio never leaves it")
        ollama = urlsplit(self.ollama_url)
        if ollama.scheme not in {"http", "https"} or ollama.hostname not in LOOPBACK_HOSTS:
            raise ValueError(
                "ollama_url must point at this device (127.0.0.1); clinical text never leaves it"
            )
        if self.whisper_lifecycle is WhisperLifecycle.ON_DEMAND:
            if parts.hostname not in LOOPBACK_HOSTS or parts.port is None:
                raise ValueError(
                    "on-demand whisper-server needs a loopback whisper_url with an explicit port"
                )
            if self.whisper_server_path is None or self.whisper_model_path is None:
                raise ValueError("on-demand whisper-server needs whisper_server_path and whisper_model_path")
        return self

    @cached_property
    def resolved_recorder(self) -> RecorderKind:
        if self.recorder is RecorderKind.AUTO:
            return RecorderKind.MOCK if _is_mac() else RecorderKind.ARECORD
        return self.recorder

    @cached_property
    def resolved_cookie_secure(self) -> bool:
        return (not _is_mac()) if self.cookie_secure is None else self.cookie_secure

    @cached_property
    def resolved_allowed_origins(self) -> frozenset[str]:
        parts = (part.strip().lower().rstrip("/") for part in self.allowed_origins.split(","))
        origins = {part for part in parts if part}
        if origins:
            return frozenset(origins)
        if _is_mac():
            return frozenset((*DEV_ORIGINS, f"http://localhost:{self.port}", f"http://127.0.0.1:{self.port}"))
        return frozenset(("https://raspberrypi.local",))

    @cached_property
    def resolved_ai(self) -> AiKind:
        if self.ai is AiKind.AUTO:
            return AiKind.MOCK if _is_mac() else AiKind.OLLAMA
        return self.ai

    @cached_property
    def resolved_transcriber(self) -> TranscriberKind:
        if self.transcriber is TranscriberKind.AUTO:
            return TranscriberKind.MOCK if _is_mac() else TranscriberKind.WHISPER_SERVER
        return self.transcriber
