"""Transcription jobs: an in-memory store and one sequential worker.

whisper-server handles one request at a time, so jobs run strictly in order.
Each job's audio file is deleted as soon as the job finishes, fails or is
cancelled. Finished jobs (and their transcripts) are dropped from memory after
``ttl_seconds``. Nothing is written to disk or logged except IDs and timings.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .errors import ApiError
from .storage import WorkDir
from .transcription import Transcriber, TranscriptionError, TranscriptResult

logger = logging.getLogger(__name__)


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


FINISHED = frozenset({JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.ERROR})


@dataclass(frozen=True)
class JobError:
    code: str
    message: str
    retryable: bool = False


@dataclass
class Job:
    id: str
    audio_path: Path
    audio_duration_ms: int
    created_at: float
    status: JobStatus = JobStatus.QUEUED
    finished_at: float | None = None
    result: TranscriptResult | None = None
    error: JobError | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    @property
    def progress(self) -> int | None:
        """0 while queued, 100 when complete, None while processing (whisper-server reports no progress)."""
        if self.status is JobStatus.QUEUED:
            return 0
        if self.status is JobStatus.COMPLETED:
            return 100
        return None


UNEXPECTED_ERROR = JobError("TRANSCRIPTION_FAILED", "Transcription failed unexpectedly.")


class JobManager:
    def __init__(
        self,
        *,
        transcriber: Transcriber,
        storage: WorkDir,
        ttl_seconds: float,
        max_pending: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transcriber = transcriber
        self._storage = storage
        self._ttl = ttl_seconds
        self._max_pending = max_pending
        self._clock = clock
        self._jobs: dict[str, Job] = {}
        self._queue: asyncio.Queue[str] | None = None
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._queue = asyncio.Queue()
        self._worker = asyncio.create_task(self._run(), name="airsoap-transcription-worker")

    async def close(self) -> None:
        for job in self._jobs.values():
            if job.task is not None and not job.task.done():
                job.task.cancel()
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
        for job in self._jobs.values():
            if job.status not in FINISHED:
                self._finish(job, JobStatus.CANCELLED)
        self._jobs.clear()

    def submit(self, audio_path: Path, audio_duration_ms: int) -> Job:
        """Queue a recording. The caller still owns (and must delete) the file if this raises."""
        if self._queue is None:
            raise RuntimeError("JobManager.start() was not called")
        self._purge_expired()
        pending = sum(1 for job in self._jobs.values() if job.status not in FINISHED)
        if pending >= self._max_pending:
            raise ApiError(
                503, "QUEUE_FULL", "Too many recordings are waiting to be transcribed.", retryable=True
            )
        job = Job(
            id=uuid.uuid4().hex,
            audio_path=audio_path,
            audio_duration_ms=audio_duration_ms,
            created_at=self._clock(),
        )
        self._jobs[job.id] = job
        self._queue.put_nowait(job.id)
        logger.info("job=%s queued audio_ms=%d", job.id, audio_duration_ms)
        return job

    def get(self, job_id: str) -> Job:
        self._purge_expired()
        job = self._jobs.get(job_id)
        if job is None:
            raise ApiError(404, "JOB_NOT_FOUND", "This transcription job does not exist or has expired.")
        return job

    async def cancel(self, job_id: str) -> Job:
        """Idempotent: cancelling a finished job returns it unchanged."""
        job = self.get(job_id)
        if job.status is JobStatus.QUEUED:
            self._finish(job, JobStatus.CANCELLED)
        elif job.status is JobStatus.PROCESSING and job.task is not None:
            job.task.cancel()
            await asyncio.wait({job.task})
            if job.status not in FINISHED:
                self._finish(job, JobStatus.CANCELLED)
        return job

    async def _run(self) -> None:
        assert self._queue is not None  # noqa: S101
        while True:
            job_id = await self._queue.get()
            job = self._jobs.get(job_id)
            if job is None or job.status is not JobStatus.QUEUED:
                continue
            job.status = JobStatus.PROCESSING
            job.task = asyncio.create_task(self._process(job), name=f"airsoap-job-{job.id}")
            # asyncio.wait never raises, so cancelling one job does not stop the worker.
            await asyncio.wait({job.task})
            if job.status not in FINISHED:
                # Cancelled before the task ever ran, so _process never saw it.
                self._finish(job, JobStatus.CANCELLED)

    async def _process(self, job: Job) -> None:
        started = self._clock()
        try:
            result = await self._transcriber.transcribe(job.audio_path, job.audio_duration_ms)
        except asyncio.CancelledError:
            self._finish(job, JobStatus.CANCELLED)
            logger.info("job=%s cancelled", job.id)
            raise
        except TranscriptionError as exc:
            self._finish(job, JobStatus.ERROR, error=JobError(exc.code, exc.message))
            logger.warning("job=%s failed code=%s", job.id, exc.code)
        except Exception as exc:
            self._finish(job, JobStatus.ERROR, error=UNEXPECTED_ERROR)
            # The exception type only: messages from libraries could echo request data.
            logger.error("job=%s failed unexpectedly (%s)", job.id, type(exc).__name__)
        else:
            self._finish(job, JobStatus.COMPLETED, result=result)
            elapsed_ms = round((self._clock() - started) * 1000)
            ratio = elapsed_ms / job.audio_duration_ms if job.audio_duration_ms else 0.0
            logger.info(
                "job=%s completed audio_ms=%d elapsed_ms=%d realtime_factor=%.2f segments=%d",
                job.id,
                job.audio_duration_ms,
                elapsed_ms,
                ratio,
                len(result.segments),
            )

    def _finish(
        self,
        job: Job,
        status: JobStatus,
        *,
        result: TranscriptResult | None = None,
        error: JobError | None = None,
    ) -> None:
        job.status = status
        job.result = result
        job.error = error
        job.finished_at = self._clock()
        self._storage.delete(job.audio_path)

    def _purge_expired(self) -> None:
        now = self._clock()
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job.finished_at is not None and now - job.finished_at >= self._ttl
        ]
        for job_id in expired:
            del self._jobs[job_id]
