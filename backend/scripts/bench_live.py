#!/usr/bin/env python3
"""Phase 6, stage 0: can the Raspberry Pi transcribe live and suggest speaker roles?

Measures, on the real device, with a fictional consultation (scripts/make_bench_audio.py):

  A. whisper-server model load time
  B. latency of one request for audio windows of 3 to 20 s, with full and reduced audio context
  C. a real-time live simulation: the audio "arrives" at its true speed; the loop transcribes only
     the unconfirmed tail, confirms text two passes agree on, and records how far behind it falls
  D. the final whole-file pass, and accuracy of live and final text against the known script
  E. speaker-role suggestions with the 1.5B model: cold and warm time, accuracy, Unknown rate,
     and the cost of switching between the 1.5B and 3B models
  F. live simulation again, with speaker labelling running at the same time

Throughout, it samples available memory, CPU load, temperature and throttling. Results go to
~/airsoap-bench/bench-<time>.json. No transcript text is printed or saved unless --show-text.

Standard library only. Run it on the Pi while nobody is using AirSOAP:

    python3 scripts/bench_live.py --audio ~/bench-consultation.wav
    python3 scripts/bench_live.py --audio ~/bench-consultation.wav --quick     # about 5 minutes
"""

import argparse
import contextlib
import difflib
import io
import json
import math
import os
import re
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave
from array import array
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_dialogue import DIALOGUE, reference_text  # noqa: E402

RATE = 16000
ENV_FILE = Path.home() / ".config" / "airsoap" / "airsoap.env"
BENCH_PORT = 8179  # not the backend's 8178, so the two never collide
SPEAKER_MODEL = "qwen2.5:1.5b-instruct"
# Whisper's encoder covers 30 s in 1500 frames: 50 frames per second of audio.
FRAMES_PER_SECOND = 50

# Live loop defaults; the benchmark tries a few of them.
MAX_WINDOW_S = 20.0
COMMIT_MARGIN_MS = 1000
PAUSE_COMMIT_MS = 800
BEHIND_MS = 15000


# --- small utilities ---------------------------------------------------------------------------


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def words(text: str) -> list[str]:
    """Lower-case words without punctuation, with numbers kept as written."""
    return re.findall(r"[a-z0-9']+", text.lower())


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref, hyp = words(reference), words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        current = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (r != h))
        previous = current
    return previous[-1] / len(ref)


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, words(a), words(b)).ratio()


