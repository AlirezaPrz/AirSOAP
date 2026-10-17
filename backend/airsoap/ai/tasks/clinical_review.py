"""Task: optional clinical review of the clinician's edited note against the labelled transcript.

The model only *suggests*. This module then checks every suggestion against the
text it was given: each one must quote evidence that really appears in the
transcript or the note, otherwise it is dropped. Suggestions that advise
starting, stopping or changing a treatment are dropped too. Nothing here ever
edits the note.
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
You assist a clinician who has already reviewed a visit. You receive the visit transcript (speaker tags [Clinician], [Patient], [Unknown] were set or corrected by the clinician) and the clinician's own edited SOAP note. Return suggestions for the clinician to consider. You do not diagnose, prescribe or change the note.

Rules:
- Every suggestion needs evidence: short quotes copied word for word from the transcript or from the note. Set "source" to "transcript" or "note". If you cannot quote supporting text, do not make the suggestion.
- The transcript may be incomplete. Never state that the clinician missed something; say "not found in the transcript".
- informationGaps: relevant details that are absent or unclear. gapType is one of not_asked, asked_not_answered, omitted_from_note, uncertain.
- followUpQuestions: specific questions tied to this case. The rationale says why the question may help.
- differentialConsiderations: only when the documented symptoms, history or findings reasonably support it. Name the consideration, quote the supporting evidence, and in missingInformation say what is missing or what points against it. These are not diagnoses. If nothing is supported, return an empty list.
- documentationInconsistencies: differences between the transcript and the note, such as changed units or numbers, a missing negation, or a plan that does not match the conversation. Quote both sides when you can.
- Never recommend starting, stopping or changing any treatment or medication.
- uncertainty is low, medium or high. Empty lists are fine. At most 5 items per list. Be concise.

Answer with JSON containing exactly these keys: informationGaps, followUpQuestions, differentialConsiderations, documentationInconsistencies.\
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


class RawDifferential(RawItem):
    missingInformation: str  # noqa: N815


class RawReview(BaseModel):
    model_config = ConfigDict(extra="ignore")
    informationGaps: list[RawGap]  # noqa: N815
    followUpQuestions: list[RawItem]  # noqa: N815
    differentialConsiderations: list[RawDifferential]  # noqa: N815
    documentationInconsistencies: list[RawItem]  # noqa: N815


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


def _suggestion_id(category: str, description: str) -> str:
    digest = hashlib.sha256(f"{category}|{normalize(description)}".encode()).hexdigest()
    return f"sug-{digest[:10]}"  # the same suggestion keeps the same ID when the review is rerun


class _Verifier:
    def __init__(self, sources: _Sources) -> None:
        self._sources = sources
        self.omitted = 0

    def convert(self, category: str, items: list[Any]) -> list[SuggestionOut]:
        out: list[SuggestionOut] = []
        seen: set[str] = set()
        for item in items:
            description = item.description.strip()
            rationale = item.rationale.strip()
            evidence = [
                verified
                for raw in item.evidence
                if (verified := self._sources.verify(raw.source, raw.quote)) is not None
            ]
            if not description or not evidence or _advises_treatment(description, rationale):
                self.omitted += 1
                continue
            suggestion_id = _suggestion_id(category, description)
            if suggestion_id in seen:
                continue
            seen.add(suggestion_id)
            out.append(
                SuggestionOut(
                    id=suggestion_id,
                    category=category,  # type: ignore[arg-type]
                    description=description,
                    rationale=rationale,
                    uncertainty=item.uncertainty,
                    evidence=evidence,
                    gap_type=getattr(item, "gapType", None),
                    missing_information=(getattr(item, "missingInformation", "").strip() or None)
                    if hasattr(item, "missingInformation")
                    else None,
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
        information_gaps=verifier.convert("information_gap", raw.informationGaps),
        follow_up_questions=verifier.convert("follow_up_question", raw.followUpQuestions),
        differential_considerations=verifier.convert(
            "differential_consideration", raw.differentialConsiderations
        ),
        documentation_inconsistencies=verifier.convert(
            "documentation_inconsistency", raw.documentationInconsistencies
        ),
        omitted_unverified=verifier.omitted,
    )


def _mock(request: ClinicalReviewRequest) -> dict[str, Any]:
    """Deterministic stand-in: quotes the first transcript line, so the evidence always verifies."""
    quote = " ".join(request.segments[0].text.split()[:12])
    evidence = [{"source": "transcript", "quote": quote}]
    return {
        "informationGaps": [],
        "followUpQuestions": [
            {
                "description": "Mock suggestion: has this changed since it started?",
                "rationale": "Mock output; no AI model was used.",
                "uncertainty": "low",
                "evidence": evidence,
            }
        ],
        "differentialConsiderations": [],
        "documentationInconsistencies": [],
    }


TASK: AiTask[ClinicalReviewRequest] = AiTask(
    name="clinical-review",
    build_messages=build_messages,
    schema=inline_schema(RawReview.model_json_schema()),
    max_tokens=1500,
    mock=_mock,
)
