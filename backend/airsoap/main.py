"""Application factory.

Run with ``python -m airsoap`` (binds to AIRSOAP_HOST, 127.0.0.1 by default) or
``uvicorn --factory airsoap.main:create_app --host 127.0.0.1``.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import __version__
from .api.deps import Services
from .api.routes import health_router, recording_router, transcription_router
from .config import RecorderKind, Settings, TranscriberKind
from .errors import ApiError, api_error_handler
from .jobs import JobManager
from .recording import ArecordRecorder, MockRecorder, Recorder
from .sessions import RecordingSessions
from .storage import WorkDir
from .transcription import MockTranscriber, Transcriber, WhisperServerTranscriber

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
        return WhisperServerTranscriber(
            base_url=settings.whisper_url,
            timeout_seconds=settings.whisper_timeout_seconds,
            language=settings.whisper_language,
        )
    return MockTranscriber(delay_seconds=settings.mock_transcribe_seconds)


def create_app(
    settings: Settings | None = None,
    *,
    recorder: Recorder | None = None,
    transcriber: Transcriber | None = None,
) -> FastAPI:
    settings = settings or Settings()
    storage = WorkDir(settings.work_dir)
    recorder = recorder or build_recorder(settings)
    transcriber = transcriber or build_transcriber(settings)
    jobs = JobManager(
        transcriber=transcriber,
        storage=storage,
        ttl_seconds=settings.job_ttl_seconds,
        max_pending=settings.max_pending_jobs,
    )
    services = Services(
        settings=settings,
        storage=storage,
        recorder=recorder,
        transcriber=transcriber,
        jobs=jobs,
        sessions=RecordingSessions(recorder=recorder, storage=storage, jobs=jobs),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        storage.prepare()
        if removed := storage.sweep():
            logger.info("removed %d leftover audio file(s) from a previous run", removed)
        await jobs.start()
        logger.info("recorder=%s transcriber=%s", recorder.name, transcriber.name)
        try:
            yield
        finally:
            await services.sessions.close()
            await jobs.close()
            storage.sweep()

    app = FastAPI(title="AirSOAP backend", version=__version__, lifespan=lifespan)
    app.state.services = services
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(health_router)
    app.include_router(recording_router)
    app.include_router(transcription_router)
    if settings.frontend_dist is not None:
        app.frontend("/", directory=settings.frontend_dist)
    return app