def summarize(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    p90 = ordered[min(len(ordered) - 1, math.ceil(0.9 * len(ordered)) - 1)]
    return {
        "n": len(values),
        "median": round(statistics.median(values), 3),
        "p90": round(p90, 3),
        "max": round(max(values), 3),
    }


# --- audio ---------------------------------------------------------------------------------------


class Audio:
    """16 kHz mono 16-bit samples, sliced by milliseconds."""

    def __init__(self, samples: array) -> None:
        self.samples = samples

    @classmethod
    def load(cls, path: Path) -> "Audio":
        with wave.open(str(path), "rb") as source:
            if source.getframerate() != RATE or source.getnchannels() != 1 or source.getsampwidth() != 2:
                sys.exit("The audio must be 16 kHz, mono, 16-bit WAV (make_bench_audio.py produces this).")
            samples = array("h")
            samples.frombytes(source.readframes(source.getnframes()))
        if sys.byteorder == "big":
            samples.byteswap()
        return cls(samples)

    @property
    def duration_ms(self) -> int:
        return len(self.samples) * 1000 // RATE

    def slice(self, start_ms: int, end_ms: int) -> array:
        return self.samples[start_ms * RATE // 1000 : end_ms * RATE // 1000]

    @staticmethod
    def to_wav(samples: array) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(RATE)
            data = array("h", samples)
            if sys.byteorder == "big":
                data.byteswap()
            out.writeframes(data.tobytes())
        return buffer.getvalue()


def rms(samples: array) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class SilenceDetector:
    """Loudness per 100 ms against an adaptive noise floor. Simple on purpose: no extra model."""

    def __init__(self, minimum: float = 200.0) -> None:
        self.minimum = minimum
        self.levels: list[float] = []

    def threshold(self) -> float:
        if len(self.levels) < 10:
            return self.minimum
        floor = sorted(self.levels)[len(self.levels) // 10]
        return max(self.minimum, 3.0 * floor)

    def blocks(self, samples: array) -> list[bool]:
        """True for each 100 ms block that holds speech."""
        step = RATE // 10
        result = []
        for i in range(0, len(samples), step):
            level = rms(samples[i : i + step])
            self.levels.append(level)
            self.levels = self.levels[-600:]
            result.append(level > self.threshold())
        return result

    def is_silent(self, samples: array) -> bool:
        return not any(self.blocks(samples))

    def silent_lead_ms(self, samples: array) -> int:
        """How long ``samples`` stays quiet from its start: the pause after a segment ends."""
        quiet = 0
        for speech in self.blocks(samples):
            if speech:
                break
            quiet += 100
        return quiet


# --- resources -----------------------------------------------------------------------------------


class ResourceSampler:
    """Memory, CPU, temperature every half second, from /proc and /sys (Linux only)."""

    def __init__(self) -> None:
        self.mem_available_mb: list[float] = []
        self.cpu_percent: list[float] = []
        self.temperature_c: list[float] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @staticmethod
    def _cpu_times() -> tuple[int, int] | None:
        try:
            fields = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        except OSError:
            return None
        values = [int(v) for v in fields]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return sum(values), idle

    def _run(self) -> None:
        last = self._cpu_times()
        while not self._stop.wait(0.5):
            with contextlib.suppress(OSError, ValueError):
                for line in Path("/proc/meminfo").read_text().splitlines():
                    if line.startswith("MemAvailable:"):
                        self.mem_available_mb.append(int(line.split()[1]) / 1024)
            with contextlib.suppress(OSError, ValueError):
                self.temperature_c.append(
                    int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
                )
            now = self._cpu_times()
            if last and now and now[0] > last[0]:
                total, idle = now[0] - last[0], now[1] - last[1]
                self.cpu_percent.append(100.0 * (total - idle) / total)
            last = now

    def __enter__(self) -> "ResourceSampler":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def report(self) -> dict[str, float | None]:
        return {
            "mem_available_min_mb": round(min(self.mem_available_mb)) if self.mem_available_mb else None,
            "cpu_percent_mean": round(statistics.mean(self.cpu_percent)) if self.cpu_percent else None,
            "temperature_max_c": round(max(self.temperature_c), 1) if self.temperature_c else None,
        }


def throttled() -> str | None:
    """The Pi's throttling flags (0x0 means never throttled since boot)."""
    if not shutil.which("vcgencmd"):
        return None
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5).stdout  # noqa: S607
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.strip().split("=")[-1] or None


# --- whisper-server ------------------------------------------------------------------------------


@dataclass
class Segment:
    start_ms: int
    end_ms: int
    text: str


def multipart(fields: dict[str, str], audio: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
        "Content-Type: audio/wav\r\n\r\n".encode()
        + audio
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


NON_SPEECH = re.compile(r"\[[^\]]*\]|\([^)]*\)")


def parse_segments(payload: dict) -> list[Segment]:
    segments = []
    for raw in payload.get("segments") or []:
        text = " ".join(NON_SPEECH.sub(" ", str(raw.get("text", ""))).split())
        if text:
            start = round(float(raw.get("start", 0)) * 1000)
            segments.append(Segment(start, max(start, round(float(raw.get("end", 0)) * 1000)), text))
    return segments


class WhisperServer:
    def __init__(self, binary: str, model: str, threads: int, port: int = BENCH_PORT) -> None:
        self.binary, self.model, self.threads, self.port = binary, model, threads, port
        self.process: subprocess.Popen | None = None
        self.url = f"http://127.0.0.1:{port}"

    def start(self) -> float:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                sys.exit(f"Port {self.port} is already in use; stop whatever is listening there.")
        began = time.monotonic()
        self.process = subprocess.Popen(  # noqa: S603 - fixed argument list
            [
                self.binary,
                "-m",
                self.model,
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "-t",
                str(self.threads),
                "-l",
                "en",
            ],  # fmt: skip
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        while time.monotonic() - began < 120:
            if self.process.poll() is not None:
                sys.exit("whisper-server exited during startup; check the binary and model paths.")
            with (
                contextlib.suppress(urllib.error.URLError, OSError),
                urllib.request.urlopen(self.url + "/", timeout=1) as response,  # noqa: S310 - loopback
            ):
                if response.status == 200:
                    return time.monotonic() - began
            time.sleep(0.2)
        self.stop()
        sys.exit("whisper-server did not finish loading within 120 s.")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None

    def transcribe(self, samples: array, audio_ctx: int = 0) -> tuple[float, list[Segment]]:
        fields = {"response_format": "verbose_json", "temperature": "0.0", "language": "en"}
        if audio_ctx:
            fields["audio_ctx"] = str(audio_ctx)
        body, content_type = multipart(fields, Audio.to_wav(samples))
        request = urllib.request.Request(  # noqa: S310 - loopback
            self.url + "/inference", data=body, method="POST", headers={"Content-Type": content_type}
        )
        began = time.monotonic()
        with urllib.request.urlopen(request, timeout=600) as response:  # noqa: S310
            payload = json.loads(response.read())
        return time.monotonic() - began, parse_segments(payload)


def matched_audio_ctx(seconds: float) -> int:
    """Encoder frames covering the window plus a small margin; 0 (all) once it reaches 30 s."""
    frames = math.ceil(seconds * FRAMES_PER_SECOND) + 64
    return 0 if frames >= 1500 else frames


# --- live loop -----------------------------------------------------------------------------------


def norm(text: str) -> str:
    return " ".join(words(text))


@dataclass
class LiveState:
    """The confirmed-text bookkeeping the backend's live worker will use (stage 1)."""

    committed: list[Segment] = field(default_factory=list)
    committed_until_ms: int = 0
    previous: list[Segment] = field(default_factory=list)
    low_agreement: int = 0

    def update(
        self, hypothesis: list[Segment], window_end_ms: int, window_full: bool, quiet_after
    ) -> list[Segment]:
        """Confirm the leading segments that are stable; return the newly confirmed ones.

        A segment is confirmed when it matches the previous pass (same words, start within
        0.6 s) and ends before the margin, or when a pause follows it. If the window is full
        and nothing is stable, the first segment is confirmed anyway (counted as low agreement)
        so the window can never grow without limit.
        """
        confirmed: list[Segment] = []
        for i, segment in enumerate(hypothesis):
            agreed = (
                i < len(self.previous)
                and norm(segment.text) == norm(self.previous[i].text)
                and abs(segment.start_ms - self.previous[i].start_ms) <= 600
                and segment.end_ms <= window_end_ms - COMMIT_MARGIN_MS
            )
            paused = (
                segment.end_ms <= window_end_ms - PAUSE_COMMIT_MS
                and quiet_after(segment.end_ms) >= PAUSE_COMMIT_MS
            )
            if not (agreed or paused):
                break
            confirmed.append(segment)
        if not confirmed and window_full and hypothesis:
            confirmed = [hypothesis[0]]
            self.low_agreement += 1
        if confirmed:
            self.committed.extend(confirmed)
            self.committed_until_ms = confirmed[-1].end_ms
        self.previous = hypothesis[len(confirmed) :]
        return confirmed

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.committed)


@dataclass
class LiveResult:
    step_s: float
    audio_ctx: str
    inference_s: dict | None = None
    provisional_delay_s: dict | None = None
    final_delay_s: dict | None = None
    max_behind_s: float = 0.0
    behind_events: int = 0
    passes: int = 0
    skipped_silent: int = 0
    low_agreement: int = 0
    wer_live: float | None = None
    resources: dict = field(default_factory=dict)
    labelling: dict | None = None


def live_simulation(
    server: WhisperServer,
    audio: Audio,
    *,
    step_s: float,
    reduced_ctx: bool,
    labeller: "Labeller | None" = None,
    show_text: bool = False,
) -> tuple[LiveResult, LiveState]:
    """Replays the audio at its real speed; nothing is transcribed before it would have been spoken."""
    state = LiveState()
    silence = SilenceDetector()
    result = LiveResult(step_s=step_s, audio_ctx="matched" if reduced_ctx else "full")
    inference, provisional_delay, final_delay = [], [], []
    began = time.monotonic()
    total = audio.duration_ms

    def audio_now() -> int:
        return min(total, int((time.monotonic() - began) * 1000))

    with ResourceSampler() as sampler:
        next_pass = began + step_s
        while True:
            time.sleep(max(0.0, next_pass - time.monotonic()))
            available = audio_now()
            start = state.committed_until_ms
            end = min(available, start + int(MAX_WINDOW_S * 1000))
            behind = available - start
            result.max_behind_s = max(result.max_behind_s, behind / 1000)
            if behind > BEHIND_MS:
                result.behind_events += 1

            window = audio.slice(start, end)
            if end - start >= 1000 and silence.is_silent(window) and not state.previous:
                state.committed_until_ms = max(start, end - 300)  # skip silence; keep a little lead-in
                result.skipped_silent += 1
            elif end - start >= 1000:
                seconds = (end - start) / 1000
                took, segments = server.transcribe(window, matched_audio_ctx(seconds) if reduced_ctx else 0)
                inference.append(took)
                result.passes += 1
                hypothesis = [
                    Segment(s.start_ms + start, min(s.end_ms + start, end), s.text) for s in segments
                ]
                done_ms = audio_now()
                provisional_delay.append((done_ms - end) / 1000)
                window_full = end - start >= int(MAX_WINDOW_S * 1000) - 50

                def quiet_after(at_ms: int, _end: int = end) -> int:
                    return silence.silent_lead_ms(audio.slice(at_ms, _end)) if _end - at_ms >= 100 else 0

                for segment in state.update(hypothesis, end, window_full, quiet_after):
                    final_delay.append((done_ms - segment.end_ms) / 1000)
                    if show_text:
                        print(f"    [{segment.start_ms / 1000:6.1f}s] {segment.text}")
                if labeller:
                    labeller.offer(state.committed)

            if available >= total and (not state.previous or end >= total):
                break
            next_pass = time.monotonic() + (0 if behind > step_s * 1000 * 2 else step_s)

        if state.previous:  # the recording ended; the real system's final pass covers this
            state.committed.extend(state.previous)
        if labeller:
            labeller.finish()
            result.labelling = labeller.report()

    result.inference_s = summarize(inference)
    result.provisional_delay_s = summarize(provisional_delay)
    result.final_delay_s = summarize(final_delay)
    result.low_agreement = state.low_agreement
    result.wer_live = round(word_error_rate(reference_text(), state.text), 3)
    result.resources = sampler.report()
    return result, state


# --- speaker roles with Qwen ---------------------------------------------------------------------

SPEAKER_SYSTEM = (
    "You label who is speaking in a transcript of a medical visit between one clinician and one patient. "
    "You only see the words, never the voices. For each turn answer clinician, patient or unknown. "
    "Clues: the clinician asks about symptoms and history, examines, states findings and test results, "
    "names a diagnosis, prescribes and gives instructions. The patient describes symptoms, answers the "
    "clinician's questions and asks about their own care. Use unknown when the words alone do not show "
    "who spoke, for example 'Okay.', 'Yes.' or 'Thank you.', or whenever you are not sure. Do not guess "
    "to fill every turn. Give exactly one entry per turn, in the same order, using the given ids. "
    "Never repeat, change or add words."
)

SPEAKER_SCHEMA = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "speaker": {"type": "string", "enum": ["clinician", "patient", "unknown"]},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["id", "speaker", "confidence"],
            },
        }
    },
    "required": ["labels"],
}


