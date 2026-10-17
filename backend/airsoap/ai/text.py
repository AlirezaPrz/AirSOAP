"""Text helpers shared by the AI tasks."""

import re

from .schemas import LabeledSegment

_SPEAKER_TAG = {"clinician": "Clinician", "patient": "Patient", "unknown": "Unknown"}
_WHITESPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w\s]")


def format_transcript(segments: list[LabeledSegment]) -> str:
    """One line per segment, e.g. ``[Patient] I have had a sore throat for three days.``"""
    return "\n".join(
        f"[{_SPEAKER_TAG[segment.speaker]}] {_WHITESPACE.sub(' ', segment.text).strip()}"
        for segment in segments
    )


def normalize(text: str) -> str:
    """Lowercase, drop punctuation, collapse spaces: a quote still matches after small formatting changes."""
    return _WHITESPACE.sub(" ", _NON_WORD.sub(" ", text.casefold())).strip()
