"""One heavy job at a time, and only one model in memory.

The Raspberry Pi has 4 GB of RAM: whisper-server (about 650 MB) and Qwen 2.5 3B
(about 2 GB) must not be loaded together. Every transcription and every AI task
takes this lock, and switching from one to the other frees the other's memory first.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ..transcription import Transcriber
from .base import AiClient


class ModelGate:
    def __init__(self, *, transcriber: Transcriber, ai: AiClient) -> None:
        self._transcriber = transcriber
        self._ai = ai
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def for_transcription(self) -> AsyncIterator[None]:
        async with self._lock:
            await self._ai.unload()  # free Qwen before whisper-server loads
            yield

    @asynccontextmanager
    async def for_ai(self) -> AsyncIterator[None]:
        async with self._lock:
            await self._transcriber.release()  # free whisper-server before Qwen loads
            yield
