"""The Clinical Review task: it only suggests, and every suggestion must be backed by real text."""

import copy

import pytest
from helpers import NOTE, VISIT, item, review_answer, review_payload, segments

from airsoap.ai.errors import InvalidOutput
from airsoap.ai.schemas import ClinicalReviewRequest
from airsoap.ai.tasks import clinical_review as review


def request_for(note=None, segs=None) -> ClinicalReviewRequest:
    return ClinicalReviewRequest.model_validate(review_payload(note, segs))


def test_prompt_carries_the_confirmed_labels_and_the_doctors_edited_note():
    labelled = segments(
        ("clinician", "Does the pain spread to your ear?"),
        ("patient", "Yes, to the right ear."),
        ("unknown", "Okay."),
    )
    edited = {**NOTE, "assessment": "Pharyngitis with referred otalgia, edited by the doctor."}
    system, user = review.build_messages(request_for(edited, labelled))
    assert "[Clinician] Does the pain spread to your ear?" in user.content
    assert "[Patient] Yes, to the right ear." in user.content
    assert "[Unknown] Okay." in user.content
    assert "Assessment: Pharyngitis with referred otalgia, edited by the doctor." in user.content
    assert "Plan: Fluids and rest." in user.content
    assert (
        "does not diagnose" in system.content.replace("do not diagnose", "does not diagnose")
        or "do not diagnose" in system.content
    )
    assert "Never recommend starting, stopping or changing any treatment" in system.content


def test_all_four_lists_are_returned_with_stable_ids_and_linked_evidence():
    answer = review_answer(
        informationGaps=[
            item(
                "Allergy answer not found in the transcript",
                "Do you have any drug allergies?",
                gapType="asked_not_answered",
            )
        ],
        followUpQuestions=[item("Has the fever persisted today?", "I felt feverish last night")],
        differentialConsiderations=[
            item(
                "Streptococcal pharyngitis",
                "your tonsils are swollen",
                missingInformation="No rapid test result is documented.",
            )
        ],
        documentationInconsistencies=[item("Temperature unit differs", "Temp 38.3 C", source="note")],
    )
    result = review.parse(request_for(), answer)

    gap = result.information_gaps[0]
    assert gap.gap_type == "asked_not_answered"
    assert gap.evidence[0].segment_id == "seg-6"  # linked to the real transcript segment
    assert result.follow_up_questions[0].evidence[0].segment_id == "seg-4"
    differential = result.differential_considerations[0]
    assert differential.missing_information == "No rapid test result is documented."
    assert differential.evidence[0].segment_id == "seg-5"
    inconsistency = result.documentation_inconsistencies[0]
    assert (inconsistency.evidence[0].source, inconsistency.evidence[0].field) == ("note", "objective")
    assert result.omitted_unverified == 0
    assert all(s.id.startswith("sug-") for s in result.information_gaps + result.follow_up_questions)


def test_evidence_matches_despite_case_and_punctuation_changes():
    answer = review_answer(followUpQuestions=[item("Q?", "A SORE THROAT, for three days!")])
    assert len(review.parse(request_for(), answer).follow_up_questions) == 1


def test_a_fabricated_quote_removes_the_suggestion():
    answer = review_answer(
        followUpQuestions=[item("Ask about travel?", "the patient returned from abroad last week")],
        informationGaps=[item("Gap", "Do you have any drug allergies?", gapType="uncertain")],
    )
    result = review.parse(request_for(), answer)
    assert result.follow_up_questions == []
    assert len(result.information_gaps) == 1
    assert result.omitted_unverified == 1


def test_a_quote_from_the_wrong_source_is_not_accepted():
    # The note says "Temp 38.3 C"; the transcript never says "Temp".
    answer = review_answer(
        documentationInconsistencies=[item("Mismatch", "Temp 38.3 C", source="transcript")]
    )
    result = review.parse(request_for(), answer)
    assert result.documentation_inconsistencies == []
    assert result.omitted_unverified == 1


def test_a_suggestion_with_no_evidence_is_omitted():
    answer = review_answer(followUpQuestions=[item("Generic question?", None)])
    result = review.parse(request_for(), answer)
    assert result.follow_up_questions == []
    assert result.omitted_unverified == 1


