"""Fakes and helpers for the API tests. Nothing here needs a microphone or Whisper."""

import asyncio
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from airsoap.audio import synth_tone, write_wav
from airsoap.config import Settings
from airsoap.main import create_app
from airsoap.recording import RecorderError
from airsoap.transcription import TranscriptResult, TranscriptSegment


class FakeProcess:
    def __init__(self, path: Path, duration_ms: int, fail_on_stop: bool) -> None:
        self.path = path
        self.duration_ms = duration_ms
        self.fail_on_stop = fail_on_stop
        self.aborted = False
        # Real recorders create the file as soon as they start.
        write_wav(path, b"")

    async def stop(self) -> None:
        if self.fail_on_stop:
            raise RecorderError("RECORDING_FAILED", "The microphone stopped responding.")
        write_wav(self.path, synth_tone(self.duration_ms))

    async def abort(self) -> None:
        self.aborted = True


class FakeRecorder:
    name = "fake-recorder"

    def __init__(self, *, duration_ms: int = 3000, fail_on_start: bool = False, fail_on_stop: bool = False):
        self.duration_ms = duration_ms
        self.fail_on_start = fail_on_start
        self.fail_on_stop = fail_on_stop
        self.processes: list[FakeProcess] = []

    def is_available(self) -> bool:
        return True

    async def start(self, path: Path) -> FakeProcess:
        if self.fail_on_start:
            raise RecorderError("RECORDING_FAILED", "The microphone could not be opened.")
        process = FakeProcess(path, self.duration_ms, self.fail_on_stop)
        self.processes.append(process)
        return process


def make_result(text: str = "Synthetic transcript.", duration_ms: int = 3000) -> TranscriptResult:
    return TranscriptResult(
        text=text,
        language="en",
        duration_ms=duration_ms,
        segments=(TranscriptSegment(start_ms=0, end_ms=duration_ms, text=text),),
    )


class FakeTranscriber:
    """Records calls and concurrency; optionally blocks until released or raises."""

    name = "fake-transcriber"

    def __init__(self, *, error: Exception | None = None, gated: bool = False, reachable: bool = True):
        self.error = error
        self.reachable = reachable
        self.calls: list[tuple[Path, int]] = []
        self.files_existed: list[bool] = []
        self.released = 0
        self.active = 0
        self.max_active = 0
        self._gated = gated
        self._release: asyncio.Event | None = None

    def let_finish(self, client: TestClient) -> None:
        """Let a gated transcription finish. Runs on the app's event loop thread."""
        client.portal.call(self._event().set)

    def _event(self) -> asyncio.Event:
        if self._release is None:
            self._release = asyncio.Event()
        return self._release

    async def transcribe(self, path: Path, duration_ms: int) -> TranscriptResult:
        self.calls.append((path, duration_ms))
        self.files_existed.append(path.exists())
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            if self._gated:
                await self._event().wait()
            if self.error is not None:
                raise self.error
            return make_result(f"Transcript {len(self.calls)}.", duration_ms)
        finally:
            self.active -= 1

    async def is_reachable(self) -> bool:
        return self.reachable

    def is_loaded(self) -> bool:
        return False

    async def release(self) -> None:
        self.released += 1

    async def close(self) -> None:
        return None


@contextmanager
def running_app(settings: Settings, **overrides: object) -> Iterator[TestClient]:
    with TestClient(create_app(settings, **overrides)) as client:
        yield client


def wait_for_status(client: TestClient, job_id: str, statuses: set[str], timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/transcriptions/{job_id}").json()
        if body.get("status") in statuses:
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"job stayed {body.get('status')!r}, expected one of {statuses}")
        time.sleep(0.01)


def record_once(client: TestClient) -> dict:
    """Start and stop a recording; returns the stop response body."""
    started = client.post("/api/recording/start")
    assert started.status_code == 200, started.text
    stopped = client.post("/api/recording/stop", json={"recordingId": started.json()["recordingId"]})
    assert stopped.status_code == 200, stopped.text
    return stopped.json()


def audio_files(settings: Settings) -> list[Path]:
    return sorted(settings.work_dir.glob("rec-*.wav"))


