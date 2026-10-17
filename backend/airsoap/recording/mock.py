"""A recorder for development on macOS: no microphone, just a synthetic tone."""

import time
from pathlib import Path

from asyncer import asyncify

from ..audio import synth_tone, write_wav


class MockRecorder:
    name = "mock"

    def __init__(self, *, max_seconds: int) -> None:
        self._max_seconds = max_seconds

    def is_available(self) -> bool:
        return True

    async def start(self, path: Path) -> "MockRecordingProcess":
        return MockRecordingProcess(path, max_seconds=self._max_seconds)


class MockRecordingProcess:
    def __init__(self, path: Path, *, max_seconds: int) -> None:
        self._path = path
        self._max_seconds = max_seconds
        self._started = time.monotonic()

    async def stop(self) -> None:
        elapsed_ms = round((time.monotonic() - self._started) * 1000)
        duration_ms = min(elapsed_ms, self._max_seconds * 1000)
        await asyncify(write_wav)(self._path, synth_tone(duration_ms))

    async def abort(self) -> None:
        # Nothing was written yet.
        return None