def test_an_unsupported_differential_is_omitted_and_an_empty_list_is_valid():
    unsupported = review_answer(
        differentialConsiderations=[
            item("Myocardial infarction", "crushing chest pain radiating to the arm", missingInformation="")
        ]
    )
    result = review.parse(request_for(), unsupported)
    assert result.differential_considerations == []
    assert result.omitted_unverified == 1

    empty = review.parse(request_for(), review_answer())
    assert empty.differential_considerations == []
    assert empty.omitted_unverified == 0


@pytest.mark.parametrize(
    "advice",
    [
        "Start a course of antibiotics",
        "Stop the current medication",
        "Increase the dose of ibuprofen",
        "Consider changing the treatment to amoxicillin",
    ],
)
def test_advice_to_start_stop_or_change_treatment_is_dropped(advice):
    answer = review_answer(followUpQuestions=[item(advice, "I have had a sore throat")])
    result = review.parse(request_for(), answer)
    assert result.follow_up_questions == []
    assert result.omitted_unverified == 1


def test_documenting_what_already_happened_is_not_treatment_advice():
    answer = review_answer(
        informationGaps=[
            item(
                "The patient stopped taking their medication, but the note omits it",
                "I have had a sore throat",
                gapType="omitted_from_note",
            )
        ]
    )
    assert len(review.parse(request_for(), answer).information_gaps) == 1


def test_asking_about_medication_is_allowed():
    answer = review_answer(
        followUpQuestions=[
            item("Did the patient stop taking any medication recently?", "I have had a sore throat")
        ]
    )
    assert len(review.parse(request_for(), answer).follow_up_questions) == 1


def test_ids_are_stable_across_reruns_and_duplicates_are_merged():
    answer = review_answer(
        followUpQuestions=[
            item("Has the fever persisted?", "felt feverish last night"),
            item("has the fever persisted", "felt feverish last night"),  # same suggestion, different case
        ]
    )
    first = review.parse(request_for(), answer)
    again = review.parse(
        request_for({**NOTE, "plan": "Fluids, rest and review."}), answer
    )  # note edited, review rerun
    assert len(first.follow_up_questions) == 1
    assert first.follow_up_questions[0].id == again.follow_up_questions[0].id


def test_each_list_is_capped():
    many = [item(f"Question number {i}?", "sore throat") for i in range(9)]
    assert len(review.parse(request_for(), review_answer(followUpQuestions=many)).follow_up_questions) == 5


def test_reviewing_never_changes_the_note():
    request = request_for()
    before = copy.deepcopy(request.model_dump())
    review.parse(request, review_answer(followUpQuestions=[item("Q?", "sore throat")]))
    assert request.model_dump() == before


@pytest.mark.parametrize(
    "answer",
    [
        {},
        {"informationGaps": []},  # lists missing
        review_answer(followUpQuestions="not a list"),
        review_answer(followUpQuestions=[{"description": "no other fields"}]),
        review_answer(informationGaps=[{**item("x", "sore throat"), "gapType": "made_up"}]),
    ],
)
def test_malformed_answers_are_rejected(answer):
    with pytest.raises(InvalidOutput):
        review.parse(request_for(), answer)


def test_mock_suggestions_are_backed_by_the_transcript():
    result = review.parse(request_for(), review.TASK.mock(request_for()))
    assert result.follow_up_questions
    assert result.follow_up_questions[0].evidence[0].segment_id == "seg-1"
    assert result.omitted_unverified == 0


def test_request_needs_a_note_and_respects_the_size_limit():
    empty_note = dict.fromkeys(NOTE, "")
    with pytest.raises(ValueError, match="empty"):
        ClinicalReviewRequest.model_validate(review_payload(empty_note))
    big_segments = segments(*[("patient", "word " * 400)] * 5)  # 10,000 chars of transcript
    big_note = dict.fromkeys(NOTE, "n" * 2_900)  # 11,600 chars of note
    with pytest.raises(ValueError, match="too long"):
        ClinicalReviewRequest.model_validate(review_payload(big_note, big_segments))
    assert len(request_for(segs=VISIT).segments) == len(VISIT)
