"""Task: optional clinical review of the clinician's edited note against the labelled transcript.

Two documentation checks, both of which can be verified against the text:

- information gaps: relevant details absent or unclear in the transcript or the note, each with
  an optional question the clinician could ask;
- documentation inconsistencies: places where the note says something different from what was
  said, each quoting both sides.

The model only *suggests*. This module then checks every suggestion against the text it was
given: each quote must really appear where the model says it does, and an "inconsistency" whose
written side was in fact said is dropped, because nothing differs. Suggestions that advise
starting, stopping or changing a treatment are dropped too. Nothing here ever edits the note.
"""

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from ..base import AiTask, Message
from ..errors import InvalidOutput
from ..ollama import inline_schema
from ..schemas import (
    ClinicalReviewRequest,
    ClinicalReviewResult,
    EvidenceOut,
    SoapField,
    SuggestionOut,
)
from ..text import format_transcript, normalize

MAX_PER_CATEGORY = 5
MIN_QUOTE_CHARS = 3

SYSTEM_PROMPT = """\
You check the documentation of a visit that a clinician has already reviewed. You receive the visit transcript (speaker tags [Clinician], [Patient], [Unknown] were set or corrected by the clinician) and the clinician's own edited SOAP note. You do two jobs and nothing else. You do not diagnose, prescribe or change the note.

Job 1, informationGaps: details relevant to the reason for the visit that are absent or unclear.
- gapType: not_asked (no question or statement about it in the transcript), asked_not_answered (asked, but the answer is missing or unclear), omitted_from_note (said in the transcript but missing from the note), uncertain.
- evidence: short quotes copied word for word from the transcript or the note that show where the gap is, for example the complaint it relates to or the unanswered question.
- suggestedQuestion: one short question the clinician could ask to close the gap, or "" when a question does not fit (for example omitted_from_note).
- Never state that the clinician missed something; the transcript may be incomplete. Say "not found in the transcript".
- No generic checklists: only gaps that matter for this visit.

Job 2, documentationInconsistencies: places where the note says something different from what was said.
- kind: number_or_unit (a value, unit or time differs), negation (a "no" or "denies" is lost or added), laterality (left and right differ), medication (name, dose or frequency differs), plan (the plan does not match the conversation), other.
- transcriptQuote: the words as said, copied word for word from the transcript. noteQuote: the words as written, copied word for word from the note.
- Only report real differences. If the note matches what was said, it is not an inconsistency: leave it out. An empty list is the correct answer when nothing differs.
- Example of a real inconsistency: transcriptQuote "take ibuprofen 400 milligrams", noteQuote "Ibuprofen 200 mg", kind medication.
- Example of NOT an inconsistency: transcriptQuote "take ibuprofen 400 milligrams", noteQuote "Ibuprofen 400 mg". Leave it out.

Rules for both:
- Every quote must be copied exactly from the text, word for word. If you cannot quote it, leave the suggestion out.
- Never recommend starting, stopping or changing any treatment or medication.
- uncertainty is low, medium or high: how unsure you are about the suggestion.
- At most 5 items per list. Empty lists are fine. Be concise.

Answer with JSON containing exactly these keys: informationGaps, documentationInconsistencies.\
"""


# --- the model's raw answer ------------------------------------------------------------


class RawEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source: Literal["transcript", "note"]
    quote: str


class RawItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    description: str
    rationale: str
    uncertainty: Literal["low", "medium", "high"]
    evidence: list[RawEvidence]


class RawGap(RawItem):
    gapType: Literal["not_asked", "asked_not_answered", "omitted_from_note", "uncertain"]  # noqa: N815
    suggestedQuestion: str  # noqa: N815  (may be empty)


InconsistencyKind = Literal["number_or_unit", "negation", "laterality", "medication", "plan", "other"]


