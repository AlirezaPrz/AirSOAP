"""Request and response contracts for the AI tasks. JSON is camelCase to match the frontend.

Requests carry no patient identifiers: the frontend removes the name, date of
birth and MRN before sending, and sends only age and encounter type as context.
"""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from ..models import CamelModel

MAX_SEGMENTS = 400
MAX_TRANSCRIPT_CHARS = 12_000
MAX_NOTE_FIELD_CHARS = 3_000
MAX_COMBINED_CHARS = 16_000

Speaker = Literal["clinician", "patient", "unknown"]
EncounterType = Literal["acute", "follow-up", "new-patient", "wellness"]
SoapField = Literal["subjective", "objective", "assessment", "plan"]
SOAP_FIELDS: tuple[SoapField, ...] = ("subjective", "objective", "assessment", "plan")
SegmentId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


class ClinicalContext(CamelModel):
    """The only demographics the model may see. Ages of 90 and over arrive as '90+'."""

    patient_age: Annotated[int, Field(ge=0, le=89)] | Literal["90+"]
    encounter_type: EncounterType


class LabeledSegment(CamelModel):
    """One transcript segment with the speaker label the clinician set (or `unknown`)."""

    id: SegmentId
    speaker: Speaker
    text: Annotated[str, Field(min_length=1, max_length=4_000)]


class SoapFields(CamelModel):
    subjective: Annotated[str, Field(max_length=MAX_NOTE_FIELD_CHARS)] = ""
    objective: Annotated[str, Field(max_length=MAX_NOTE_FIELD_CHARS)] = ""
    assessment: Annotated[str, Field(max_length=MAX_NOTE_FIELD_CHARS)] = ""
    plan: Annotated[str, Field(max_length=MAX_NOTE_FIELD_CHARS)] = ""

    def items(self) -> list[tuple[SoapField, str]]:
        return [(name, getattr(self, name)) for name in SOAP_FIELDS]


def _transcript_chars(segments: list[LabeledSegment]) -> int:
    return sum(len(segment.text) for segment in segments)


class SoapRequest(CamelModel):
    segments: Annotated[list[LabeledSegment], Field(min_length=1, max_length=MAX_SEGMENTS)]
    context: ClinicalContext

    @model_validator(mode="after")
    def _within_size_limit(self) -> "SoapRequest":
        if _transcript_chars(self.segments) > MAX_TRANSCRIPT_CHARS:
            raise ValueError("transcript too long")
        return self


class ClinicalReviewRequest(CamelModel):
    segments: Annotated[list[LabeledSegment], Field(min_length=1, max_length=MAX_SEGMENTS)]
    soap: SoapFields
    context: ClinicalContext

    @model_validator(mode="after")
    def _within_size_limit(self) -> "ClinicalReviewRequest":
        transcript = _transcript_chars(self.segments)
        note = sum(len(value) for _, value in self.soap.items())
        if transcript > MAX_TRANSCRIPT_CHARS or transcript + note > MAX_COMBINED_CHARS:
            raise ValueError("input too long")
        if not note:
            raise ValueError("the SOAP note is empty")
        return self


class SpeakerSegment(CamelModel):
    """One transcript turn to label: its ID and its words (identifiers removed by the browser)."""

    id: SegmentId
    text: Annotated[str, Field(min_length=1, max_length=4_000)]


class SpeakerRequest(CamelModel):
    segments: Annotated[list[SpeakerSegment], Field(min_length=1, max_length=MAX_SEGMENTS)]

    @model_validator(mode="after")
    def _within_size_limit(self) -> "SpeakerRequest":
        if sum(len(segment.text) for segment in self.segments) > MAX_TRANSCRIPT_CHARS:
            raise ValueError("transcript too long")
        return self


class SpeakerSuggestion(CamelModel):
    id: str
    speaker: Speaker


class SpeakerResult(CamelModel):
    """One suggestion per requested turn, in order. The words are never returned or changed."""

    suggestions: list[SpeakerSuggestion]


# --- SOAP result -----------------------------------------------------------------


class GroundingWarning(CamelModel):
    """A value in the generated note that does not appear in the transcript."""

    field: SoapField
    message: str


class SoapResult(CamelModel):
    soap: SoapFields
    warnings: list[GroundingWarning]


# --- Clinical review result ----------------------------------------------------


class EvidenceOut(CamelModel):
    source: Literal["transcript", "note"]
    quote: str
    segment_id: str | None = None
    field: SoapField | None = None


class SuggestionOut(CamelModel):
    id: str
    category: Literal["information_gap", "documentation_inconsistency"]
    description: str
    rationale: str
    uncertainty: Literal["low", "medium", "high"]
    # Gaps: the quotes that show where the gap is. Inconsistencies: [as said, as written].
    evidence: list[EvidenceOut]
    gap_type: Literal["not_asked", "asked_not_answered", "omitted_from_note", "uncertain"] | None = None
    # A question the clinician could ask to close a gap.
    suggested_question: str | None = None
    inconsistency_kind: (
        Literal["number_or_unit", "negation", "laterality", "medication", "plan", "other"] | None
    ) = None


class ClinicalReviewResult(CamelModel):
    information_gaps: list[SuggestionOut]
    documentation_inconsistencies: list[SuggestionOut]
    # Suggestions the model made that were left out because they could not be checked: a quote
    # not found in the supplied text, an "inconsistency" whose note text was said as written,
    # or advice to change treatment.
    omitted_unverified: int
