"""A transcriber for development on macOS. Returns synthetic, fictional text."""

import asyncio
from pathlib import Path

from .base import TranscriptResult, TranscriptSegment

_SYNTHETIC_LINES = (
    "Good morning, what brings you in today?",
    "I have had a sore throat for about three days.",
    "Any fever or cough?",
    "A mild fever last night, no cough.",
    "Let me take a look at your throat.",
    "Your tonsils are swollen. I will run a quick strep test.",
)
_SECONDS_PER_LINE = 4


class MockTranscriber:
    name = "mock"

    def __init__(self, *, delay_seconds: float) -> None:
        self._delay = delay_seconds

    async def transcribe(self, path: Path, duration_ms: int) -> TranscriptResult:
        await asyncio.sleep(self._delay)
        count = min(len(_SYNTHETIC_LINES), max(1, duration_ms // (_SECONDS_PER_LINE * 1000)))
        step = duration_ms // count
        segments = tuple(
            TranscriptSegment(start_ms=i * step, end_ms=(i + 1) * step, text=_SYNTHETIC_LINES[i])
            for i in range(count)
        )
        return TranscriptResult(
            text=" ".join(segment.text for segment in segments),
            language="en",
            duration_ms=duration_ms,
            segments=segments,
        )

    async def is_reachable(self) -> bool:
        return True

    def is_loaded(self) -> bool:
        return False

    async def close(self) -> None:
        return None