def validate_labels(answer: object, ids: list[str]) -> list[str] | None:
    """The labels in order, low confidence turned into unknown, or None if the answer is unusable."""
    if not isinstance(answer, dict) or not isinstance(answer.get("labels"), list):
        return None
    labels = answer["labels"]
    if [entry.get("id") if isinstance(entry, dict) else None for entry in labels] != ids:
        return None
    result = []
    for entry in labels:
        speaker, confidence = entry.get("speaker"), entry.get("confidence")
        if speaker not in {"clinician", "patient", "unknown"} or confidence not in {"low", "medium", "high"}:
            return None
        result.append("unknown" if confidence == "low" else speaker)
    return result


class Ollama:
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")

    def _post(self, path: str, payload: dict, timeout: float) -> dict:
        request = urllib.request.Request(  # noqa: S310 - loopback
            self.url + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read() or b"{}")

    def installed(self) -> set[str]:
        with urllib.request.urlopen(self.url + "/api/tags", timeout=5) as response:  # noqa: S310
            return {m.get("name") for m in json.loads(response.read()).get("models", [])}

    def unload(self, model: str) -> None:
        with contextlib.suppress(urllib.error.URLError, OSError, ValueError):
            self._post("/api/chat", {"model": model, "messages": [], "keep_alive": 0}, 30)

    def label(
        self, model: str, turns: list[tuple[str, str]], earlier: list[tuple[str, str, str]]
    ) -> tuple[float, list[str] | None]:
        content = json.dumps(
            {
                "earlier_turns": [{"id": i, "text": t, "speaker": s} for i, t, s in earlier],
                "turns": [{"id": i, "text": t} for i, t in turns],
            }
        )
        began = time.monotonic()
        response = self._post(
            "/api/chat",
            {
                "model": model,
                "stream": False,
                "format": SPEAKER_SCHEMA,
                "keep_alive": "5m",
                "options": {"temperature": 0, "num_ctx": 4096},
                "messages": [
                    {"role": "system", "content": SPEAKER_SYSTEM},
                    {"role": "user", "content": content},
                ],
            },
            600,
        )
        took = time.monotonic() - began
        try:
            answer = json.loads(response.get("message", {}).get("content", ""))
        except ValueError:
            return took, None
        return took, validate_labels(answer, [i for i, _ in turns])

    def tiny(self, model: str) -> float:
        began = time.monotonic()
        self._post(
            "/api/chat",
            {
                "model": model,
                "stream": False,
                "keep_alive": "5m",
                "options": {"num_predict": 1},
                "messages": [{"role": "user", "content": "Reply OK."}],
            },  # fmt: skip
            600,
        )
        return time.monotonic() - began


