"""WAV helpers for the 16 kHz, mono, 16-bit PCM format the recorder produces."""

import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2  # bytes, S16_LE
CANONICAL_HEADER_SIZE = 44


class InvalidAudioError(ValueError):
    """The file is missing, unreadable or not in the expected format."""


@dataclass(frozen=True)
class WavInfo:
    frames: int
    sample_rate: int

    @property
    def duration_ms(self) -> int:
        return round(self.frames * 1000 / self.sample_rate)


def repair_wav_header(path: Path) -> bool:
    """Fix the RIFF and data sizes of a canonical 44-byte header if they disagree with the file size.

    ``arecord`` rewrites these sizes when it exits cleanly; if it was killed they
    keep a placeholder value. Returns True when the header was rewritten.
    """
    size = path.stat().st_size
    if size < CANONICAL_HEADER_SIZE:
        return False
    with path.open("r+b") as handle:
        header = handle.read(CANONICAL_HEADER_SIZE)
        if header[0:4] != b"RIFF" or header[8:12] != b"WAVE" or header[36:40] != b"data":
            return False
        riff_size = size - 8
        data_size = size - CANONICAL_HEADER_SIZE
        current_riff, current_data = (
            struct.unpack_from("<I", header, 4)[0],
            struct.unpack_from("<I", header, 40)[0],
        )
        if current_riff == riff_size and current_data == data_size:
            return False
        handle.seek(4)
        handle.write(struct.pack("<I", riff_size))
        handle.seek(40)
        handle.write(struct.pack("<I", data_size))
    return True


def inspect_wav(path: Path) -> WavInfo:
    """Validate the format and return the frame count."""
    try:
        with wave.open(str(path), "rb") as reader:
            channels, width, rate, frames = (
                reader.getnchannels(),
                reader.getsampwidth(),
                reader.getframerate(),
                reader.getnframes(),
            )
    except (OSError, EOFError, wave.Error) as exc:
        raise InvalidAudioError("audio file could not be read") from exc
    if (channels, width, rate) != (CHANNELS, SAMPLE_WIDTH, SAMPLE_RATE):
        raise InvalidAudioError("audio is not 16 kHz mono 16-bit PCM")
    return WavInfo(frames=frames, sample_rate=rate)


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(CHANNELS)
        writer.setsampwidth(SAMPLE_WIDTH)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(pcm)


def synth_tone(duration_ms: int, frequency: float = 220.0, amplitude: int = 1200) -> bytes:
    """A quiet sine tone, used by the mock recorder and tests in place of real speech."""
    frames = round(SAMPLE_RATE * duration_ms / 1000)
    step = 2 * math.pi * frequency / SAMPLE_RATE
    return b"".join(struct.pack("<h", round(amplitude * math.sin(step * i))) for i in range(frames))