def make_binary(directory: Path, name: str, source: str) -> str:
    """Write an executable Python script that stands in for a system binary."""
    script = directory / name
    script.write_text(f"#!{sys.executable}\n{source}")
    script.chmod(0o755)
    return str(script)


# --- AI helpers (synthetic, fictional clinical text only) ----------------------------


class FakeAiClient:
    """Scripted stand-in for the Ollama client. Records calls, concurrency and unloads."""

    name = "fake-ai"

    def __init__(self, responses: list[object] | None = None, *, gated: bool = False, ready: bool = True):
        # Each entry is the JSON the "model" answers with, or an exception to raise. The last repeats.
        self.responses = responses or []
        self.ready = ready
        self.calls: list[tuple[str, int]] = []  # (task name, attempt)
        self.active = 0
        self.max_active = 0
        self.unloads = 0
        self._gated = gated
        self._event: asyncio.Event | None = None

    def let_finish(self, client: TestClient) -> None:
        client.portal.call(self._gate().set)

    def _gate(self) -> asyncio.Event:
        if self._event is None:
            self._event = asyncio.Event()
        return self._event

    async def complete(self, task, request, *, attempt: int):
        from airsoap.ai.base import AiCompletion

        self.calls.append((task.name, attempt))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            if self._gated:
                await self._gate().wait()
            if not self.responses:
                return AiCompletion(data=task.mock(request), prompt_tokens=10, completion_tokens=5)
            item = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
            if isinstance(item, Exception):
                raise item
            return AiCompletion(data=item, prompt_tokens=10, completion_tokens=5)  # type: ignore[arg-type]
        finally:
            self.active -= 1

    async def is_ready(self) -> bool:
        return self.ready

    async def unload(self) -> None:
        self.unloads += 1

    async def close(self) -> None:
        return None


def segments(*pairs: tuple[str, str]) -> list[dict]:
    """[("clinician", "text"), ("patient", "text")] -> API segments with stable IDs."""
    return [
        {"id": f"seg-{i}", "speaker": speaker, "text": text} for i, (speaker, text) in enumerate(pairs, 1)
    ]


VISIT = segments(
    ("clinician", "What brings you in today?"),
    ("patient", "I have had a sore throat for three days and it hurts to swallow."),
    ("clinician", "Any fever or cough?"),
    ("patient", "I felt feverish last night, around 101. No cough."),
    ("clinician", "Your temperature today is 38.3 and your tonsils are swollen."),
    ("clinician", "Do you have any drug allergies?"),
)
CONTEXT = {"patientAge": 35, "encounterType": "acute"}
NOTE = {
    "subjective": "Sore throat for three days. Fever last night, around 101 F. No cough.",
    "objective": "Temp 38.3 C. Tonsils swollen.",
    "assessment": "Probable pharyngitis.",
    "plan": "Fluids and rest.",
}


def soap_payload(segs: list[dict] | None = None) -> dict:
    return {"segments": segs or VISIT, "context": CONTEXT}


def review_payload(note: dict | None = None, segs: list[dict] | None = None) -> dict:
    return {"segments": segs or VISIT, "soap": note or NOTE, "context": CONTEXT}


SOAP_ANSWER = {
    "subjective": (
        "Patient reports a sore throat for three days with pain on swallowing. "
        "Felt feverish last night (about 101). Denies cough."
    ),
    "objective": "Temperature 38.3. Tonsils swollen.",
    "assessment": "Not documented.",
    "plan": "Not documented.",
}


def review_answer(**lists: list) -> dict:
    base = {
        "informationGaps": [],
        "followUpQuestions": [],
        "differentialConsiderations": [],
        "documentationInconsistencies": [],
    }
    return {**base, **lists}


def item(description: str, quote: str | None, *, source: str = "transcript", **extra) -> dict:
    return {
        "description": description,
        "rationale": "Because it may clarify the history.",
        "uncertainty": "medium",
        "evidence": [{"source": source, "quote": quote}] if quote else [],
        **extra,
    }


def wait_for_ai_job(client: TestClient, job_id: str, statuses: set[str], timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/ai/jobs/{job_id}").json()
        if body.get("status") in statuses:
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"AI job stayed {body.get('status')!r}, expected one of {statuses}")
        time.sleep(0.01)
