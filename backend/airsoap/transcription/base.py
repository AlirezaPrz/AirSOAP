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

    async def is_reachable(self) -> bool: ...
