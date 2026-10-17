"""The recorder interface shared by the arecord and mock implementations."""

from pathlib import Path
from typing import Protocol


class RecorderError(Exception):
    """A recorder failed to start or stop. ``message`` is safe to show to the user."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RecordingProcess(Protocol):
    async def stop(self) -> None:
        """Stop capturing and leave a finalized WAV file at the target path."""
        ...

    async def abort(self) -> None:
        """Stop capturing as quickly as possible; the file may be incomplete."""
        ...


class Recorder(Protocol):
    name: str

    def is_available(self) -> bool: ...

    async def start(self, path: Path) -> RecordingProcess:
        """Begin capturing 16 kHz mono 16-bit PCM WAV audio into ``path``."""
        ...
