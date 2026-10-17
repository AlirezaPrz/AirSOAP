"""A whisper-server transcriber whose process only runs while it has work."""

import asyncio
from pathlib import Path

from .base import TranscriptResult
from .whisper_process import WhisperProcessManager
from .whisper_server import WhisperServerTranscriber


class ManagedWhisperTranscriber:
    name = "whisper-server (on demand)"

    def __init__(self, *, client: WhisperServerTranscriber, manager: WhisperProcessManager) -> None:
        self._client = client
        self._manager = manager

    async def transcribe(self, path: Path, duration_ms: int) -> TranscriptResult:
        try:
            async with self._manager.lease():
                return await self._client.transcribe(path, duration_ms)
        except asyncio.CancelledError:
            # whisper-server would otherwise finish the request it is on and keep the CPU busy.
            await asyncio.shield(self._manager.stop())
            raise

    async def is_reachable(self) -> bool:
        """Ready to transcribe: either running, or installed and able to start on demand."""
        if self._manager.is_running:
            return await self._client.is_reachable()
        return self._manager.prerequisites_ok()

    def is_loaded(self) -> bool:
        return self._manager.is_running

    async def release(self) -> None:
        await self._manager.stop()

    async def close(self) -> None:
        await self.release()