def score_labels(predicted: list[str], truth: list[str]) -> dict[str, float]:
    decided = [(p, t) for p, t in zip(predicted, truth, strict=True) if p != "unknown"]
    correct = sum(p == t for p, t in decided)
    return {
        "turns": len(truth),
        "labelled": len(decided),
        "unknown": len(truth) - len(decided),
        "accuracy_of_labelled": round(correct / len(decided), 3) if decided else 0.0,
        "wrong": len(decided) - correct,
    }


class Labeller:
    """Labels confirmed segments in batches, one request at a time, beside the live loop."""

    def __init__(self, ollama: Ollama, model: str, batch: int = 6) -> None:
        self.ollama, self.model, self.batch = ollama, model, batch
        self.done = 0
        self.times: list[float] = []
        self.invalid = 0
        self._busy = threading.Lock()
        self._threads: list[threading.Thread] = []

    def offer(self, committed: list[Segment]) -> None:
        if len(committed) - self.done < self.batch or self._busy.locked():
            return
        turns = [(f"s{i}", s.text) for i, s in enumerate(committed[self.done :], start=self.done)]
        self.done = len(committed)
        thread = threading.Thread(target=self._run, args=(turns,), daemon=True)
        self._threads.append(thread)
        thread.start()

    def _run(self, turns: list[tuple[str, str]]) -> None:
        with self._busy:
            took, labels = self.ollama.label(self.model, turns, [])
            self.times.append(took)
            self.invalid += labels is None

    def finish(self) -> None:
        for thread in self._threads:
            thread.join(timeout=600)

    def report(self) -> dict:
        return {"batches": len(self.times), "invalid": self.invalid, "seconds": summarize(self.times)}


