"""The SOAP task: prompt, output validation, grounding warnings and retry. Synthetic text only."""

import pytest
from helpers import CONTEXT, SOAP_ANSWER, VISIT, FakeAiClient, segments, soap_payload

from airsoap.ai.errors import AiError, InvalidOutput
from airsoap.ai.runner import run_task
from airsoap.ai.schemas import SoapRequest
from airsoap.ai.tasks import soap

pytestmark = pytest.mark.anyio


def request_for(segs=None) -> SoapRequest:
    return SoapRequest.model_validate(soap_payload(segs))


def test_prompt_carries_labels_age_and_the_documentation_rules():
    system, user = soap.build_messages(request_for())
    assert system.role == "system" and user.role == "user"
    # The labels the clinician set reach the model, as does the permitted context.
    assert "[Clinician] What brings you in today?" in user.content
    assert "[Patient] I have had a sore throat for three days" in user.content
    assert "Patient age: 35 years. Encounter type: acute." in user.content
    # The rules the note must follow.
    for rule in (
        "Never invent",
        "A question asked by the clinician is not a symptom",
        "Do not assume a negative",
    ):
        assert rule in system.content
    assert "Not documented" in system.content
    # Nothing but the transcript and the context goes in: no IDs, no identifiers.
    assert "seg-1" not in user.content


def test_unknown_speaker_and_age_over_ninety_are_described_plainly():
    request = SoapRequest.model_validate(
        {
            "segments": segments(("unknown", "Hello there.")),
            "context": {"patientAge": "90+", "encounterType": "wellness"},
        }
    )
    user = soap.build_messages(request)[1].content
    assert "[Unknown] Hello there." in user
    assert "90 years or older" in user


def test_valid_answer_gives_all_four_sections():
    result = soap.parse(request_for(), SOAP_ANSWER)
    assert result.soap.subjective.startswith("Patient reports a sore throat")
    assert result.soap.objective == "Temperature 38.3. Tonsils swollen."
    assert (result.soap.assessment, result.soap.plan) == ("Not documented.", "Not documented.")
    assert result.warnings == []


def test_missing_information_stays_missing():
    answer = {**SOAP_ANSWER, "assessment": "  Not documented  ", "plan": "Not documented"}
    result = soap.parse(request_for(), answer)
    assert result.soap.assessment == "Not documented"  # trimmed, never filled in
    assert result.soap.plan == "Not documented"


@pytest.mark.parametrize(
    "answer",
    [
        {"subjective": "a", "objective": "b", "assessment": "c"},  # a section is missing
        {**SOAP_ANSWER, "plan": ""},  # empty section
        {**SOAP_ANSWER, "plan": "   "},
        {**SOAP_ANSWER, "objective": 42},  # wrong type
        {**SOAP_ANSWER, "subjective": "x" * 3001},  # over the length limit
        {},
    ],
)
def test_malformed_answers_are_rejected(answer):
    with pytest.raises(InvalidOutput):
        soap.parse(request_for(), answer)


def test_extra_keys_from_the_model_are_ignored():
    result = soap.parse(request_for(), {**SOAP_ANSWER, "diagnosis": "invented"})
    assert not hasattr(result.soap, "diagnosis")


class TestGrounding:
    def test_a_number_missing_from_the_transcript_is_flagged(self):
        answer = {**SOAP_ANSWER, "objective": "Temperature 39.9. Heart rate 120. Tonsils swollen."}
        result = soap.parse(request_for(), answer)
        flagged = {(w.field, w.message.split('"')[1]) for w in result.warnings}
        assert flagged == {("objective", "39.9"), ("objective", "120")}
        assert all("Please check it" in w.message for w in result.warnings)

    def test_numbers_from_the_transcript_are_not_flagged(self):
        assert soap.parse(request_for(), SOAP_ANSWER).warnings == []

    def test_spelled_out_numbers_and_the_age_count_as_present(self):
        segs = segments(
            ("patient", "I have had it for ten days and I take twenty five milligrams."),
            ("clinician", "A hundred and one is a fever."),
        )
        answer = {
            **SOAP_ANSWER,
            "subjective": "Symptoms for 10 days. Takes 25 mg. A 35-year-old patient.",
            "objective": "Not documented.",
        }
        assert soap.parse(request_for(segs), answer).warnings == []

    def test_list_numbering_is_not_a_clinical_value(self):
        answer = {**SOAP_ANSWER, "plan": "1. Fluids.\n2. Rest.\n3. Return if worse."}
        assert soap.parse(request_for(), answer).warnings == []

    def test_each_value_is_flagged_once_per_field(self):
        answer = {**SOAP_ANSWER, "plan": "Take 500 mg. Repeat 500 mg."}
        assert len([w for w in soap.parse(request_for(), answer).warnings if w.field == "plan"]) == 1


def test_mock_never_turns_a_clinician_question_into_a_patient_symptom():
    segs = segments(("clinician", "Do you have a fever?"), ("clinician", "Any chest pain?"))
    draft = soap.TASK.mock(request_for(segs))
    assert draft["subjective"] == "Not documented."
    assert "fever" not in " ".join(draft.values()).lower()
    assert draft["objective"] == "Not documented."


def test_mock_output_is_itself_valid_and_grounded():
    result = soap.parse(request_for(), soap.TASK.mock(request_for()))
    assert result.warnings == []
    assert "sore throat" in result.soap.subjective


async def test_an_unusable_answer_is_retried_and_the_retry_can_succeed():
    ai = FakeAiClient([{"subjective": "only one section"}, SOAP_ANSWER])
    result, _ = await run_task(ai, soap.TASK, request_for(), soap.parse, max_attempts=2)
    assert result.soap.plan == "Not documented."
    assert ai.calls == [("soap", 0), ("soap", 1)]


async def test_if_every_attempt_is_unusable_the_task_fails_instead_of_returning_it():
    ai = FakeAiClient([{"subjective": "bad"}])
    with pytest.raises(AiError) as excinfo:
        await run_task(ai, soap.TASK, request_for(), soap.parse, max_attempts=2)
    assert excinfo.value.code == "AI_INVALID_OUTPUT"
    assert len(ai.calls) == 2


async def test_service_errors_are_not_retried():
    ai = FakeAiClient([AiError("AI_UNAVAILABLE", "The local AI model service is not running.")])
    with pytest.raises(AiError) as excinfo:
        await run_task(ai, soap.TASK, request_for(), soap.parse, max_attempts=2)
    assert excinfo.value.code == "AI_UNAVAILABLE"
    assert len(ai.calls) == 1


def test_request_limits():
    too_long = segments(*[("patient", "word " * 700)] * 5)  # 17,500 characters
    with pytest.raises(ValueError, match="too long"):
        SoapRequest.model_validate(soap_payload(too_long))
    with pytest.raises(ValueError):
        SoapRequest.model_validate({"segments": [], "context": CONTEXT})
    with pytest.raises(ValueError):
        SoapRequest.model_validate(soap_payload([{"id": "a b", "speaker": "patient", "text": "x"}]))
    with pytest.raises(ValueError):
        SoapRequest.model_validate(soap_payload([{"id": "a", "speaker": "robot", "text": "x"}]))
    assert len(request_for(VISIT).segments) == len(VISIT)
