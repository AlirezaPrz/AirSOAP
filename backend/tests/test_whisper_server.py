"""whisper-server client, exercised against an in-process fake HTTP transport."""

import re
from pathlib import Path

import httpx
import pytest

from airsoap.audio import synth_tone, write_wav
from airsoap.transcription import TranscriptionError, WhisperServerTranscriber
from airsoap.transcription.text import clean_text, parse_whisper_response

pytestmark = pytest.mark.anyio

VERBOSE_JSON = {
    "task": "transcribe",
    "language": "english",
    "duration": 7.5,
    "text": " Hello there. [BLANK_AUDIO] How are you?",
    "segments": [
        {"id": 0, "start": 0.0, "end": 2.4, "text": " Hello there."},
        {"id": 1, "start": 2.4, "end": 4.0, "text": " [BLANK_AUDIO]"},
        {"id": 2, "start": 4.0, "end": 7.5, "text": " How are you?"},
    ],
}


@pytest.fixture
def wav(tmp_path: Path) -> Path:
    path = tmp_path / "rec.wav"
    write_wav(path, synth_tone(500))
    return path


def transcriber(handler) -> WhisperServerTranscriber:
    return WhisperServerTranscriber(
        base_url="http://127.0.0.1:8178",
        timeout_seconds=5,
        language="en",
        transport=httpx.MockTransport(handler),
    )


async def test_sends_one_inference_request_with_only_audio_and_options(wav: Path):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=VERBOSE_JSON)

    result = await transcriber(handler).transcribe(wav, 7500)

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.url == "http://127.0.0.1:8178/inference"
    body = request.read()
    assert b'name="file"; filename="recording.wav"' in body
    assert b"Content-Type: audio/wav" in body
    assert set(re.findall(rb'form-data; name="([^"]+)"', body)) == {
        b"response_format",
        b"temperature",
        b"language",
        b"file",
    }
    assert b"prompt" not in body

    assert [s.text for s in result.segments] == ["Hello there.", "How are you?"]
    assert result.segments[1].start_ms == 4000
    assert result.text == "Hello there. How are you?"
    assert result.duration_ms == 7500


async def test_connection_refused_means_service_not_running(wav: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(TranscriptionError) as excinfo:
        await transcriber(handler).transcribe(wav, 500)
    assert excinfo.value.code == "TRANSCRIBER_UNAVAILABLE"


async def test_timeout(wav: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(TranscriptionError) as excinfo:
        await transcriber(handler).transcribe(wav, 500)
    assert excinfo.value.code == "TRANSCRIPTION_TIMEOUT"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"error": "failed to read audio"}),
        httpx.Response(200, json={"segments": "nope"}),
        httpx.Response(200, json=["unexpected"]),
    ],
)
async def test_server_errors_and_bad_payloads(wav: Path, response: httpx.Response):
    with pytest.raises(TranscriptionError) as excinfo:
        await transcriber(lambda request: response).transcribe(wav, 500)
    assert excinfo.value.code == "TRANSCRIPTION_FAILED"
    assert "failed to read audio" not in excinfo.value.message


async def test_is_reachable():
    assert await transcriber(lambda r: httpx.Response(200, text="<html>")).is_reachable() is True
    assert await transcriber(lambda r: httpx.Response(503, text="loading")).is_reachable() is False

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert await transcriber(refuse).is_reachable() is False


def test_plain_json_text_response_becomes_one_segment():
    result = parse_whisper_response({"text": "  Just text.\n"}, fallback_duration_ms=3200)
    assert len(result.segments) == 1
    assert result.segments[0].end_ms == 3200
    assert result.text == "Just text."
    assert result.duration_ms == 3200


def test_silence_only_response_is_empty_not_an_error():
    result = parse_whisper_response({"text": " [BLANK_AUDIO]"}, fallback_duration_ms=2000)
    assert result.segments == ()
    assert result.text == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  hello   world ", "hello world"),
        ("[BLANK_AUDIO]", ""),
        ("Okay. [ Silence ] Next.", "Okay. Next."),
        ("(music) Hi", "Hi"),
        ("Take [two] tablets", "Take [two] tablets"),
    ],
)
def test_clean_text(raw: str, expected: str):
    assert clean_text(raw) == expected