# --- the benchmark -------------------------------------------------------------------------------


def section(title: str) -> None:
    print(f"\n== {title}")


def window_benchmark(server: WhisperServer, audio: Audio, sizes: list[int], reps: int) -> list[dict]:
    rows = []
    middle = audio.duration_ms // 2
    for seconds in sizes:
        start = max(0, middle - seconds * 500)
        samples = audio.slice(start, start + seconds * 1000)
        row: dict = {"window_s": seconds}
        texts = {}
        for mode in ("full", "matched"):
            ctx = matched_audio_ctx(seconds) if mode == "matched" else 0
            times = []
            for _ in range(reps):
                took, segments = server.transcribe(samples, ctx)
                times.append(took)
            texts[mode] = " ".join(s.text for s in segments)
            row[mode] = summarize(times)
        row["matched_vs_full_similarity"] = round(similarity(texts["full"], texts["matched"]), 3)
        rows.append(row)
        print(
            f"  {seconds:>3} s window: full {row['full']['median']:.2f} s, "
            f"reduced context {row['matched']['median']:.2f} s, same words {row['matched_vs_full_similarity']:.0%}"
        )
    return rows


def recommend(results: dict) -> list[str]:
    advice = []
    runs = results.get("live", [])
    good = [
        r
        for r in runs
        if r["inference_s"] and r["inference_s"]["p90"] < 0.85 * r["step_s"] and r["behind_events"] == 0
    ]
    if good:
        best = min(good, key=lambda r: (r["final_delay_s"] or {}).get("median", 99))
        advice.append(
            f"Live transcription keeps up: use a {best['step_s']:g} s step with {best['audio_ctx']} audio context "
            f"(text confirmed a median {best['final_delay_s']['median']:.1f} s after speech)."
        )
    elif runs:
        advice.append(
            "Live transcription fell behind in every setting tried. Use the whole-file workflow, or a live view "
            "that only updates during pauses."
        )
    concurrent = results.get("concurrent")
    if concurrent and good:
        base = next((r for r in good if r["step_s"] == concurrent["step_s"]), good[0])
        slower = (concurrent["final_delay_s"] or {}).get("median", 99) - (base["final_delay_s"] or {}).get(
            "median", 0
        )
        memory = concurrent["resources"].get("mem_available_min_mb")
        if concurrent["behind_events"] == 0 and slower < 2.0 and (memory is None or memory > 300):
            advice.append("Speaker labelling can run during recording without making live text fall behind.")
        else:
            advice.append(
                "Run speaker labelling after the recording stops: during recording it slowed live text "
                f"by {slower:.1f} s or left too little memory ({memory} MB free at the lowest)."
            )
    labels = results.get("speaker_labels")
    if labels and labels.get("score"):
        score = labels["score"]
        advice.append(
            f"Speaker suggestions: {score['labelled']} of {score['turns']} turns labelled, "
            f"{score['accuracy_of_labelled']:.0%} of those correct, {score['unknown']} left Unknown."
        )
    return advice


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--audio", type=Path, required=True, help="16 kHz mono WAV of the fictional consultation"
    )
    parser.add_argument("--env-file", type=Path, default=ENV_FILE)
    parser.add_argument("--speaker-model", default=SPEAKER_MODEL)
    parser.add_argument("--quick", action="store_true", help="fewer window sizes and one live setting")
    parser.add_argument("--skip-qwen", action="store_true", help="transcription only")
    parser.add_argument(
        "--show-text", action="store_true", help="print the (fictional) transcript as it is confirmed"
    )
    parser.add_argument("--out", type=Path, default=Path.home() / "airsoap-bench")
    args = parser.parse_args()

    env = read_env(args.env_file)
    binary = env.get("AIRSOAP_WHISPER_SERVER_PATH")
    model = env.get("AIRSOAP_WHISPER_MODEL_PATH")
    if not binary or not model:
        sys.exit(f"AIRSOAP_WHISPER_SERVER_PATH and AIRSOAP_WHISPER_MODEL_PATH must be set in {args.env_file}")
    threads = int(env.get("AIRSOAP_WHISPER_THREADS", "4"))
    ollama = Ollama(env.get("AIRSOAP_OLLAMA_URL", "http://127.0.0.1:11434"))
    soap_model = env.get("AIRSOAP_OLLAMA_MODEL", "qwen2.5:3b-instruct")

    audio = Audio.load(args.audio)
    if subprocess.run(["pgrep", "-x", "whisper-server"], capture_output=True).returncode == 0:  # noqa: S607
        print(
            "Note: another whisper-server is running (the AirSOAP backend?). Results will be slower; stop it if you can."
        )

    results: dict = {
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "audio_seconds": round(audio.duration_ms / 1000, 1),
        "threads": threads,
        "throttled_before": throttled(),
    }
    print(f"AirSOAP live-transcription benchmark: {results['audio_seconds']} s of audio, {threads} threads")

    if not args.skip_qwen:
        for name in (soap_model, args.speaker_model):
            ollama.unload(name)  # whisper-server gets the memory, as the backend's gate would arrange

    server = WhisperServer(binary, model, threads)
    try:
        section("A. Loading the Whisper model")
        results["whisper_load_s"] = round(server.start(), 2)
        print(f"  ready in {results['whisper_load_s']:.1f} s")

        section("B. One request per audio window (median of repeats)")
        sizes = [5, 10, 20] if args.quick else [3, 5, 8, 10, 15, 20]
        results["windows"] = window_benchmark(server, audio, sizes, reps=2 if args.quick else 3)

        section("C. Live simulation at real speed")
        reduced_helps = any(
            row["matched"]["median"] < 0.85 * row["full"]["median"]
            and row["matched_vs_full_similarity"] > 0.9
            for row in results["windows"]
        )
        settings = (
            [(3.0, reduced_helps)]
            if args.quick
            else [(2.0, reduced_helps), (3.0, reduced_helps), (3.0, False)]
        )
        results["live"] = []
        for step, reduced in dict.fromkeys(settings):
            print(
                f"  step {step:g} s, {'reduced' if reduced else 'full'} audio context ({results['audio_seconds']:.0f} s)..."
            )
            run, state = live_simulation(
                server, audio, step_s=step, reduced_ctx=reduced, show_text=args.show_text
            )
            results["live"].append(asdict(run))
            live_text = state.text
            print(
                f"    request {run.inference_s['median']:.2f} s (p90 {run.inference_s['p90']:.2f}), "
                f"confirmed {run.final_delay_s['median'] if run.final_delay_s else float('nan'):.1f} s after speech, "
                f"most behind {run.max_behind_s:.1f} s, word errors {run.wer_live:.0%}, "
                f"CPU {run.resources.get('cpu_percent_mean')}%, {run.resources.get('temperature_max_c')} °C"
            )

        section("D. Final whole-file pass")
        took, segments = server.transcribe(audio.samples)
        final_text = " ".join(s.text for s in segments)
        results["final"] = {
            "seconds": round(took, 2),
            "realtime_factor": round(took / (audio.duration_ms / 1000), 2),
            "wer": round(word_error_rate(reference_text(), final_text), 3),
            "live_vs_final_similarity": round(similarity(live_text, final_text), 3),
        }
        print(
            f"  {took:.1f} s for the whole recording, word errors {results['final']['wer']:.0%}, "
            f"live text matched it {results['final']['live_vs_final_similarity']:.0%}"
        )
    finally:
        server.stop()

    if not args.skip_qwen:
        installed = ollama.installed()
        if args.speaker_model not in installed:
            print(
                f"\n{args.speaker_model} is not installed (ollama list shows: {', '.join(sorted(installed))}). Skipping E and F."
            )
        else:
            section(f"E. Speaker roles with {args.speaker_model}")
            truth = [speaker for speaker, _ in DIALOGUE]
            turns = [(f"t{i}", text) for i, (_, text) in enumerate(DIALOGUE)]
            with ResourceSampler() as sampler:
                cold, first = ollama.label(args.speaker_model, turns[:15], [])
                warm, second = ollama.label(
                    args.speaker_model,
                    turns[15:],
                    [(i, t, s) for (i, t), s in zip(turns[:15], first or ["unknown"] * 15, strict=True)],
                )
            predicted = (first or ["unknown"] * 15) + (second or ["unknown"] * (len(turns) - 15))
            results["speaker_labels"] = {
                "cold_s": round(cold, 2),
                "warm_s": round(warm, 2),
                "valid_answers": int(first is not None) + int(second is not None),
                "score": score_labels(predicted, truth),
                "resources": sampler.report(),
            }
            print(f"  first batch (includes loading) {cold:.1f} s, second batch {warm:.1f} s")
            ollama.unload(args.speaker_model)
            to_soap = ollama.tiny(soap_model)
            back = ollama.tiny(args.speaker_model)
            ollama.unload(args.speaker_model)
            results["model_switch_s"] = {"load_3b": round(to_soap, 2), "switch_back_to_1_5b": round(back, 2)}
            print(f"  loading the 3B model {to_soap:.1f} s, switching back to 1.5B {back:.1f} s")

            section("F. Live simulation with speaker labelling at the same time")
            server = WhisperServer(binary, model, threads)
            try:
                server.start()
                best = min(results["live"], key=lambda r: (r["inference_s"] or {}).get("p90", 99))
                run, _state = live_simulation(
                    server,
                    audio,
                    step_s=best["step_s"],
                    reduced_ctx=best["audio_ctx"] == "matched",
                    labeller=Labeller(ollama, args.speaker_model),
                )
                results["concurrent"] = asdict(run)
                print(
                    f"    request {run.inference_s['median']:.2f} s, confirmed "
                    f"{run.final_delay_s['median'] if run.final_delay_s else float('nan'):.1f} s after speech, "
                    f"most behind {run.max_behind_s:.1f} s, lowest free memory "
                    f"{run.resources.get('mem_available_min_mb')} MB, labelling {run.labelling}"
                )
            finally:
                server.stop()
                ollama.unload(args.speaker_model)

    results["throttled_after"] = throttled()
    results["recommendations"] = recommend(results)
    section("Recommendations")
    for line in results["recommendations"]:
        print(f"  - {line}")
    if results["throttled_after"] not in (None, "0x0"):
        print(
            f"  - The Pi throttled (vcgencmd {results['throttled_after']}): improve cooling before trusting these numbers."
        )

    args.out.mkdir(mode=0o700, parents=True, exist_ok=True)
    out = args.out / f"bench-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(results, indent=2))
    os.chmod(out, 0o600)
    print(f"\nFull results: {out}  (paste this file's contents back for the next stage)")


if __name__ == "__main__":
    main()
