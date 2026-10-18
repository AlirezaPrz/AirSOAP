"""Reading the WAV file arecord is still writing.

arecord writes a canonical 44-byte header (with placeholder sizes) and then appends PCM as it
records, so the audio so far is everything after the header. The file itself is not changed:
arecord still finalizes it on stop, and it is deleted as before once transcription ends.
"""

import io
import os
import sys
import wave
from array import array
from pathlib import Path

from ..audio import CANONICAL_HEADER_SIZE, CHANNELS, SAMPLE_RATE, SAMPLE_WIDTH

BYTES_PER_MS = SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS // 1000


class GrowingWav:
    def __init__(self, path: Path) -> None:
        self._path = path

    def available_ms(self) -> int:
        try:
            size = os.stat(self._path).st_size
        except FileNotFoundError:
            return 0
        return max(0, size - CANONICAL_HEADER_SIZE) // BYTES_PER_MS

    def read(self, start_ms: int, end_ms: int) -> array:
        """16-bit samples between two times. Returns fewer if the file is not that long yet."""
        if end_ms <= start_ms:
            return array("h")
        offset = CANONICAL_HEADER_SIZE + start_ms * BYTES_PER_MS
        length = (end_ms - start_ms) * BYTES_PER_MS
        with open(self._path, "rb") as handle:
            handle.seek(offset)
            data = handle.read(length)
        samples = array("h")
        samples.frombytes(data[: len(data) - len(data) % SAMPLE_WIDTH])
        if sys.byteorder == "big":
            samples.byteswap()
        return samples


def to_wav(samples: array) -> bytes:
    """A complete in-memory WAV for one block, the format whisper-server expects."""
    data = array("h", samples)
    if sys.byteorder == "big":
        data.byteswap()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(CHANNELS)
        writer.setsampwidth(SAMPLE_WIDTH)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(data.tobytes())
    return buffer.getvalue()
