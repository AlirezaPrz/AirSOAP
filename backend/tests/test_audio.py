import struct
import wave
from pathlib import Path

import pytest

from airsoap.audio import InvalidAudioError, inspect_wav, repair_wav_header, synth_tone, write_wav


def test_inspect_reports_duration(tmp_path: Path):
    path = tmp_path / "a.wav"
    write_wav(path, synth_tone(2500))
    info = inspect_wav(path)
    assert info.frames == 40_000
    assert info.duration_ms == 2500


def test_inspect_rejects_wrong_format(tmp_path: Path):
    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(44_100)
        writer.writeframes(b"\x00" * 400)
    with pytest.raises(InvalidAudioError):
        inspect_wav(path)


def test_inspect_rejects_garbage_and_missing(tmp_path: Path):
    garbage = tmp_path / "garbage.wav"
    garbage.write_bytes(b"not a wav file at all")
    with pytest.raises(InvalidAudioError):
        inspect_wav(garbage)
    with pytest.raises(InvalidAudioError):
        inspect_wav(tmp_path / "missing.wav")


def test_repair_fixes_placeholder_sizes_from_killed_arecord(tmp_path: Path):
    path = tmp_path / "killed.wav"
    write_wav(path, synth_tone(1000))
    with path.open("r+b") as handle:  # what arecord leaves behind when it cannot finalize
        handle.seek(4)
        handle.write(struct.pack("<I", 0x7FFFFFFF))
        handle.seek(40)
        handle.write(struct.pack("<I", 0x7FFFFFFF))
    assert repair_wav_header(path) is True
    assert inspect_wav(path).duration_ms == 1000
    assert repair_wav_header(path) is False


def test_repair_ignores_non_canonical_files(tmp_path: Path):
    path = tmp_path / "short.wav"
    path.write_bytes(b"RIFF")
    assert repair_wav_header(path) is False
