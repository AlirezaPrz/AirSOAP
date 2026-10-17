"""Application factory.

Run with ``python -m airsoap`` (binds to AIRSOAP_HOST, 127.0.0.1 by default) or
``uvicorn --factory airsoap.main:create_app --host 127.0.0.1``.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__
from .ai.base import AiClient
from .ai.gate import ModelGate
from .ai.jobs import AiJobManager
from .ai.mock import MockAiClient
from .ai.ollama import OllamaClient
from .api.ai_routes import router as ai_router
from .api.deps import Services
from .api.routes import health_router, recording_router, transcription_router
from .config import AiKind, RecorderKind, Settings, TranscriberKind, WhisperLifecycle
from .errors import ApiError, api_error_handler
from .jobs import JobManager
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
) -> FastAPI:
    settings = settings or Settings()
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
    services = Services(
        settings=settings,
        storage=storage,
        recorder=recorder,
        transcriber=transcriber,
        jobs=jobs,
        sessions=RecordingSessions(recorder=recorder, storage=storage, jobs=jobs),
        ai_client=ai_client,
        ai_jobs=ai_jobs,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        storage.prepare()
        if removed := storage.sweep():
            logger.info("removed %d leftover audio file(s) from a previous run", removed)
        await jobs.start()
        await ai_jobs.start()
        logger.info("recorder=%s transcriber=%s ai=%s", recorder.name, transcriber.name, ai_client.name)
        try:
            yield
        finally:
            await services.sessions.close()
            await jobs.close()
            await ai_jobs.close()
            await ai_client.unload()  # leave no model in memory after the backend stops
            await ai_client.close()
            await transcriber.close()
            storage.sweep()

    app = FastAPI(title="AirSOAP backend", version=__version__, lifespan=lifespan)
    app.state.services = services
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(health_router)
    app.include_router(recording_router)
    app.include_router(transcription_router)
    app.include_router(ai_router)
    if settings.frontend_dist is not None:
        app.frontend("/", directory=settings.frontend_dist)
    return app
