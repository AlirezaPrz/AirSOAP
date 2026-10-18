"""Application factory.

Run with ``python -m airsoap`` (binds to AIRSOAP_HOST, 127.0.0.1 by default) or
``uvicorn --factory airsoap.main:create_app --host 127.0.0.1``.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__
from .ai.base import AiClient
from .ai.gate import ModelGate
from .ai.jobs import AiJobManager
from .ai.mock import MockAiClient
from .ai.ollama import OllamaClient
from .api.admin_routes import router as admin_router
from .api.ai_routes import router as ai_router
from .api.auth_routes import router as auth_router
from .api.deps import Services
from .api.routes import health_router, recording_router, system_router, transcription_router
from .auth.passwords import Passwords
from .auth.service import AuthService
from .config import AiKind, LiveEngine, RecorderKind, Settings, TranscriberKind, WhisperLifecycle
from .db import Database
from .errors import ApiError, api_error_handler
from .jobs import JobManager
from .live import GrowingWav, LiveSession, LiveTranscriber, MockLiveTranscriber, WhisperLiveTranscriber
from .recording import ArecordRecorder, MockRecorder, Recorder
from .sessions import RecordingSessions
from .storage import WorkDir
from .transcription import (
    ManagedWhisperTranscriber,
    MockTranscriber,
    Transcriber,
    WhisperProcessManager,
    WhisperServerTranscriber,
)

logger = logging.getLogger(__name__)

# The built frontend loads only its own files; nothing may frame it.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
    "frame-ancestors 'none'"
)


async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    if request.url.path.startswith("/api/"):
        # Clinical results and temporary passwords must never be cached by the browser or a proxy.
        response.headers["Cache-Control"] = "no-store"
    return response


async def _sweep_lapsed_sessions(services: Services, interval: float) -> None:
    """End the recording and jobs of accounts that no longer have a live session (expired or disabled)."""
    while True:
        await asyncio.sleep(interval)
        owners = (
            {o for o in (services.sessions.active_owner,) if o}
            | services.jobs.owners()
            | services.ai_jobs.owners()
        )
        for owner in owners:
            try:
                live = await asyncio.to_thread(services.auth.user_has_live_session, owner)
                if not live:
                    await services.discard_owner(owner)
            except Exception as exc:
                logger.error("session sweep failed (%s)", type(exc).__name__)


def build_recorder(settings: Settings) -> Recorder:
    if settings.resolved_recorder is RecorderKind.ARECORD:
        return ArecordRecorder(
            binary=settings.arecord_path,
            device=settings.arecord_device,
            max_seconds=settings.max_recording_seconds,
        )
    return MockRecorder(max_seconds=settings.max_recording_seconds)


def build_transcriber(settings: Settings) -> Transcriber:
    if settings.resolved_transcriber is TranscriberKind.WHISPER_SERVER:
        client = WhisperServerTranscriber(
            base_url=settings.whisper_url,
            timeout_seconds=settings.whisper_timeout_seconds,
            language=settings.whisper_language,
        )
        if settings.whisper_lifecycle is WhisperLifecycle.ON_DEMAND:
            parts = urlsplit(settings.whisper_url)
            assert settings.whisper_server_path and settings.whisper_model_path  # noqa: S101 - validated
            assert parts.hostname and parts.port  # noqa: S101 - validated
            manager = WhisperProcessManager(
                binary=settings.whisper_server_path,
                model=settings.whisper_model_path,
                host=parts.hostname,
                port=parts.port,
                threads=settings.whisper_threads,
                language=settings.whisper_language,
                idle_seconds=settings.whisper_idle_seconds,
                startup_timeout=settings.whisper_startup_timeout_seconds,
                probe=client.is_reachable,
            )
            return ManagedWhisperTranscriber(client=client, manager=manager)
        return client
    return MockTranscriber(delay_seconds=settings.mock_transcribe_seconds)


# Live blocks are short (15 s at most); a request that takes this long means Whisper is stuck.
LIVE_REQUEST_TIMEOUT_SECONDS = 60.0


def build_live_transcriber(settings: Settings) -> LiveTranscriber | None:
    if settings.resolved_live is LiveEngine.MOCK:
        return MockLiveTranscriber()
    if settings.resolved_live is LiveEngine.WHISPER:
        parts = urlsplit(settings.live_whisper_url)
        assert settings.whisper_server_path and settings.live_whisper_model_path  # noqa: S101 - resolved
        assert parts.hostname and parts.port  # noqa: S101 - validated
        client = WhisperServerTranscriber(
            base_url=settings.live_whisper_url,
            timeout_seconds=LIVE_REQUEST_TIMEOUT_SECONDS,
            language=settings.whisper_language,
        )
        manager = WhisperProcessManager(
            binary=settings.whisper_server_path,
            model=settings.live_whisper_model_path,
            host=parts.hostname,
            port=parts.port,
            threads=settings.whisper_threads,
            language=settings.whisper_language,
            idle_seconds=0,
            startup_timeout=settings.whisper_startup_timeout_seconds,
            probe=client.is_reachable,
        )
        return WhisperLiveTranscriber(client=client, manager=manager)
    return None


def build_ai_client(settings: Settings) -> AiClient:
    if settings.resolved_ai is AiKind.OLLAMA:
        return OllamaClient(
            base_url=settings.ollama_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
            keep_alive=settings.ollama_keep_alive,
            num_ctx=settings.ollama_num_ctx,
        )
    return MockAiClient(delay_seconds=settings.mock_ai_seconds)


async def validation_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
    """FastAPI's default 422 body echoes the rejected input; this one never does."""
    return JSONResponse(
        status_code=422,
        content={
            "error": {"code": "INVALID_REQUEST", "message": "The request was not valid.", "retryable": False}
        },
    )


