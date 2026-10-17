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
You are a clinical documentation assistant. You write down what was said in a visit, as a SOAP note draft. A clinician will review and edit it. You are not a clinician: you never diagnose, you never suggest causes, and you never add medical knowledge of your own.

Rules:
- Use only facts stated in the transcript. Never invent symptoms, examination findings, vitals, test results, diagnoses, medications, doses or plans.
- Speaker tags are [Clinician], [Patient] and [Unknown]. The clinician set them. A question asked by the clinician is not a symptom. Record what the patient said as patient-reported ("Patient reports ..."). Do not attribute [Unknown] lines to either person unless the text makes it clear.
- Do not assume a negative finding. Only write "denies" or "no" if the transcript says so.
- Subjective: the history and symptoms the patient reported, with duration and order of events.
- Objective: examination findings, vitals and test results that were stated. A test result the clinician states (for example "the rapid test is positive") is recorded here as a result. It is not a diagnosis.
- Assessment: ONLY a diagnosis or clinical impression that the clinician said out loud. Copy their wording. Do NOT work out a diagnosis from the symptoms, the findings or a test result, even when it seems obvious. Do NOT add possible causes, a differential or your own reasoning. If the clinician did not say a diagnosis or impression, set assessmentStated to false and write exactly "Not documented." as the assessment.
- Plan: ONLY treatments, medications, tests, referrals, advice and follow-up that the clinician said out loud, with the doses and durations they gave. Do NOT add standard care or advice that was not said. If none was said, set planStated to false and write exactly "Not documented." as the plan.
- If anything else is missing from the transcript, write "Not documented". Never guess.
- Keep units, negations and dates exactly as stated. Be concise and professional. Plain text, no markdown.

Answer with JSON using exactly these keys, in this order: subjective, objective, assessmentStated, assessment, planStated, plan. Decide assessmentStated and planStated before you write the text.

Example of the format. It is a different visit, shown only to illustrate the format; never copy its content.
[Patient] My right knee has hurt for two weeks.
[Clinician] Does it swell?
[Patient] A little, in the evenings.
[Clinician] There is mild swelling over the joint. I will arrange an X-ray.
{"subjective": "Patient reports right knee pain for two weeks, with slight swelling in the evenings.", "objective": "Mild swelling over the knee joint.", "assessmentStated": false, "assessment": "Not documented.", "planStated": true, "plan": "X-ray to be arranged."}\
"""


SOAP_KEYS = ("subjective", "objective", "assessment", "plan")


class SoapDraft(BaseModel):
    """The raw answer the model must produce."""

    model_config = ConfigDict(extra="ignore")

    # The order matters: the model decides whether a diagnosis and a plan were stated *before* it
    # writes those sections, which small models do far more reliably than "don't diagnose".
    subjective: str = Field(max_length=MAX_NOTE_FIELD_CHARS)
    objective: str = Field(max_length=MAX_NOTE_FIELD_CHARS)
    assessmentStated: bool  # noqa: N815
    assessment: str = Field(max_length=MAX_NOTE_FIELD_CHARS)
    planStated: bool  # noqa: N815
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


NOT_DOCUMENTED = "Not documented."

# Wording that presents a diagnosis or a guess at a cause...
_DIAGNOSTIC_WORDING = re.compile(
    r"consistent with|suggestive of|suggests?\b|likely|probabl|possible|possibly|suspect|suspicious for|"
    r"differential|rule out|indicative of|diagnos(?:is|ed)|could be|may be|might be|attributed to",
    re.IGNORECASE,
)
# ...and wording the clinician might have used when stating an impression themselves.
_IMPRESSION_CUE = re.compile(
    r"diagnos|impression|suspect|likely|probabl|looks like|sounds like|consistent with|could be|might be|"
    r"may be|i think|i believe|signs? of|suggests?\b",
    re.IGNORECASE,
)


def diagnosis_warning(request: SoapRequest, assessment: str) -> GroundingWarning | None:
    """Flag diagnostic wording in the Assessment when nobody but the patient said anything like it."""
    if not _DIAGNOSTIC_WORDING.search(assessment):
        return None
    spoken_by_others = " ".join(s.text for s in request.segments if s.speaker != "patient")
    if _IMPRESSION_CUE.search(spoken_by_others):
        return None
    return GroundingWarning(
        field="assessment",
        message=(
            "The Assessment reads like a diagnosis, but no diagnosis or impression was found in what the "
            "clinician said. Remove it unless you agree with it."
        ),
    )


def parse(request: SoapRequest, data: dict[str, Any]) -> SoapResult:
    try:
        draft = SoapDraft.model_validate(data)
    except ValidationError as exc:
        raise InvalidOutput("schema") from exc
    cleaned = {name: value.strip() for name, value in draft.model_dump().items() if isinstance(value, str)}
    # If the model says nothing was stated, nothing is shown, whatever it wrote anyway.
    if not draft.assessmentStated:
        cleaned["assessment"] = NOT_DOCUMENTED
    if not draft.planStated:
        cleaned["plan"] = NOT_DOCUMENTED
    if not all(cleaned.values()):
        raise InvalidOutput("empty_field")
    soap = SoapFields(**cleaned)
    warnings = grounding_warnings(request, soap)
    if (warning := diagnosis_warning(request, soap.assessment)) is not None:
        warnings.append(warning)
    return SoapResult(soap=soap, warnings=warnings)


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
        "assessmentStated": False,
        "assessment": "Not documented.",
        "planStated": False,
        "plan": "Not documented.",
    }


TASK: AiTask[SoapRequest] = AiTask(
    name="soap",
    build_messages=build_messages,
    schema=inline_schema(SoapDraft.model_json_schema()),
    max_tokens=900,
    mock=_mock,
)