class RawInconsistency(BaseModel):
    model_config = ConfigDict(extra="ignore")
    description: str
    rationale: str
    uncertainty: Literal["low", "medium", "high"]
    kind: InconsistencyKind
    transcriptQuote: str  # noqa: N815
    noteQuote: str  # noqa: N815


class RawReview(BaseModel):
    model_config = ConfigDict(extra="ignore")
    informationGaps: list[RawGap]  # noqa: N815
    documentationInconsistencies: list[RawInconsistency]  # noqa: N815


# --- prompt --------------------------------------------------------------------------


def _note_text(request: ClinicalReviewRequest) -> str:
    return "\n".join(
        f"{name.capitalize()}: {value.strip() or '(empty)'}" for name, value in request.soap.items()
    )


def build_messages(request: ClinicalReviewRequest) -> list[Message]:
    age = request.context.patient_age
    age_text = f"{age} years" if isinstance(age, int) else "90 years or older"
    user = (
        f"Patient age: {age_text}. Encounter type: {request.context.encounter_type}.\n\n"
        f"Transcript:\n{format_transcript(request.segments)}\n\n"
        f"SOAP note (as edited by the clinician):\n{_note_text(request)}"
    )
    return [Message("system", SYSTEM_PROMPT), Message("user", user)]


# --- verification --------------------------------------------------------------------

# "start/stop/change ... a medication" style advice. Questions are exempt (see _advises_treatment).
_TREATMENT_ADVICE = re.compile(
    r"\b(?:start|starting|begin|beginning|initiate|initiating|stop|stopping|discontinue|discontinuing|cease"
    r"|increase|increasing|decrease|decreasing|reduce|reducing|switch|switching|change|changing"
    r"|prescribe|prescribing|administer|administering)\b"
    r"[^.?!]{0,60}\b(?:medication|medicine|drug|dose|dosage|antibiotics?|treatment|therapy|mg|tablets?|insulin|steroids?)\b",
    re.IGNORECASE,
)


def _advises_treatment(description: str, rationale: str) -> bool:
    texts = [rationale] if description.rstrip().endswith("?") else [description, rationale]
    return any(_TREATMENT_ADVICE.search(text) for text in texts)


class _Sources:
    """The text the model was given, in a form that quotes can be checked against."""

    def __init__(self, request: ClinicalReviewRequest) -> None:
        self.segments = [(segment.id, normalize(segment.text)) for segment in request.segments]
        self.joined_transcript = " ".join(text for _, text in self.segments)
        self.fields: list[tuple[SoapField, str]] = [
            (name, normalize(value)) for name, value in request.soap.items()
        ]
        self.joined_note = " ".join(text for _, text in self.fields)

    def verify(self, source: str, quote: str) -> EvidenceOut | None:
        needle = normalize(quote)
        if len(needle) < MIN_QUOTE_CHARS:
            return None
        if source == "transcript":
            for segment_id, text in self.segments:
                if needle in text:
                    return EvidenceOut(source="transcript", quote=quote.strip(), segment_id=segment_id)
            if needle in self.joined_transcript:  # a quote that spans two segments
                return EvidenceOut(source="transcript", quote=quote.strip())
            return None
        for name, text in self.fields:
            if needle in text:
                return EvidenceOut(source="note", quote=quote.strip(), field=name)
        return EvidenceOut(source="note", quote=quote.strip()) if needle in self.joined_note else None

    def was_said(self, quote: str) -> bool:
        """True when these words appear in the transcript as they are."""
        needle = normalize(quote)
        return len(needle) >= MIN_QUOTE_CHARS and needle in self.joined_transcript


def _suggestion_id(category: str, description: str) -> str:
    digest = hashlib.sha256(f"{category}|{normalize(description)}".encode()).hexdigest()
    return f"sug-{digest[:10]}"  # the same suggestion keeps the same ID when the review is rerun