def create_app(
    settings: Settings | None = None,
    *,
    recorder: Recorder | None = None,
    transcriber: Transcriber | None = None,
    ai_client: AiClient | None = None,
    password_hasher: PasswordHasher | None = None,
    auth_clock: Callable[[], datetime] | None = None,
    live_transcriber_override: LiveTranscriber | None = None,
) -> FastAPI:
    """``password_hasher`` and ``auth_clock`` exist for tests (fast hashing, controlled time)."""
    settings = settings or Settings()
    db = Database(settings.database_path)
    auth = AuthService(
        db=db,
        settings=settings,
        passwords=Passwords(password_hasher),
        **({"clock": auth_clock} if auth_clock else {}),
    )
    storage = WorkDir(settings.work_dir)
    recorder = recorder or build_recorder(settings)
    transcriber = transcriber or build_transcriber(settings)
    ai_client = ai_client or build_ai_client(settings)
    gate = ModelGate(transcriber=transcriber, ai=ai_client)
    jobs = JobManager(
        transcriber=transcriber,
        storage=storage,
        ttl_seconds=settings.job_ttl_seconds,
        max_pending=settings.max_pending_jobs,
        gate=gate,
    )
    ai_jobs = AiJobManager(
        client=ai_client,
        gate=gate,
        ttl_seconds=settings.job_ttl_seconds,
        max_pending=settings.max_pending_ai_jobs,
        max_attempts=settings.ai_max_attempts,
    )
    live_transcriber = (
        live_transcriber_override
        if live_transcriber_override is not None
        else build_live_transcriber(settings)
    )

    def start_live(recording_id: str, path: Path) -> LiveSession:
        assert live_transcriber is not None  # noqa: S101
        return LiveSession(
            recording_id=recording_id,
            source=GrowingWav(path),
            transcriber=live_transcriber,
            pause_ms=settings.live_pause_ms,
            max_block_ms=round(settings.live_max_block_seconds * 1000),
            behind_ms=round(settings.live_behind_seconds * 1000),
            hold_model=gate.for_transcription,
        )

    services = Services(
        settings=settings,
        storage=storage,
        recorder=recorder,
        transcriber=transcriber,
        jobs=jobs,
        sessions=RecordingSessions(
            recorder=recorder,
            storage=storage,
            jobs=jobs,
            live_factory=start_live if live_transcriber is not None else None,
        ),
        ai_client=ai_client,
        ai_jobs=ai_jobs,
        db=db,
        auth=auth,
        live=live_transcriber,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db.open()  # refuses unsafe paths and owners; applies migrations
        auth.prune()
        storage.prepare()
        if removed := storage.sweep():
            logger.info("removed %d leftover audio file(s) from a previous run", removed)
        await jobs.start()
        await ai_jobs.start()
        logger.info(
            "recorder=%s transcriber=%s live=%s ai=%s",
            recorder.name,
            transcriber.name,
            live_transcriber.name if live_transcriber else "off",
            ai_client.name,
        )
        sweeper = asyncio.create_task(_sweep_lapsed_sessions(services, settings.session_sweep_seconds))
        try:
            yield
        finally:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
            await services.sessions.close()
            if live_transcriber is not None:
                await live_transcriber.stop()
            await jobs.close()
            await ai_jobs.close()
            await ai_client.unload()  # leave no model in memory after the backend stops
            await ai_client.close()
            await transcriber.close()
            storage.sweep()
            db.close()

    docs = settings.api_docs
    app = FastAPI(
        title="AirSOAP backend",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.middleware("http")(security_headers)
    app.state.services = services
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(system_router)
    app.include_router(recording_router)
    app.include_router(transcription_router)
    app.include_router(ai_router)
    if settings.frontend_dist is not None:
        app.frontend("/", directory=settings.frontend_dist)
    return app
