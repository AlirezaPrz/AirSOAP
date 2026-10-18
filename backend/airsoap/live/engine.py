"""What transcribes the live blocks: a dedicated whisper-server, or a clearly simulated one on the Mac."""

import asyncio
import contextlib
from array import array
from typing import Protocol

from ..transcription import TranscriptSegment
from ..transcription.whisper_process import WhisperProcessManager
from ..transcription.whisper_server import WhisperServerTranscriber
from .source import to_wav


class LiveTranscriber(Protocol):
    name: str

    def available(self) -> bool:
        """Installed and able to start."""
        ...

    async def start(self) -> None:
        """Load the model for a recording. May take a few seconds."""
        ...

    async def transcribe_block(self, samples: array, start_ms: int) -> list[TranscriptSegment]:
        """Segments for one block, with times from the start of the recording."""
        ...

    async def stop(self) -> None:
        """Free the model's memory. Safe to call when not started."""
        ...


class WhisperLiveTranscriber:
    """A second whisper-server (base.en on the Pi) on its own loopback port, loaded only while recording."""

    name = "whisper-server (live)"

    def __init__(self, *, client: WhisperServerTranscriber, manager: WhisperProcessManager) -> None:
        self._client = client
        self._manager = manager
        self._lease: contextlib.AsyncExitStack | None = None

    def available(self) -> bool:
        return self._manager.prerequisites_ok()

    async def start(self) -> None:
        if self._lease is not None:
            return
        stack = contextlib.AsyncExitStack()
        await stack.enter_async_context(self._manager.lease())
        self._lease = stack

    async def transcribe_block(self, samples: array, start_ms: int) -> list[TranscriptSegment]:
        duration_ms = len(samples) * 1000 // 16_000
        result = await self._client.transcribe_wav(to_wav(samples), duration_ms)
        return [
            TranscriptSegment(
                start_ms=s.start_ms + start_ms, end_ms=min(s.end_ms, duration_ms) + start_ms, text=s.text
            )
            for s in result.segments
        ]

    async def stop(self) -> None:
        lease, self._lease = self._lease, None
        if lease is not None:
            await lease.aclose()
        await self._manager.stop()  # free the RAM now rather than after the idle timer


# Fictional lines for the simulated engine, in the order of a short visit. No real patient.
SIMULATED_LINES = (
    "Good morning. What brings you in today?",
    "I've had a sore throat for about three days now.",
    "Does it hurt to swallow?",
    "Yes, especially in the morning.",
    "Have you had any fever or chills?",
    "I felt hot last night, around one hundred and one.",
    "Any cough or runny nose?",
    "No cough. Just the throat.",
    "Let me take a look. Your tonsils are swollen with some white patches.",
    "I'm going to do a rapid strep test.",
    "The rapid test is positive.",
    "I'll prescribe amoxicillin, five hundred milligrams twice a day for ten days.",
    "Should I keep taking ibuprofen?",
    "Yes, for pain and fever. Drink plenty of fluids and rest.",
)


class MockLiveTranscriber:
    """Development on the Mac: answers each block with the next fictional line. Never a real result."""

    name = "simulated"

    def __init__(self, *, delay_seconds: float = 0.3) -> None:
        self._delay = delay_seconds
        self._next = 0

    def available(self) -> bool:
        return True

    async def start(self) -> None:
        self._next = 0

    async def transcribe_block(self, samples: array, start_ms: int) -> list[TranscriptSegment]:
        await asyncio.sleep(self._delay)
        text = SIMULATED_LINES[self._next % len(SIMULATED_LINES)]
        self._next += 1
        return [
            TranscriptSegment(start_ms=start_ms, end_ms=start_ms + len(samples) * 1000 // 16_000, text=text)
        ]

    async def stop(self) -> None:
        return None
