"""AI task jobs: an in-memory store and one sequential worker.

Same shape as the transcription jobs. Task input is dropped as soon as a job
finishes, and finished results are dropped after ``ttl_seconds`` or when the
frontend deletes the job. Nothing is written to disk, and logs contain only IDs,
job kinds and timings: never transcript text, notes or model output.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from ..errors import ApiError
from ..jobs import FINISHED, JobError, JobStatus
from .base import AiClient
from .errors import AiError
from .gate import ModelGate
from .runner import run_task
from .schemas import ClinicalReviewRequest, SoapRequest
from .tasks import clinical_review, soap

logger = logging.getLogger(__name__)

UNEXPECTED = JobError("AI_FAILED", "The AI task failed unexpectedly.")


class AiJobKind(StrEnum):
    SOAP = "soap"
    CLINICAL_REVIEW = "clinical-review"


@dataclass
class AiJob:
    id: str
    kind: AiJobKind
    created_at: float
    request: Any | None  # dropped when the job finishes
    status: JobStatus = JobStatus.QUEUED
    finished_at: float | None = None
    result: BaseModel | None = None
    error: JobError | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)


class AiJobManager:
    def __init__(
        self,
        *,
        client: AiClient,
        gate: ModelGate,
        ttl_seconds: float,
        max_pending: int,
        max_attempts: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._gate = gate
        self._ttl = ttl_seconds
        self._max_pending = max_pending
        self._max_attempts = max_attempts
        self._clock = clock
        self._jobs: dict[str, AiJob] = {}
        self._queue: asyncio.Queue[str] | None = None
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._queue = asyncio.Queue()
        self._worker = asyncio.create_task(self._run(), name="airsoap-ai-worker")

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

    def submit(self, kind: AiJobKind, request: SoapRequest | ClinicalReviewRequest) -> AiJob:
        if self._queue is None:
            raise RuntimeError("AiJobManager.start() was not called")
        self._purge_expired()
        pending = sum(1 for job in self._jobs.values() if job.status not in FINISHED)
        if pending >= self._max_pending:
            raise ApiError(
                503, "AI_QUEUE_FULL", "The AI model is busy. Please try again shortly.", retryable=True
            )
        job = AiJob(id=uuid.uuid4().hex, kind=kind, created_at=self._clock(), request=request)
        self._jobs[job.id] = job
        self._queue.put_nowait(job.id)
        logger.info("ai job=%s kind=%s queued", job.id, kind.value)
        return job

    def get(self, job_id: str) -> AiJob:
        self._purge_expired()
        job = self._jobs.get(job_id)
        if job is None:
            raise ApiError(404, "JOB_NOT_FOUND", "This AI job does not exist or has expired.")
        return job

    async def cancel(self, job_id: str) -> AiJob:
        """Idempotent. Cancelling a running job aborts its request to the model."""
        job = self.get(job_id)
        if job.task is not None and not job.task.done():
            job.task.cancel()
            await asyncio.wait({job.task})
        if job.status not in FINISHED:
            self._finish(job, JobStatus.CANCELLED)
        return job

    async def delete(self, job_id: str) -> None:
        """Cancel if still running, then forget the job and its result."""
        if job_id in self._jobs:
            await self.cancel(job_id)
            self._jobs.pop(job_id, None)

    async def _run(self) -> None:
        assert self._queue is not None  # noqa: S101
        while True:
            job_id = await self._queue.get()
            job = self._jobs.get(job_id)
            if job is None or job.status is not JobStatus.QUEUED:
                continue
            job.task = asyncio.create_task(self._process(job), name=f"airsoap-ai-{job.id}")
            await asyncio.wait({job.task})
            if job.status not in FINISHED:
                self._finish(job, JobStatus.CANCELLED)

    async def _process(self, job: AiJob) -> None:
        try:
            async with self._gate.for_ai():
                job.status = JobStatus.PROCESSING
                started = self._clock()
                result, completion = await self._execute(job)
        except asyncio.CancelledError:
            self._finish(job, JobStatus.CANCELLED)
            logger.info("ai job=%s cancelled", job.id)
            raise
        except AiError as exc:
            self._finish(job, JobStatus.ERROR, error=JobError(exc.code, exc.message, exc.retryable))
            logger.warning("ai job=%s kind=%s failed code=%s", job.id, job.kind.value, exc.code)
        except Exception as exc:
            self._finish(job, JobStatus.ERROR, error=UNEXPECTED)
            logger.error("ai job=%s failed unexpectedly (%s)", job.id, type(exc).__name__)
        else:
            self._finish(job, JobStatus.COMPLETED, result=result)
            logger.info(
                "ai job=%s kind=%s completed elapsed_ms=%d prompt_tokens=%s completion_tokens=%s",
                job.id,
                job.kind.value,
                round((self._clock() - started) * 1000),
                completion.prompt_tokens if completion else None,
                completion.completion_tokens if completion else None,
            )

    async def _execute(self, job: AiJob) -> tuple[BaseModel, Any]:
        if job.kind is AiJobKind.SOAP:
            return await run_task(
                self._client, soap.TASK, job.request, soap.parse, max_attempts=self._max_attempts
            )
        return await run_task(
            self._client,
            clinical_review.TASK,
            job.request,
            clinical_review.parse,
            max_attempts=self._max_attempts,
        )

    def _finish(
        self, job: AiJob, status: JobStatus, *, result: BaseModel | None = None, error: JobError | None = None
    ) -> None:
        job.status = status
        job.result = result
        job.error = error
        job.request = None  # the transcript and note are no longer needed
        job.finished_at = self._clock()

    def _purge_expired(self) -> None:
        now = self._clock()
        for job_id in [
            job_id
            for job_id, job in self._jobs.items()
            if job.finished_at is not None and now - job.finished_at >= self._ttl
        ]:
            del self._jobs[job_id]
