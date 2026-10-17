"""The SOAP task: prompt, output validation, grounding warnings and retry. Synthetic text only."""

import pytest
from helpers import CONTEXT, SOAP_ANSWER, STATED, VISIT, FakeAiClient, segments, soap_payload

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
    result = soap.parse(request_for(), SOAP_ANSWER)
    assert (result.soap.assessment, result.soap.plan) == ("Not documented.", "Not documented.")


@pytest.mark.parametrize(
    "answer",
    [
        {"subjective": "a", "objective": "b", "assessment": "c"},  # a section is missing
        {**SOAP_ANSWER, **STATED, "plan": ""},  # empty section that was claimed to be stated
        {**SOAP_ANSWER, **STATED, "plan": "   "},
        {**SOAP_ANSWER, "subjective": ""},
        {key: value for key, value in SOAP_ANSWER.items() if key != "assessmentStated"},  # decision missing
        {**SOAP_ANSWER, "planStated": "maybe"},  # not a yes or no
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
        answer = {**SOAP_ANSWER, **STATED, "plan": "1. Fluids.\n2. Rest.\n3. Return if worse."}
        assert soap.parse(request_for(), answer).warnings == []

    def test_each_value_is_flagged_once_per_field(self):
        answer = {**SOAP_ANSWER, **STATED, "plan": "Take 500 mg. Repeat 500 mg."}
        assert len([w for w in soap.parse(request_for(), answer).warnings if w.field == "plan"]) == 1


def test_mock_never_turns_a_clinician_question_into_a_patient_symptom():
    segs = segments(("clinician", "Do you have a fever?"), ("clinician", "Any chest pain?"))
    draft = soap.TASK.mock(request_for(segs))
    assert draft["subjective"] == "Not documented."
    assert "fever" not in " ".join(v for v in draft.values() if isinstance(v, str)).lower()
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


class TestNoInventedDiagnosis:
    """The model once wrote a diagnosis that nobody had said. These checks make that visible or impossible."""

    # A note with no numbers, for the short transcripts below.
    BASE = {"subjective": "Patient reports a sore throat.", "objective": "Not documented."}

    def test_prompt_forbids_working_out_a_diagnosis_and_asks_for_the_decision_first(self):
        system = soap.build_messages(request_for())[0].content
        assert "ONLY a diagnosis or clinical impression that the clinician said out loud" in system
        assert "Do NOT work out a diagnosis from the symptoms" in system
        assert "even when it seems obvious" in system
        assert "is not a diagnosis" in system  # a test result stated aloud is not a diagnosis
        assert "what the documented findings directly support" not in system  # the old wording invited it
        assert "Decide assessmentStated and planStated before you write the text" in system

    def test_the_model_decides_before_it_writes(self):
        assert list(soap.TASK.schema["properties"]) == [
            "subjective",
            "objective",
            "assessmentStated",
            "assessment",
            "planStated",
            "plan",
        ]

    def test_a_diagnosis_nobody_stated_is_removed_even_if_the_model_wrote_one(self):
        answer = {
            **SOAP_ANSWER,
            "assessmentStated": False,
            "assessment": "Acute bacterial pharyngitis, likely streptococcal.",
        }
        result = soap.parse(request_for(), answer)
        assert result.soap.assessment == "Not documented."
        assert "pharyngitis" not in result.model_dump_json()

    def test_a_plan_nobody_stated_is_removed_even_if_the_model_wrote_one(self):
        answer = {**SOAP_ANSWER, "planStated": False, "plan": "Start amoxicillin 500 mg for 10 days."}
        assert soap.parse(request_for(), answer).soap.plan == "Not documented."

    def test_a_stated_impression_is_kept_as_written(self):
        segs = segments(
            ("patient", "My throat hurts."), ("clinician", "I think this is a viral throat infection.")
        )
        answer = {
            **SOAP_ANSWER,
            **self.BASE,
            **STATED,
            "assessment": "Viral throat infection (clinician's impression).",
            "plan": "Not documented.",
        }
        result = soap.parse(request_for(segs), answer)
        assert result.soap.assessment == "Viral throat infection (clinician's impression)."
        assert result.warnings == []

    def test_diagnostic_wording_with_nothing_behind_it_is_flagged(self):
        # The model claims the clinician stated an impression, but the transcript has none.
        answer = {
            **SOAP_ANSWER,
            "assessmentStated": True,
            "assessment": "Findings are consistent with strep throat.",
        }
        result = soap.parse(request_for(), answer)
        assert result.soap.assessment == "Findings are consistent with strep throat."  # shown, but flagged
        flagged = [w for w in result.warnings if w.field == "assessment"]
        assert len(flagged) == 1
        assert "no diagnosis or impression was found" in flagged[0].message

    def test_a_patient_saying_it_does_not_count_as_the_clinician_stating_it(self):
        segs = segments(
            ("patient", "I think it is probably strep, a friend had it."),
            ("clinician", "Your temperature is 38.3 and your tonsils are swollen."),
        )
        answer = {**SOAP_ANSWER, "assessmentStated": True, "assessment": "Probable strep throat."}
        result = soap.parse(request_for(segs), answer)
        assert any(w.field == "assessment" for w in result.warnings)

    def test_no_warning_when_the_clinician_did_give_an_impression(self):
        segs = segments(("clinician", "This looks like a viral infection. Rest and fluids."))
        answer = {
            **SOAP_ANSWER,
            **self.BASE,
            **STATED,
            "assessment": "Likely viral infection.",
            "plan": "Rest and fluids.",
        }
        assert soap.parse(request_for(segs), answer).warnings == []

    def test_a_clinician_whose_label_is_unknown_still_counts(self):
        segs = segments(("unknown", "I suspect a viral cause."), ("patient", "Okay."))
        answer = {
            **SOAP_ANSWER,
            **self.BASE,
            **STATED,
            "assessment": "Suspected viral cause.",
            "plan": "Not documented.",
        }
        assert soap.parse(request_for(segs), answer).warnings == []

    def test_a_test_result_alone_does_not_become_a_diagnosis_in_the_mock(self):
        segs = segments(("clinician", "The rapid strep test is positive."))
        draft = soap.TASK.mock(request_for(segs))
        assert draft["assessmentStated"] is False
        assert draft["assessment"] == "Not documented."
