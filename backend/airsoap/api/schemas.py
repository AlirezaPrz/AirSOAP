"""Request and response models. JSON uses camelCase to match the frontend's ``api/types.ts``."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from ..jobs import Job, JobStatus
from ..sessions import RecordingState

HexId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class HealthResponse(CamelModel):
    status: Literal["ok"] = "ok"
    recorder: str
    recorder_available: bool
    transcriber: str
    transcriber_reachable: bool
    transcriber_loaded: bool
    recording: RecordingState


class RecordingRequest(CamelModel):
    recording_id: HexId


class StartRecordingResponse(CamelModel):
    recording_id: str
    started_at: datetime


class StopRecordingResponse(CamelModel):
    recording_id: str
    duration_ms: int
    job_id: str


class CancelRecordingResponse(CamelModel):
    recording_id: str
    status: Literal["cancelled"] = "cancelled"


class TranscriptSegmentOut(CamelModel):
    id: str
    start_ms: int
    end_ms: int
    text: str


class TranscriptOut(CamelModel):
    text: str
    language: str
    duration_ms: int
    segments: list[TranscriptSegmentOut]


class ErrorOut(CamelModel):
    code: str
    message: str
    retryable: bool


class TranscriptionStatusResponse(CamelModel):
    job_id: str
    status: JobStatus
    progress: int | None
    audio_duration_ms: int
    transcript: TranscriptOut | None = None
    error: ErrorOut | None = None

    @classmethod
    def from_job(cls, job: Job) -> "TranscriptionStatusResponse":
        transcript = None
        if job.result is not None:
            transcript = TranscriptOut(
                text=job.result.text,
                language=job.result.language,
                duration_ms=job.result.duration_ms,
                segments=[
                    TranscriptSegmentOut(id=f"seg-{i}", start_ms=s.start_ms, end_ms=s.end_ms, text=s.text)
                    for i, s in enumerate(job.result.segments, start=1)
                ],
            )
        error = None
        if job.error is not None:
            error = ErrorOut(code=job.error.code, message=job.error.message, retryable=job.error.retryable)
        return cls(
            job_id=job.id,
            status=job.status,
            progress=job.progress,
            audio_duration_ms=job.audio_duration_ms,
            transcript=transcript,
            error=error,
        )