class _Verifier:
    def __init__(self, sources: _Sources) -> None:
        self._sources = sources
        self.omitted = 0

    def _keep(self, category: str, description: str, seen: set[str]) -> str | None:
        """The suggestion's stable ID, or None for a duplicate."""
        suggestion_id = _suggestion_id(category, description)
        if suggestion_id in seen:
            return None
        seen.add(suggestion_id)
        return suggestion_id

    def gaps(self, items: list[RawGap]) -> list[SuggestionOut]:
        out: list[SuggestionOut] = []
        seen: set[str] = set()
        for item in items:
            description = item.description.strip()
            rationale = item.rationale.strip()
            question = item.suggestedQuestion.strip()
            evidence = [
                verified
                for raw in item.evidence
                if (verified := self._sources.verify(raw.source, raw.quote)) is not None
            ]
            if not description or not evidence or _advises_treatment(description, rationale):
                self.omitted += 1
                continue
            if (suggestion_id := self._keep("information_gap", description, seen)) is None:
                continue
            out.append(
                SuggestionOut(
                    id=suggestion_id,
                    category="information_gap",
                    description=description,
                    rationale=rationale,
                    uncertainty=item.uncertainty,
                    evidence=evidence,
                    gap_type=item.gapType,
                    # A question is never treatment advice, but one that is not a question is dropped.
                    suggested_question=question if question.endswith("?") else None,
                )
            )
        return out[:MAX_PER_CATEGORY]

    def inconsistencies(self, items: list[RawInconsistency]) -> list[SuggestionOut]:
        out: list[SuggestionOut] = []
        seen: set[str] = set()
        for item in items:
            description = item.description.strip()
            rationale = item.rationale.strip()
            said = self._sources.verify("transcript", item.transcriptQuote)
            written = self._sources.verify("note", item.noteQuote)
            if (
                not description
                or said is None
                or written is None
                # The written words were said as they are: nothing differs.
                or self._sources.was_said(item.noteQuote)
                or normalize(item.noteQuote) == normalize(item.transcriptQuote)
                or _advises_treatment(description, rationale)
            ):
                self.omitted += 1
                continue
            if (suggestion_id := self._keep("documentation_inconsistency", description, seen)) is None:
                continue
            out.append(
                SuggestionOut(
                    id=suggestion_id,
                    category="documentation_inconsistency",
                    description=description,
                    rationale=rationale,
                    uncertainty=item.uncertainty,
                    evidence=[said, written],
                    inconsistency_kind=item.kind,
                )
            )
        return out[:MAX_PER_CATEGORY]


def parse(request: ClinicalReviewRequest, data: dict[str, Any]) -> ClinicalReviewResult:
    try:
        raw = RawReview.model_validate(data)
    except ValidationError as exc:
        raise InvalidOutput("schema") from exc
    verifier = _Verifier(_Sources(request))
    return ClinicalReviewResult(
        information_gaps=verifier.gaps(raw.informationGaps),
        documentation_inconsistencies=verifier.inconsistencies(raw.documentationInconsistencies),
        omitted_unverified=verifier.omitted,
    )


def _mock(request: ClinicalReviewRequest) -> dict[str, Any]:
    """Deterministic stand-in: quotes the first transcript line, so the evidence always verifies."""
    quote = " ".join(request.segments[0].text.split()[:12])
    return {
        "informationGaps": [
            {
                "description": "Mock suggestion: when this started is not clear in the transcript",
                "rationale": "Mock output; no AI model was used.",
                "uncertainty": "low",
                "gapType": "uncertain",
                "suggestedQuestion": "When did this start, and has it changed since?",
                "evidence": [{"source": "transcript", "quote": quote}],
            }
        ],
        "documentationInconsistencies": [],
    }


TASK: AiTask[ClinicalReviewRequest] = AiTask(
    name="clinical-review",
    build_messages=build_messages,
    schema=inline_schema(RawReview.model_json_schema()),
    max_tokens=1000,
    mock=_mock,
)
