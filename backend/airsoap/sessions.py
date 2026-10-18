"""The single active recording: start, stop (which queues transcription) and cancel."""

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from asyncer import asyncify

from .audio import InvalidAudioError, inspect_wav, repair_wav_header
from .errors import ApiError
from .jobs import Job, JobManager
from .live import LiveSession
from .recording import Recorder, RecorderError, RecordingProcess
from .storage import WorkDir

logger = logging.getLogger(__name__)

# Shorter than this and there is nothing worth sending to Whisper.
MIN_AUDIO_MS = 300


class RecordingState(StrEnum):
    IDLE = "idle"
    RECORDING = "recording"


@dataclass(frozen=True)
class ActiveRecording:
    id: str
    path: Path
    started_at: datetime
    process: RecordingProcess
    # The signed-in account that started it. Only that account can stop or cancel it.
    owner_id: str | None = None
    live: LiveSession | None = None


@dataclass(frozen=True)
class StoppedRecording:
    recording_id: str
    duration_ms: int
    job: Job


def _no_active_recording() -> ApiError:
    return ApiError(409, "NO_ACTIVE_RECORDING", "There is no active recording with this ID.")


LiveFactory = Callable[[str, Path], LiveSession]


class RecordingSessions:
    def __init__(
        self,
        *,
        recorder: Recorder,
        storage: WorkDir,
        jobs: JobManager,
        live_factory: LiveFactory | None = None,
    ) -> None:
        self._recorder = recorder
        self._storage = storage
        self._jobs = jobs
        self._live_factory = live_factory
        self._lock = asyncio.Lock()
        self._active: ActiveRecording | None = None

    @property
    def state(self) -> RecordingState:
        return RecordingState.RECORDING if self._active else RecordingState.IDLE

    @property
    def active_owner(self) -> str | None:
        return self._active.owner_id if self._active else None

    async def start(self, owner_id: str | None = None) -> ActiveRecording:
        async with self._lock:
            if self._active is not None:
                raise ApiError(409, "RECORDING_IN_PROGRESS", "A recording is already in progress.")
            path = self._storage.new_audio_path()
            try:
                process = await self._recorder.start(path)
            except RecorderError as exc:
                self._storage.delete(path)
                logger.warning("recording failed to start code=%s", exc.code)
                raise ApiError(503, exc.code, exc.message) from exc
            recording_id = uuid.uuid4().hex
            live = self._live_factory(recording_id, path) if self._live_factory else None
            if live is not None:
                live.start()
            self._active = ActiveRecording(
                id=recording_id,
                path=path,
                started_at=datetime.now(UTC),
                process=process,
                owner_id=owner_id,
                live=live,
            )
            logger.info("recording=%s started live=%s", recording_id, live is not None)
            return self._active

    @property
    def live_enabled(self) -> bool:
        return self._live_factory is not None

    def live_for(self, recording_id: str, owner_id: str | None) -> LiveSession:
        """The live transcript of the caller's own active recording; anything else is not found."""
        active = self._active
        if (
            active is None
            or active.live is None
            or active.id != recording_id
            or (owner_id is not None and active.owner_id != owner_id)
        ):
            raise _no_active_recording()
        return active.live

    async def stop(self, recording_id: str, owner_id: str | None = None) -> StoppedRecording:
        async with self._lock:
            active = self._take(recording_id, owner_id)
            try:
                await active.process.stop()
                await asyncify(repair_wav_header)(active.path)
                info = await asyncify(inspect_wav)(active.path)
            except (RecorderError, InvalidAudioError, OSError) as exc:
                await self._abort_live(active)
                self._storage.delete(active.path)
                logger.warning("recording=%s could not be finalized (%s)", active.id, type(exc).__name__)
                raise ApiError(500, "RECORDING_FAILED", "The recording could not be saved.") from exc

            if info.duration_ms < MIN_AUDIO_MS:
                await self._abort_live(active)
                self._storage.delete(active.path)
                raise ApiError(422, "EMPTY_RECORDING", "The recording contains no audio.")
            try:
                job = self._jobs.submit(
                    active.path, info.duration_ms, owner_id=active.owner_id, live=active.live
                )
            except ApiError:
                await self._abort_live(active)
                self._storage.delete(active.path)
                raise
            logger.info("recording=%s stopped audio_ms=%d job=%s", active.id, info.duration_ms, job.id)
            return StoppedRecording(recording_id=active.id, duration_ms=info.duration_ms, job=job)

    async def cancel(self, recording_id: str, owner_id: str | None = None) -> None:
        async with self._lock:
            active = self._take(recording_id, owner_id)
            try:
                await self._abort_live(active)
                await active.process.abort()
            finally:
                self._storage.delete(active.path)
            logger.info("recording=%s cancelled", active.id)

    async def close(self) -> None:
        async with self._lock:
            if self._active is None:
                return
            active, self._active = self._active, None
            try:
                await self._abort_live(active)
                await active.process.abort()
            finally:
                self._storage.delete(active.path)

    async def discard_owner(self, owner_id: str) -> bool:
        """Stop and delete this account's recording, e.g. after sign-out or session expiry."""
        async with self._lock:
            if self._active is None or self._active.owner_id != owner_id:
                return False
            active, self._active = self._active, None
            try:
                await self._abort_live(active)
                await active.process.abort()
            finally:
                self._storage.delete(active.path)
            logger.info("recording=%s discarded because its session ended", active.id)
            return True

    @staticmethod
    async def _abort_live(active: ActiveRecording) -> None:
        if active.live is not None:
            await active.live.abort()

    def _take(self, recording_id: str, owner_id: str | None) -> ActiveRecording:
        if (
            self._active is None
            or self._active.id != recording_id
            or (owner_id is not None and self._active.owner_id != owner_id)
        ):
            raise _no_active_recording()
        active, self._active = self._active, None
        return active
