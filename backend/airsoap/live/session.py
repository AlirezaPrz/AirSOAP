"""One recording's live transcript: a worker that transcribes each stretch of speech as it ends.

The live transcript is the final transcript (the Pi benchmarks showed a second whole-file pass
would add minutes after Stop). If the live worker fails, the recording carries on and the stop
path falls back to whole-file transcription, so a failure never loses the visit.

Nothing here logs transcript text: only IDs, counts and timings.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from asyncer import asyncify

from ..transcription import TranscriptionError, TranscriptResult, TranscriptSegment
from .engine import LiveTranscriber
from .segmenter import Block, BlockPlanner
from .source import GrowingWav

logger = logging.getLogger(__name__)

POLL_SECONDS = 0.25


class LiveStatus(StrEnum):
    STARTING = "starting"  # waiting for the model to load (or for an AI task to finish)
    LISTENING = "listening"  # up to date; waiting for the next pause
    TRANSCRIBING = "transcribing"  # working on a stretch of speech
    BEHIND = "behind"  # more than live_behind_seconds of speech is waiting
    FAILED = "failed"  # live transcription stopped; the recording continues
    FINISHING = "finishing"  # recording stopped; transcribing the last stretch
    DONE = "done"


@dataclass(frozen=True)
class LiveSegment:
    id: str
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class LiveSnapshot:
    revision: int
    status: LiveStatus
    behind_ms: int
    segments: tuple[LiveSegment, ...]


class LiveSession:
    def __init__(
        self,
        *,
        recording_id: str,
        source: GrowingWav,
        transcriber: LiveTranscriber,
        pause_ms: int,
        max_block_ms: int,
        behind_ms: int,
        hold_model: Callable[[], contextlib.AbstractAsyncContextManager[None]] | None = None,
    ) -> None:
        self.recording_id = recording_id
        self._source = source
        self._transcriber = transcriber
        self._planner = BlockPlanner(pause_ms=pause_ms, max_block_ms=max_block_ms)
        self._behind_ms = behind_ms
        self._hold_model = hold_model
        self._segments: list[LiveSegment] = []
        self._status = LiveStatus.STARTING
        self._revision = 0
        self._changed = asyncio.Condition()
        self._stopping = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._resources = contextlib.AsyncExitStack()
        self._held = False
        self._available_ms = 0  # how much has been recorded so far
        self._in_progress_ms: int | None = None  # start of the block being transcribed right now

    # -- state -------------------------------------------------------------

    @property
    def status(self) -> LiveStatus:
        return self._status

    @property
    def backlog_ms(self) -> int:
        """Recorded but not yet transcribed (or skipped as silence): how far behind the live view is."""
        done_until = self._planner.cut_ms if self._in_progress_ms is None else self._in_progress_ms
        return max(0, self._available_ms - done_until)

    @property
    def failed(self) -> bool:
        return self._status is LiveStatus.FAILED

    def snapshot(self) -> LiveSnapshot:
        return LiveSnapshot(
            revision=self._revision,
            status=self._status,
            behind_ms=self.backlog_ms,
            segments=tuple(self._segments),
        )

    async def wait_for_change(self, after_revision: int, wait_seconds: float) -> LiveSnapshot:
        """The next snapshot newer than ``after_revision``, or the current one after ``wait_seconds``."""
        async with self._changed:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._changed.wait_for(lambda: self._revision > after_revision), wait_seconds
                )
        return self.snapshot()

    async def _publish(self, status: LiveStatus | None = None) -> None:
        if status is not None:
            self._status = status
        self._revision += 1
        async with self._changed:
            self._changed.notify_all()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"airsoap-live-{self.recording_id}")

    async def finish(self, final_ms: int) -> TranscriptResult | None:
        """After the recording stopped: transcribe the last stretch and return the whole transcript.

        Returns None if live transcription failed, so the caller can fall back to the whole file.
        """
        self._stopping.set()
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
        try:
            if self.failed:
                return None
            await self._publish(LiveStatus.FINISHING)
            await self._catch_up(final_ms)
            tail = self._planner.remainder(final_ms)
            if tail is not None:
                await self._transcribe(tail)
            await self._publish(LiveStatus.DONE)
            segments = tuple(TranscriptSegment(s.start_ms, s.end_ms, s.text) for s in self._segments)
            return TranscriptResult(
                text=" ".join(s.text for s in segments),
                language="en",
                duration_ms=final_ms,
                segments=segments,
            )
        except (TranscriptionError, OSError) as exc:
            await self._fail(exc)
            return None
        finally:
            await self._release()

    async def abort(self) -> None:
        """Cancel, sign-out, session expiry or shutdown: stop at once and keep nothing."""
        self._stopping.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._segments.clear()
        await self._release()
        await self._publish(LiveStatus.DONE)

    async def _release(self) -> None:
        with contextlib.suppress(Exception):
            await self._transcriber.stop()
        with contextlib.suppress(Exception):
            await self._resources.aclose()
        self._held = False

    # -- the worker ----------------------------------------------------------

    async def _run(self) -> None:
        try:
            if self._hold_model is not None:
                # Only one heavy job at a time: unloads Qwen, and waits for a running AI task.
                await self._resources.enter_async_context(self._hold_model())
                self._held = True
            await self._transcriber.start()
            await self._publish(LiveStatus.LISTENING)
            while not self._stopping.is_set():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stopping.wait(), POLL_SECONDS)
                if self._stopping.is_set():
                    break
                await self._catch_up(await asyncify(self._source.available_ms)())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - any failure falls back to whole-file transcription
            await self._fail(exc)

    async def _catch_up(self, available_ms: int) -> None:
        """Feed new audio and transcribe every stretch of speech that is ready."""
        self._available_ms = max(self._available_ms, available_ms)
        fed = self._planner.fed_ms
        if available_ms - fed >= 100:
            samples = await asyncify(self._source.read)(fed, available_ms - (available_ms - fed) % 100)
            self._planner.feed(samples)
        while (block := self._planner.next_block()) is not None:
            self._in_progress_ms = block.start_ms
            await self._update_status()  # say "behind" before starting, not after catching up
            try:
                await self._transcribe(block)
            finally:
                self._in_progress_ms = None
            if self._stopping.is_set() and self._status is not LiveStatus.FINISHING:
                return
        await self._update_status()

    async def _update_status(self) -> None:
        if self._status in (LiveStatus.FINISHING, LiveStatus.FAILED, LiveStatus.DONE):
            return
        status = LiveStatus.BEHIND if self.backlog_ms > self._behind_ms else LiveStatus.LISTENING
        if self._status is not status:
            await self._publish(status)

    async def _transcribe(self, block: Block) -> None:
        if self._status not in (LiveStatus.FINISHING, LiveStatus.BEHIND):
            await self._publish(LiveStatus.TRANSCRIBING)
        samples = await asyncify(self._source.read)(block.start_ms, block.end_ms)
        started = time.monotonic()
        found = await self._transcriber.transcribe_block(samples, block.start_ms)
        for segment in found:
            self._segments.append(
                LiveSegment(
                    id=f"seg-{len(self._segments) + 1}",
                    start_ms=segment.start_ms,
                    end_ms=max(segment.start_ms, segment.end_ms),
                    text=segment.text,
                )
            )
        logger.info(
            "live recording=%s block_ms=%d forced=%s segments=%d elapsed_ms=%d",
            self.recording_id,
            block.end_ms - block.start_ms,
            block.forced,
            len(found),
            round((time.monotonic() - started) * 1000),
        )
        await self._publish()

    async def _fail(self, exc: BaseException) -> None:
        code = exc.code if isinstance(exc, TranscriptionError) else type(exc).__name__
        logger.warning(
            "live recording=%s stopped transcribing (%s); whole-file fallback", self.recording_id, code
        )
        await self._publish(LiveStatus.FAILED)
        await self._release()
