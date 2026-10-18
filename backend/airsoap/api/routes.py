import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import StreamingResponse

from ..auth.csrf import require_same_origin
from ..auth.deps import ActiveSessionDep, ClinicianDep, active_session, require_clinician
from ..live import LiveSession, LiveStatus
from .deps import ServicesDep
from .schemas import (
    CancelRecordingResponse,
    HealthResponse,
    LiveSegmentOut,
    LiveSnapshotOut,
    RecordingRequest,
    StartRecordingResponse,
    StopRecordingResponse,
    SystemStatusResponse,
    TranscriptionStatusResponse,
)

JobIdPath = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$", description="Transcription job ID")]
RecordingIdPath = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$", description="Recording ID")]
HEARTBEAT_SECONDS = 15.0
STREAM_ENDS = frozenset({LiveStatus.DONE, LiveStatus.FINISHING, LiveStatus.FAILED})

# Public: liveness only. It says nothing about components or activity.
health_router = APIRouter(prefix="/api", tags=["health"])
# Any signed-in role (with a role chosen).
system_router = APIRouter(
    prefix="/api/system",
    tags=["system"],
    dependencies=[Depends(require_same_origin), Depends(active_session)],
)
# Clinicians only. Recordings and jobs are also checked against their owner.
clinical = [Depends(require_same_origin), Depends(require_clinician)]
recording_router = APIRouter(prefix="/api/recording", tags=["recording"], dependencies=clinical)
transcription_router = APIRouter(prefix="/api/transcriptions", tags=["transcriptions"], dependencies=clinical)


@health_router.get("/health")
async def health() -> HealthResponse:
    return HealthResponse()


@system_router.get("/status")
async def system_status(_session: ActiveSessionDep, services: ServicesDep) -> SystemStatusResponse:
    reachable = await services.transcriber.is_reachable()
    return SystemStatusResponse(
        recorder=services.recorder.name,
        recorder_available=services.recorder.is_available(),
        transcriber=services.transcriber.name,
        transcriber_reachable=reachable,
        transcriber_loaded=services.transcriber.is_loaded(),
        ai=services.ai_client.name,
        ai_ready=await services.ai_client.is_ready(),
        recording=services.sessions.state,
        live=services.live.name if services.live else "off",
        live_available=services.live.available() if services.live else False,
    )


@recording_router.post("/start")
async def start_recording(user: ClinicianDep, services: ServicesDep) -> StartRecordingResponse:
    active = await services.sessions.start(owner_id=user.user.id)
    return StartRecordingResponse(
        recording_id=active.id, started_at=active.started_at, live=active.live is not None
    )


def _event(live: LiveSession) -> str:
    snapshot = live.snapshot()
    body = LiveSnapshotOut(
        recording_id=live.recording_id,
        revision=snapshot.revision,
        status=snapshot.status.value,
        behind_ms=snapshot.behind_ms,
        segments=[
            LiveSegmentOut(id=s.id, start_ms=s.start_ms, end_ms=s.end_ms, text=s.text)
            for s in snapshot.segments
        ],
    )
    return f"id: {snapshot.revision}\nevent: snapshot\ndata: {json.dumps(body.model_dump(by_alias=True))}\n\n"


@recording_router.get("/{recording_id}/live")
async def live_transcript(
    recording_id: RecordingIdPath, request: Request, user: ClinicianDep, services: ServicesDep
) -> StreamingResponse:
    """Server-sent events with the whole live transcript so far, each time it changes.

    Only the clinician who started the recording can follow it. The stream ends when the recording
    stops (the transcript then arrives through the transcription job, as before) or if live
    transcription fails (the recording carries on and is transcribed as a whole file on stop).
    The browser reconnects by itself; every event is complete, so nothing is lost.
    """
    live = services.sessions.live_for(recording_id, owner_id=user.user.id)

    async def stream() -> AsyncIterator[str]:
        revision = -1
        while True:
            snapshot = live.snapshot()
            if snapshot.revision != revision:
                revision = snapshot.revision
                yield _event(live)
                if snapshot.status in STREAM_ENDS:
                    return
            if await request.is_disconnected():
                return
            before = revision
            snapshot = await live.wait_for_change(revision, HEARTBEAT_SECONDS)
            if snapshot.revision == before:
                yield ": still recording\n\n"  # keeps proxies from closing an idle connection
            await asyncio.sleep(0)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@recording_router.post("/stop")
async def stop_recording(
    body: RecordingRequest, user: ClinicianDep, services: ServicesDep
) -> StopRecordingResponse:
    """Finalize the WAV file and queue it for transcription. Poll the returned job ID."""
    stopped = await services.sessions.stop(body.recording_id, owner_id=user.user.id)
    return StopRecordingResponse(
        recording_id=stopped.recording_id, duration_ms=stopped.duration_ms, job_id=stopped.job.id
    )


@recording_router.post("/cancel")
async def cancel_recording(
    body: RecordingRequest, user: ClinicianDep, services: ServicesDep
) -> CancelRecordingResponse:
    """Stop the active recording and delete its audio without transcribing it."""
    await services.sessions.cancel(body.recording_id, owner_id=user.user.id)
    return CancelRecordingResponse(recording_id=body.recording_id)


@transcription_router.get("/{job_id}")
async def get_transcription(
    job_id: JobIdPath, user: ClinicianDep, services: ServicesDep
) -> TranscriptionStatusResponse:
    return TranscriptionStatusResponse.from_job(services.jobs.get(job_id, owner_id=user.user.id))


@transcription_router.post("/{job_id}/cancel")
async def cancel_transcription(
    job_id: JobIdPath, user: ClinicianDep, services: ServicesDep
) -> TranscriptionStatusResponse:
    return TranscriptionStatusResponse.from_job(await services.jobs.cancel(job_id, owner_id=user.user.id))
