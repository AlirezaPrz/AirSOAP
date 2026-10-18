"""Live transcription: cutting at pauses, the live session, the event stream and the fallback.

No microphone, Whisper or Ollama: synthetic audio and fake transcribers only.
"""

import asyncio
import json
import logging
import struct
import threading
import time
import wave
from array import array
from pathlib import Path

import pytest
from helpers import CLINICIAN, PASSWORD, FakeTranscriber, add_user, running_app, sign_in

from airsoap.audio import CANONICAL_HEADER_SIZE, synth_tone
from airsoap.live import GrowingWav, LiveSession, LiveStatus, MockLiveTranscriber
from airsoap.live.segmenter import BlockPlanner, SilenceDetector
from airsoap.recording import mock as mock_recorder
from airsoap.transcription import TranscriptionError, TranscriptSegment

RATE = 16_000


def speech(ms: int) -> array:
    samples = array("h")
    samples.frombytes(synth_tone(ms))
    return samples


def silence(ms: int) -> array:
    return array("h", [0] * (RATE * ms // 1000))


def planner(**overrides) -> BlockPlanner:
    return BlockPlanner(**{"pause_ms": 600, "max_block_ms": 15000, **overrides})


# --- where to cut ---------------------------------------------------------------------------


def test_a_stretch_of_speech_is_cut_after_its_pause() -> None:
    plan = planner()
    plan.feed(speech(1500))
    assert plan.next_block() is None  # still talking
    plan.feed(silence(700))
    block = plan.next_block()
    assert block is not None and not block.forced
    assert block.start_ms == 0
    assert 1500 <= block.end_ms <= 1900  # the speech plus a short tail, not the whole pause
    assert plan.next_block() is None  # nothing is transcribed twice


def test_silence_is_skipped_without_transcribing() -> None:
    plan = planner()
    plan.feed(silence(3000))
    assert plan.next_block() is None
    assert plan.cut_ms > 2000  # moved past the silence
    plan.feed(speech(1200) + silence(700))
    block = plan.next_block()
    assert block is not None and block.start_ms >= 2500  # starts just before the speech


def test_a_monologue_without_pauses_is_cut_at_the_limit() -> None:
    plan = planner(max_block_ms=3000)
    plan.feed(speech(2000) + silence(100) + speech(1500))
    block = plan.next_block()
    assert block is not None and block.forced
    assert block.end_ms == 2000  # at the quietest moment, not mid-word


def test_whatever_is_left_at_stop_is_transcribed_once() -> None:
    plan = planner()
    plan.feed(speech(900))
    tail = plan.remainder(900)
    assert tail is not None and (tail.start_ms, tail.end_ms) == (0, 900)
    assert plan.remainder(900) is None
    quiet = planner()
    quiet.feed(silence(900))
    assert quiet.remainder(900) is None


def test_speech_from_the_first_second_is_not_taken_for_background() -> None:
    detector = SilenceDetector()
    levels = [1000.0] * 30
    for level in levels:
        detector.observe(level)
    assert detector.is_speech(1000.0)
    assert not detector.is_speech(0.0)


# --- reading the growing file ---------------------------------------------------------------


def write_header(path: Path) -> None:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(RATE)
        writer.writeframes(b"")


def test_the_growing_wav_is_read_while_it_is_written(tmp_path: Path) -> None:
    path = tmp_path / "rec.wav"
    write_header(path)
    source = GrowingWav(path)
    assert source.available_ms() == 0
    with path.open("ab") as handle:
        handle.write(speech(500).tobytes())
    assert source.available_ms() == 500
    assert len(source.read(100, 300)) == RATE * 200 // 1000
    assert len(source.read(400, 900)) == RATE * 100 // 1000  # only what exists so far
    assert path.stat().st_size == CANONICAL_HEADER_SIZE + 500 * 32


# --- the live session -----------------------------------------------------------------------


class MemorySource:
    """Audio that 'arrives' when the test appends it."""

    def __init__(self) -> None:
        self.samples = array("h")

    def append(self, samples: array) -> None:
        self.samples.extend(samples)

    def available_ms(self) -> int:
        return len(self.samples) * 1000 // RATE

    def read(self, start_ms: int, end_ms: int) -> array:
        return self.samples[start_ms * RATE // 1000 : end_ms * RATE // 1000]


class FakeLive:
    name = "fake-live"

    def __init__(self, *, fail_on: int | None = None, delay: float = 0.0) -> None:
        self.blocks: list[tuple[int, int]] = []
        self.started = 0
        self.stopped = 0
        self.fail_on = fail_on
        self.delay = delay

    def available(self) -> bool:
        return True

    async def start(self) -> None:
        self.started += 1

    async def transcribe_block(self, samples: array, start_ms: int) -> list[TranscriptSegment]:
        self.blocks.append((start_ms, start_ms + len(samples) * 1000 // RATE))
        if self.fail_on is not None and len(self.blocks) == self.fail_on:
            raise TranscriptionError(
                "TRANSCRIBER_UNAVAILABLE", "The local transcription service is not running."
            )
        await asyncio.sleep(self.delay)
        end = start_ms + len(samples) * 1000 // RATE
        return [TranscriptSegment(start_ms, end, f"Synthetic block {len(self.blocks)}.")]

    async def stop(self) -> None:
        self.stopped += 1


def session(source, live, **overrides) -> LiveSession:
    return LiveSession(
        **{
            "recording_id": "a" * 32,
            "source": source,
            "transcriber": live,
            "pause_ms": 600,
            "max_block_ms": 15000,
            "behind_ms": 15000,
            **overrides,
        }
    )


async def wait_until(condition, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


@pytest.mark.anyio
async def test_each_stretch_appears_once_and_in_order() -> None:
    source, live = MemorySource(), FakeLive()
    s = session(source, live)
    s.start()
    source.append(speech(1500) + silence(700))
    await wait_until(lambda: len(s.snapshot().segments) == 1)
    source.append(speech(1600) + silence(700))
    await wait_until(lambda: len(s.snapshot().segments) == 2)
    source.append(speech(800))  # still talking when the clinician presses Stop
    result = await s.finish(source.available_ms())
    assert result is not None
    assert [seg.text for seg in result.segments] == [
        "Synthetic block 1.",
        "Synthetic block 2.",
        "Synthetic block 3.",
    ]
    starts = [seg.start_ms for seg in result.segments]
    assert starts == sorted(starts)
    assert [seg.id for seg in s.snapshot().segments] == ["seg-1", "seg-2", "seg-3"]
    assert s.status is LiveStatus.DONE
    assert live.started == 1 and live.stopped >= 1


@pytest.mark.anyio
async def test_the_model_is_held_for_the_whole_recording() -> None:
    held = []

    class Hold:
        async def __aenter__(self):
            held.append("in")

        async def __aexit__(self, *exc):
            held.append("out")

    source, live = MemorySource(), FakeLive()
    s = session(source, live, hold_model=Hold)
    s.start()
    await wait_until(lambda: held == ["in"])
    source.append(speech(1000))
    await s.finish(source.available_ms())
    assert held == ["in", "out"]  # released after the last stretch, so SOAP can load Qwen


@pytest.mark.anyio
async def test_a_slow_transcriber_is_reported_as_behind() -> None:
    source, live = MemorySource(), FakeLive(delay=0.5)
    s = session(source, live, behind_ms=1000)
    s.start()
    source.append(speech(1500) + silence(700))
    await wait_until(lambda: len(live.blocks) == 1)
    source.append(speech(2500) + silence(700))  # arrives while the first block is still working
    await wait_until(lambda: s.status is LiveStatus.BEHIND, timeout=4)
    await s.abort()


@pytest.mark.anyio
async def test_a_failure_stops_live_transcription_and_asks_for_the_fallback() -> None:
    source, live = MemorySource(), FakeLive(fail_on=1)
    s = session(source, live)
    s.start()
    source.append(speech(1500) + silence(700))
    await wait_until(lambda: s.status is LiveStatus.FAILED)
    assert await s.finish(source.available_ms()) is None
    assert live.stopped >= 1


@pytest.mark.anyio
async def test_abort_keeps_nothing() -> None:
    source, live = MemorySource(), FakeLive()
    s = session(source, live)
    s.start()
    source.append(speech(1500) + silence(700))
    await wait_until(lambda: len(s.snapshot().segments) == 1)
    await s.abort()
    assert s.snapshot().segments == ()
    assert s.status is LiveStatus.DONE


@pytest.mark.anyio
async def test_listeners_wake_on_each_change() -> None:
    source, live = MemorySource(), FakeLive()
    s = session(source, live)
    first = s.snapshot().revision
    s.start()
    snapshot = await s.wait_for_change(first, 2.0)
    assert snapshot.revision > first
    await s.abort()


# --- through the API ------------------------------------------------------------------------


@pytest.fixture
def fast_mock_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Mac's mock recorder, with short 'speech' and 'pauses' so tests run quickly."""
    monkeypatch.setattr(mock_recorder, "SPEECH_MS", 1000)
    monkeypatch.setattr(mock_recorder, "PAUSE_MS", 800)


def live_settings(settings):
    return settings.model_copy(update={"live": "auto"})


def read_stream(client, recording_id: str) -> list[dict]:
    """Every event of the live stream, once it ends (TestClient returns only complete responses)."""
    with client.stream("GET", f"/api/recording/{recording_id}/live") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        return [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]


def live_of(client, recording_id: str):
    return client.app.state.services.sessions.live_for(recording_id, owner_id=None)


def wait_for(condition, timeout: float = 6.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.05)


def stream_then(client, recording_id: str, end):
    """Follow the stream in a thread while ``end()`` (stop or cancel) runs here: (events, end's result)."""
    events: list[dict] = []
    reader = threading.Thread(target=lambda: events.extend(read_stream(client, recording_id)))
    reader.start()
    time.sleep(0.3)
    result = end()
    reader.join(timeout=10)
    assert not reader.is_alive(), "the stream did not end"
    return events, result


def wait_job(client, job_id: str, timeout: float = 6.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/transcriptions/{job_id}").json()
        if body["status"] in {"completed", "error", "cancelled"}:
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"job stayed {body['status']}")
        time.sleep(0.05)


def test_the_transcript_arrives_while_recording_and_is_the_final_transcript(
    settings, fast_mock_audio
) -> None:
    transcriber = FakeTranscriber()
    with running_app(
        live_settings(settings),
        transcriber=transcriber,
        live_transcriber_override=MockLiveTranscriber(delay_seconds=0),
    ) as client:
        started = client.post("/api/recording/start").json()
        assert started["live"] is True
        recording_id = started["recordingId"]
        wait_for(lambda: len(live_of(client, recording_id).snapshot().segments) >= 1)
        events, stopped = stream_then(
            client,
            recording_id,
            lambda: client.post("/api/recording/stop", json={"recordingId": recording_id}).json(),
        )
        assert events[0]["segments"][0]["text"] == "Good morning. What brings you in today?"
        assert events[0]["recordingId"] == recording_id
        assert events[-1]["status"] in {"finishing", "done"}  # the stream ends when recording stops
        revisions = [e["revision"] for e in events]
        assert revisions == sorted(revisions)

        job = wait_job(client, stopped["jobId"])
        assert job["status"] == "completed"
        texts = [s["text"] for s in job["transcript"]["segments"]]
        assert texts[0] == "Good morning. What brings you in today?"
        assert transcriber.calls == []  # no second, whole-file pass
        assert list(settings.work_dir.glob("rec-*.wav")) == []  # audio deleted as before


def test_a_live_failure_falls_back_to_whole_file_transcription(settings, fast_mock_audio) -> None:
    transcriber = FakeTranscriber()
    with running_app(
        live_settings(settings), transcriber=transcriber, live_transcriber_override=FakeLive(fail_on=1)
    ) as client:
        started = client.post("/api/recording/start").json()
        events = read_stream(client, started["recordingId"])  # ends by itself when live fails
        assert events[-1]["status"] == "failed"  # said plainly, never shown as working
        stopped = client.post("/api/recording/stop", json={"recordingId": started["recordingId"]}).json()
        job = wait_job(client, stopped["jobId"])
        assert job["status"] == "completed"
        assert len(transcriber.calls) == 1  # the whole file was transcribed instead


def test_another_clinician_cannot_follow_the_live_transcript(settings, fast_mock_audio) -> None:
    with running_app(live_settings(settings), live_transcriber_override=FakeLive()) as client:
        recording_id = client.post("/api/recording/start").json()["recordingId"]
        mine = client.cookies["airsoap_session"]
        client.cookies.clear()
        add_user(client, "second@example.test")
        sign_in(client, "second@example.test")
        response = client.get(f"/api/recording/{recording_id}/live")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "NO_ACTIVE_RECORDING"
        client.cookies.clear()
        client.cookies.set("airsoap_session", mine, path="/api")
        client.post("/api/recording/cancel", json={"recordingId": recording_id})


def test_cancelling_stops_live_transcription_and_deletes_the_audio(settings, fast_mock_audio) -> None:
    live = FakeLive()
    with running_app(live_settings(settings), live_transcriber_override=live) as client:
        recording_id = client.post("/api/recording/start").json()["recordingId"]
        wait_for(lambda: live_of(client, recording_id).status.value == "listening")
        events, _ = stream_then(
            client,
            recording_id,
            lambda: client.post("/api/recording/cancel", json={"recordingId": recording_id}),
        )
        assert events[-1]["status"] == "done" and events[-1]["segments"] == []
        assert live.stopped >= 1
        assert list(settings.work_dir.glob("rec-*.wav")) == []
        assert client.get(f"/api/recording/{recording_id}/live").status_code == 409


def test_signing_out_ends_live_transcription(settings, fast_mock_audio) -> None:
    live = FakeLive()
    with running_app(live_settings(settings), live_transcriber_override=live) as client:
        recording_id = client.post("/api/recording/start").json()["recordingId"]
        wait_for(lambda: live_of(client, recording_id).status.value == "listening")
        client.post("/api/auth/logout")
        assert live.stopped >= 1
        assert list(settings.work_dir.glob("rec-*.wav")) == []
        sign_in(client, CLINICIAN, PASSWORD)
        assert client.get(f"/api/recording/{recording_id}/live").status_code == 409


def test_live_is_off_without_a_live_model(settings) -> None:
    with running_app(settings) as client:
        started = client.post("/api/recording/start").json()
        assert started["live"] is False
        assert client.get(f"/api/recording/{started['recordingId']}/live").status_code == 409
        assert client.get("/api/system/status").json()["live"] == "off"
        client.post("/api/recording/cancel", json={"recordingId": started["recordingId"]})


def test_live_transcripts_are_never_logged(settings, fast_mock_audio, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    with running_app(
        live_settings(settings), live_transcriber_override=MockLiveTranscriber(delay_seconds=0)
    ) as client:
        started = client.post("/api/recording/start").json()
        wait_for(lambda: len(live_of(client, started["recordingId"]).snapshot().segments) >= 1)
        stopped = client.post("/api/recording/stop", json={"recordingId": started["recordingId"]}).json()
        wait_job(client, stopped["jobId"])
    assert "What brings you in" not in caplog.text
    assert "live recording=" in caplog.text  # IDs, sizes and timings only


def test_mock_recorder_writes_as_it_records(tmp_path: Path) -> None:
    audio = mock_recorder.pattern(0, mock_recorder.SPEECH_MS + mock_recorder.PAUSE_MS)
    speech_bytes = RATE * mock_recorder.SPEECH_MS // 1000 * 2
    assert any(audio[:speech_bytes])
    assert not any(audio[speech_bytes:])
    assert struct.calcsize("<h") == 2
