"""A recorder for development on macOS: no microphone, just synthetic audio.

Like arecord, it writes the WAV as it goes, so the live-transcription path can be exercised end
to end on a Mac: stretches of a quiet tone ("speech") separated by short silences ("pauses").
"""

import asyncio
import contextlib
import struct
import time
from pathlib import Path

from asyncer import asyncify

from ..audio import CANONICAL_HEADER_SIZE, SAMPLE_RATE, synth_tone, write_wav

SPEECH_MS = 2600
PAUSE_MS = 900
CHUNK_MS = 200


def pattern(start_ms: int, duration_ms: int) -> bytes:
    """Synthetic audio for [start, start + duration): tone during "speech", zeros during "pauses"."""
    out = bytearray()
    cycle = SPEECH_MS + PAUSE_MS
    position = start_ms
    end = start_ms + duration_ms
    while position < end:
        in_cycle = position % cycle
        if in_cycle < SPEECH_MS:
            length = min(SPEECH_MS - in_cycle, end - position)
            out += synth_tone(length)
        else:
            length = min(cycle - in_cycle, end - position)
            out += b"\x00\x00" * (SAMPLE_RATE * length // 1000)
        position += length
    return bytes(out)


class MockRecorder:
    name = "mock"

    def __init__(self, *, max_seconds: int) -> None:
        self._max_seconds = max_seconds

    def is_available(self) -> bool:
        return True

    async def start(self, path: Path) -> "MockRecordingProcess":
        process = MockRecordingProcess(path, max_seconds=self._max_seconds)
        await asyncify(write_wav)(path, b"")  # header now, data as it "records"
        process.begin()
        return process


class MockRecordingProcess:
    def __init__(self, path: Path, *, max_seconds: int) -> None:
        self._path = path
        self._max_seconds = max_seconds
        self._started = time.monotonic()
        self._written_ms = 0
        self._writer: asyncio.Task[None] | None = None

    def begin(self) -> None:
        self._writer = asyncio.create_task(self._write(), name="airsoap-mock-recorder")

    def _elapsed_ms(self) -> int:
        return min(round((time.monotonic() - self._started) * 1000), self._max_seconds * 1000)

    def _append(self, upto_ms: int) -> None:
        if upto_ms <= self._written_ms:
            return
        with self._path.open("ab") as handle:
            handle.write(pattern(self._written_ms, upto_ms - self._written_ms))
        self._written_ms = upto_ms

    async def _write(self) -> None:
        while True:
            await asyncio.sleep(CHUNK_MS / 1000)
            await asyncify(self._append)(self._elapsed_ms())

    async def _stop_writer(self) -> None:
        if self._writer is not None:
            self._writer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._writer

    async def stop(self) -> None:
        await self._stop_writer()
        await asyncify(self._append)(self._elapsed_ms())
        await asyncify(_fix_sizes)(self._path)

    async def abort(self) -> None:
        await self._stop_writer()


def _fix_sizes(path: Path) -> None:
    size = path.stat().st_size
    with path.open("r+b") as handle:
        handle.seek(4)
        handle.write(struct.pack("<I", size - 8))
        handle.seek(40)
        handle.write(struct.pack("<I", size - CANONICAL_HEADER_SIZE))
