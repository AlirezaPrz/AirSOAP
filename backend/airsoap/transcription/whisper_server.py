"""Client for a persistent, loopback-only whisper.cpp ``whisper-server``.

The server loads ``ggml-small.en.bin`` once at startup; each recording is sent
as a single ``POST /inference`` request. Only the audio and decoding options
are sent: no prompt, no patient details.
"""

from pathlib import Path

import httpx
from asyncer import asyncify

from .base import TranscriptionError, TranscriptResult
from .text import parse_whisper_response

REACHABILITY_TIMEOUT_SECONDS = 1.5
CONNECT_TIMEOUT_SECONDS = 5.0


class WhisperServerTranscriber:
    name = "whisper-server"

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        language: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout_seconds, connect=CONNECT_TIMEOUT_SECONDS)
        self._language = language
        self._transport = transport

    def _client(self, timeout: httpx.Timeout | float) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base_url, timeout=timeout, transport=self._transport)

    async def transcribe(self, path: Path, duration_ms: int) -> TranscriptResult:
        audio = await asyncify(path.read_bytes)()
        form = {
            "response_format": "verbose_json",
            "temperature": "0.0",
            "language": self._language,
        }
        try:
            async with self._client(self._timeout) as client:
                response = await client.post(
                    "/inference",
                    files={"file": ("recording.wav", audio, "audio/wav")},
                    data=form,
                )
        except httpx.TimeoutException as exc:
            raise TranscriptionError(
                "TRANSCRIPTION_TIMEOUT", "Transcription took too long and was stopped."
            ) from exc
        except httpx.TransportError as exc:
            raise TranscriptionError(
                "TRANSCRIBER_UNAVAILABLE", "The local transcription service is not running."
            ) from exc

        if response.status_code != 200:
            raise TranscriptionError(
                "TRANSCRIPTION_FAILED", "The local transcription service reported an error."
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise TranscriptionError(
                "TRANSCRIPTION_FAILED", "The transcription service returned an unreadable response."
            ) from exc
        if isinstance(payload, dict) and "error" in payload:
            raise TranscriptionError(
                "TRANSCRIPTION_FAILED", "The local transcription service reported an error."
            )
        return parse_whisper_response(payload, duration_ms)

    async def is_reachable(self) -> bool:
        try:
            async with self._client(REACHABILITY_TIMEOUT_SECONDS) as client:
                response = await client.get("/")
        except httpx.HTTPError:
            return False
        # whisper-server answers 503 while the model is still loading.
        return response.status_code < 500
