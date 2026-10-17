"""Task: draft a SOAP note from the transcript with the clinician's speaker labels."""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..base import AiTask, Message
from ..errors import InvalidOutput
from ..ollama import inline_schema
from ..schemas import MAX_NOTE_FIELD_CHARS, GroundingWarning, SoapFields, SoapRequest, SoapResult
from ..text import format_transcript

SYSTEM_PROMPT = """\
You are a clinical documentation assistant. You draft a SOAP note from a visit transcript. A clinician will review and edit it.

Rules:
- Use only facts stated in the transcript. Never invent symptoms, examination findings, vitals, test results, diagnoses, medications, doses or plans.
- Speaker tags are [Clinician], [Patient] and [Unknown]. The clinician set them. A question asked by the clinician is not a symptom. Record what the patient said as patient-reported ("Patient reports ..."). Do not attribute [Unknown] lines to either person unless the text makes it clear.
- Do not assume a negative finding. Only write "denies" or "no" if the transcript says so.
- If an item is not in the transcript, write "Not documented". Never guess.
- Subjective: the history and symptoms the patient reported, with duration and order of events.
- Objective: examination findings, vitals and test results that were stated.
- Assessment: only what the clinician stated, or what the documented findings directly support. Say when it is uncertain.
- Plan: only treatments, tests and advice that were discussed or decided.
- Keep units, negations and dates exactly as stated. Be concise and professional. Plain text, no markdown.

Answer with JSON containing exactly these keys: subjective, objective, assessment, plan.\
"""


SOAP_KEYS = ("subjective", "objective", "assessment", "plan")


class SoapDraft(BaseModel):
    """The raw answer the model must produce."""

    model_config = ConfigDict(extra="ignore")

    subjective: str = Field(max_length=MAX_NOTE_FIELD_CHARS)
    objective: str = Field(max_length=MAX_NOTE_FIELD_CHARS)
    assessment: str = Field(max_length=MAX_NOTE_FIELD_CHARS)
    plan: str = Field(max_length=MAX_NOTE_FIELD_CHARS)


def _age_text(request: SoapRequest) -> str:
    age = request.context.patient_age
    return f"{age} years" if isinstance(age, int) else "90 years or older"


def build_messages(request: SoapRequest) -> list[Message]:
    context = f"Patient age: {_age_text(request)}. Encounter type: {request.context.encounter_type}."
    user = f"{context}\n\nTranscript:\n{format_transcript(request.segments)}"
    return [Message("system", SYSTEM_PROMPT), Message("user", user)]


# --- grounding: flag numbers in the note that the transcript never mentioned ------------

_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
_LIST_MARKER = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)
_NUMBER_WORDS = {
    **{w: i for i, w in enumerate("zero one two three four five six seven eight nine ten".split())},
    **{
        w: i + 11
        for i, w in enumerate(
            "eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
        )
    },
    **{w: 20 + 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())},
    "hundred": 100,
    "a": 1,
    "an": 1,
}


def _numbers_in_transcript(text: str, extra: set[str]) -> set[str]:
    found = set(_NUMBER.findall(text)) | extra
    for word in re.findall(r"[a-z]+", text.casefold()):
        if word in _NUMBER_WORDS and word not in {"a", "an"}:
            found.add(str(_NUMBER_WORDS[word]))
    # "one hundred and one", "twenty five": add simple sums of adjacent number words.
    words = re.findall(r"[a-z]+", text.casefold())
    for first, second in zip(words, words[1:], strict=False):
        if (
            first in _NUMBER_WORDS
            and second in _NUMBER_WORDS
            and _NUMBER_WORDS[first] >= 20 > _NUMBER_WORDS[second]
        ):
            found.add(str(_NUMBER_WORDS[first] + _NUMBER_WORDS[second]))
    return found


def grounding_warnings(request: SoapRequest, soap: SoapFields) -> list[GroundingWarning]:
    age = request.context.patient_age
    known = _numbers_in_transcript(
        " ".join(segment.text for segment in request.segments), {str(age)} if isinstance(age, int) else set()
    )
    warnings: list[GroundingWarning] = []
    for name, value in soap.items():
        seen: set[str] = set()
        for number in _NUMBER.findall(_LIST_MARKER.sub("", value)):
            if number not in known and number not in seen:
                seen.add(number)
                warnings.append(
                    GroundingWarning(
                        field=name,
                        message=f'The value "{number}" in {name.capitalize()} was not found in the transcript. Please check it.',
                    )
                )
    return warnings


def parse(request: SoapRequest, data: dict[str, Any]) -> SoapResult:
    try:
        draft = SoapDraft.model_validate(data)
    except ValidationError as exc:
        raise InvalidOutput("schema") from exc
    cleaned = {name: value.strip() for name, value in draft.model_dump().items()}
    if not all(cleaned.values()):
        raise InvalidOutput("empty_field")
    soap = SoapFields(**cleaned)
    return SoapResult(soap=soap, warnings=grounding_warnings(request, soap))


def _mock(request: SoapRequest) -> dict[str, Any]:
    """A deterministic stand-in built only from the transcript (no model involved)."""
    patient = [s.text for s in request.segments if s.speaker == "patient"]
    unknown = [s.text for s in request.segments if s.speaker == "unknown"]
    clinician = [s.text for s in request.segments if s.speaker == "clinician"]
    reported = patient or unknown
    measured = [text for text in clinician if re.search(r"\d", text)]
    return {
        "subjective": "Mock draft (no AI model used). Patient reports: " + " ".join(reported[:3])
        if reported
        else "Not documented.",
        "objective": " ".join(measured[:2]) if measured else "Not documented.",
        "assessment": "Not documented (mock output; no AI model used).",
        "plan": "Not documented (mock output; no AI model used).",
    }


TASK: AiTask[SoapRequest] = AiTask(
    name="soap",
    build_messages=build_messages,
    schema=inline_schema(SoapDraft.model_json_schema()),
    max_tokens=900,
    mock=_mock,
)
