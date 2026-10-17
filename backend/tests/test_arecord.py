"""ArecordRecorder against a stand-in ``arecord`` script, so the real process
handling (start, SIGINT, startup failure) is tested without a microphone."""

import asyncio
import sys
from pathlib import Path

import pytest

from airsoap.audio import inspect_wav, repair_wav_header
from airsoap.recording import ArecordRecorder, RecorderError

pytestmark = pytest.mark.anyio

# Writes a canonical WAV header with placeholder sizes, streams silence and, like
# arecord, rewrites the sizes when interrupted with SIGINT (exiting non-zero).
FAKE_ARECORD = """\
import signal, struct, sys, time
path = sys.argv[-1]
out = open(path, "wb")
fmt = struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
placeholder = struct.pack("<I", 0x7FFFFFFF)
out.write(b"RIFF" + placeholder + b"WAVEfmt " + fmt + b"data" + placeholder)
def finish(*_):
    size = out.tell()
    out.seek(4); out.write(struct.pack("<I", size - 8))
    out.seek(40); out.write(struct.pack("<I", size - 44))
    out.close()
    sys.exit(1)
signal.signal(signal.SIGINT, finish)
while True:
    out.write(b"\\x00\\x00" * 1600)  # 0.1 s
    out.flush()
    time.sleep(0.1)
"""

FAILING_ARECORD = """\
import sys
sys.stderr.write("arecord: main:850: audio open error: Device or resource busy\\n")
sys.exit(1)
"""


def make_binary(tmp_path: Path, name: str, source: str) -> str:
    script = tmp_path / name
    script.write_text(f"#!{sys.executable}\n{source}")
    script.chmod(0o755)
    return str(script)


def test_command_is_an_argument_list_with_the_tested_format():
    recorder = ArecordRecorder(binary="arecord", device="plughw:CARD=MICROPHONE,DEV=0", max_seconds=1800)
    assert recorder.build_command(Path("/run/user/1000/airsoap/rec-x.wav")) == [
        "arecord",
        "-q",
        "-D",
        "plughw:CARD=MICROPHONE,DEV=0",
        "-f",
        "S16_LE",
        "-r",
        "16000",
        "-c",
        "1",
        "-t",
        "wav",
        "-d",
        "1800",
        "/run/user/1000/airsoap/rec-x.wav",
    ]


async def test_start_and_stop_produces_a_valid_wav(tmp_path: Path):
    binary = make_binary(tmp_path, "arecord", FAKE_ARECORD)
    recorder = ArecordRecorder(binary=binary, device="default", max_seconds=60, startup_grace=0.2)
    path = tmp_path / "rec.wav"
    process = await recorder.start(path)
    # Unlike the C binary, the Python stand-in takes a moment to open its file.
    async with asyncio.timeout(5):
        while not path.exists() or path.stat().st_size <= 44:
            await asyncio.sleep(0.05)
    await process.stop()
    repair_wav_header(path)
    assert inspect_wav(path).frames > 0


async def test_device_failure_during_startup_is_reported(tmp_path: Path):
    binary = make_binary(tmp_path, "arecord", FAILING_ARECORD)
    recorder = ArecordRecorder(binary=binary, device="default", max_seconds=60, startup_grace=1.0)
    with pytest.raises(RecorderError) as excinfo:
        await recorder.start(tmp_path / "rec.wav")
    assert excinfo.value.code == "RECORDING_FAILED"
    assert "busy" not in excinfo.value.message  # raw device output stays out of API errors


async def test_missing_binary(tmp_path: Path):
    recorder = ArecordRecorder(binary=str(tmp_path / "nope"), device="default", max_seconds=60)
    assert recorder.is_available() is False
    with pytest.raises(RecorderError) as excinfo:
        await recorder.start(tmp_path / "rec.wav")
    assert excinfo.value.code == "RECORDER_UNAVAILABLE"


async def test_abort_terminates_quickly(tmp_path: Path):
    binary = make_binary(tmp_path, "arecord", FAKE_ARECORD)
    recorder = ArecordRecorder(binary=binary, device="default", max_seconds=60, startup_grace=0.2)
    process = await recorder.start(tmp_path / "rec.wav")
    await process.abort()
