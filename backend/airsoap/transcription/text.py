"""Turning whisper-server responses into a clean transcript."""

import re
from typing import Any

from .base import TranscriptionError, TranscriptResult, TranscriptSegment

# Whisper's non-speech annotations, e.g. "[BLANK_AUDIO]", "[ Silence ]", "(music)".
_NON_SPEECH = re.compile(
    r"\[\s*(?:BLANK_AUDIO|SILENCE|MUSIC|NOISE|INAUDIBLE|SOUND)[^\]]*\]"
    r"|\(\s*(?:silence|music|noise|inaudible|blank audio|sound)[^)]*\)",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\s+")


def _malformed() -> TranscriptionError:
    return TranscriptionError(
        "TRANSCRIPTION_FAILED", "The transcription service returned an unreadable response."
    )


def clean_text(text: str) -> str:
    return _WHITESPACE.sub(" ", _NON_SPEECH.sub(" ", text)).strip()


def _seconds_to_ms(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _malformed()
    return max(0, round(value * 1000))


def parse_whisper_response(payload: Any, fallback_duration_ms: int) -> TranscriptResult:
    """Accept whisper-server's ``verbose_json`` (with segments) or plain ``json`` (text only)."""
    if not isinstance(payload, dict):
        raise _malformed()

    segments: list[TranscriptSegment] = []
    raw_segments = payload.get("segments")
    if isinstance(raw_segments, list):
        for raw in raw_segments:
            if not isinstance(raw, dict) or not isinstance(raw.get("text", ""), str):
                raise _malformed()
            text = clean_text(raw.get("text", ""))
            if not text:
                continue
            start_ms = _seconds_to_ms(raw.get("start", 0))
            end_ms = max(start_ms, _seconds_to_ms(raw.get("end", 0)))
            segments.append(TranscriptSegment(start_ms=start_ms, end_ms=end_ms, text=text))
    elif raw_segments is not None:
        raise _malformed()
    else:
        raw_text = payload.get("text", "")
        if not isinstance(raw_text, str):
            raise _malformed()
        text = clean_text(raw_text)
        if text:
            segments.append(TranscriptSegment(start_ms=0, end_ms=fallback_duration_ms, text=text))

    duration = payload.get("duration")
    duration_ms = _seconds_to_ms(duration) if duration is not None else fallback_duration_ms
    language = payload.get("language")
    return TranscriptResult(
        text=" ".join(segment.text for segment in segments),
        language=language if isinstance(language, str) and language else "en",
        duration_ms=duration_ms,
        segments=tuple(segments),
    )
