#!/usr/bin/env python3
"""Make the benchmark's synthetic consultation audio on a Mac (uses macOS `say` and `afconvert`).

Two different voices read the fictional dialogue in bench_dialogue.py, with short pauses
between turns, into one 16 kHz mono 16-bit WAV, the format arecord produces on the Pi.

    python3 scripts/make_bench_audio.py ~/Desktop/bench-consultation.wav
    scp ~/Desktop/bench-consultation.wav ai-innovate@raspberrypi.local:

No real patient and no real voice is involved.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from bench_dialogue import DIALOGUE

RATE = 16000
PAUSE_SECONDS = 0.7


def available_voices() -> set[str]:
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, check=True).stdout  # noqa: S607
    return {line.split("  ")[0].strip() for line in out.splitlines() if line.strip()}


def pick(preferred: list[str], voices: set[str]) -> str:
    for name in preferred:
        if name in voices:
            return name
    sys.exit(
        f"None of these voices are installed: {', '.join(preferred)}. Pass --clinician-voice/--patient-voice."
    )


def speak(text: str, voice: str, workdir: Path, index: int) -> bytes:
    aiff = workdir / f"turn-{index}.aiff"
    wav = workdir / f"turn-{index}.wav"
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)  # noqa: S603, S607
    convert = ["afconvert", "-f", "WAVE", "-d", f"LEI16@{RATE}", "-c", "1", str(aiff), str(wav)]
    subprocess.run(convert, check=True)  # noqa: S603
    with wave.open(str(wav), "rb") as source:
        if source.getframerate() != RATE or source.getnchannels() != 1 or source.getsampwidth() != 2:
            sys.exit("afconvert did not produce 16 kHz mono 16-bit audio")
        return source.readframes(source.getnframes())


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--clinician-voice", help="default: Daniel or another installed English voice")
    parser.add_argument(
        "--patient-voice", help="default: Shelley (English (US)) or another installed English voice"
    )
    args = parser.parse_args()
    if not (shutil.which("say") and shutil.which("afconvert")):
        sys.exit("This script needs macOS (say and afconvert).")

    voices = available_voices()
    clinician = args.clinician_voice or pick(["Daniel", "Eddy (English (UK))", "Alex", "Fred"], voices)
    patient = args.patient_voice or pick(
        ["Shelley (English (US))", "Samantha", "Flo (English (US))", "Sandy (English (US))", "Kathy"], voices
    )
    silence = b"\x00\x00" * int(RATE * PAUSE_SECONDS)
    frames = bytearray(silence)
    with tempfile.TemporaryDirectory() as tmp:
        for index, (speaker, text) in enumerate(DIALOGUE):
            frames += speak(text, clinician if speaker == "clinician" else patient, Path(tmp), index)
            frames += silence
    with wave.open(str(args.output), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(bytes(frames))
    seconds = len(frames) / 2 / RATE
    print(f"Wrote {args.output} ({seconds:.0f} s, clinician voice {clinician!r}, patient voice {patient!r}).")


if __name__ == "__main__":
    main()
