"""The transcriber interface shared by the whisper-server and mock implementations."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class TranscriptSegment:
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    language: str
    duration_ms: int
    segments: tuple[TranscriptSegment, ...]


class TranscriptionError(Exception):
    """Transcription failed. ``message`` is safe to show; it never includes transcript text."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Transcriber(Protocol):
    name: str

    async def transcribe(self, path: Path, duration_ms: int) -> TranscriptResult:
        """Transcribe the whole recording in a single request."""
        ...

    async def is_reachable(self) -> bool:
        """Able to take a transcription request (for on-demand services: able to start)."""
        ...

    def is_loaded(self) -> bool:
        """The model is in memory right now."""
        ...

    async def release(self) -> None:
        """Free the model's memory now (stops a managed whisper-server). Safe when idle."""
        ...

    async def close(self) -> None:
        """Release anything held at shutdown."""
        ...
