"""The Clinical Review task: two documentation checks, and every suggestion must be backed by real text."""

import copy

import pytest
from helpers import NOTE, VISIT, gap, mismatch, review_answer, review_payload, segments

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
    assert "You do not diagnose" in system.content
    assert "Never recommend starting, stopping or changing any treatment" in system.content
    assert "informationGaps, documentationInconsistencies" in system.content


def test_both_lists_are_returned_with_stable_ids_and_linked_evidence():
    answer = review_answer(
        informationGaps=[
            gap(
                "Allergy answer not found in the transcript",
                "Do you have any drug allergies?",
                gapType="asked_not_answered",
                suggestedQuestion="Do you have any drug allergies?",
            )
        ],
        documentationInconsistencies=[
            mismatch("Temperature unit differs", "Your temperature today is 38.3", "Temp 38.3 C")
        ],
    )
    result = review.parse(request_for(), answer)

    found = result.information_gaps[0]
    assert found.gap_type == "asked_not_answered"
    assert found.suggested_question == "Do you have any drug allergies?"
    assert found.evidence[0].segment_id == "seg-6"  # linked to the real transcript segment
    inconsistency = result.documentation_inconsistencies[0]
    assert inconsistency.inconsistency_kind == "number_or_unit"
    said, written = inconsistency.evidence
    assert (said.source, said.segment_id) == ("transcript", "seg-5")
    assert (written.source, written.field) == ("note", "objective")
    assert result.omitted_unverified == 0
    everything = result.information_gaps + result.documentation_inconsistencies
    assert all(s.id.startswith("sug-") for s in everything)


def test_a_suggested_question_that_is_not_a_question_is_dropped_but_the_gap_kept():
    answer = review_answer(
        informationGaps=[gap("Allergies unclear", "drug allergies", suggestedQuestion="Start antibiotics")]
    )
    found = review.parse(request_for(), answer).information_gaps
    assert len(found) == 1
    assert found[0].suggested_question is None


def test_an_inconsistency_whose_note_text_was_said_as_written_is_dropped():
    # The case that prompted this check: the model "found" a dose that matches the transcript.
    visit = segments(
        (
            "clinician",
            "so we'll get an X-ray, apply a splint, and have you take ibuprofen 400 milligrams "
            "every six hours for pain.",
        ),
    )
    note = {**NOTE, "plan": "Take ibuprofen 400 milligrams every six hours for pain."}
    answer = review_answer(
        documentationInconsistencies=[
            mismatch(
                "Units and dosage",
                "have you take ibuprofen 400 milligrams every six hours for pain.",
                "Take ibuprofen 400 milligrams every six hours for pain.",
                kind="medication",
            )
        ]
    )
    result = review.parse(request_for(note, visit), answer)
    assert result.documentation_inconsistencies == []
    assert result.omitted_unverified == 1


def test_a_real_dose_difference_is_kept():
    visit = segments(("clinician", "Take ibuprofen 400 milligrams every six hours."))
    note = {**NOTE, "plan": "Ibuprofen 200 mg every six hours."}
    answer = review_answer(
        documentationInconsistencies=[
            mismatch("Dose differs", "ibuprofen 400 milligrams", "Ibuprofen 200 mg", kind="medication")
        ]
    )
    result = review.parse(request_for(note, visit), answer)
    assert len(result.documentation_inconsistencies) == 1
    assert result.documentation_inconsistencies[0].inconsistency_kind == "medication"


def test_an_inconsistency_needs_both_sides_from_the_right_source():
    answer = review_answer(
        documentationInconsistencies=[
            mismatch("Said side not found", "the patient was afebrile", "Temp 38.3 C"),
            mismatch("Written side not found", "around 101", "Temperature 39 degrees"),
            mismatch("Sides swapped", "Temp 38.3 C", "Your temperature today is 38.3"),
        ]
    )
    result = review.parse(request_for(), answer)
    assert result.documentation_inconsistencies == []
    assert result.omitted_unverified == 3


def test_evidence_matches_despite_case_and_punctuation_changes():
    answer = review_answer(informationGaps=[gap("Gap", "A SORE THROAT, for three days!")])
    assert len(review.parse(request_for(), answer).information_gaps) == 1


def test_a_fabricated_quote_removes_the_suggestion():
    answer = review_answer(
        informationGaps=[
            gap("Travel not found", "the patient returned from abroad last week"),
            gap("Allergies unclear", "Do you have any drug allergies?"),
        ],
    )
    result = review.parse(request_for(), answer)
    assert [g.description for g in result.information_gaps] == ["Allergies unclear"]
    assert result.omitted_unverified == 1


def test_a_quote_from_the_wrong_source_is_not_accepted():
    # The note says "Temp 38.3 C"; the transcript never says "Temp".
    answer = review_answer(informationGaps=[gap("Mismatch", "Temp 38.3 C", source="transcript")])
    result = review.parse(request_for(), answer)
    assert result.information_gaps == []
    assert result.omitted_unverified == 1


def test_a_gap_with_no_evidence_is_omitted_and_empty_lists_are_valid():
    result = review.parse(request_for(), review_answer(informationGaps=[gap("Generic gap", None)]))
    assert result.information_gaps == []
    assert result.omitted_unverified == 1

    empty = review.parse(request_for(), review_answer())
    assert empty.information_gaps == empty.documentation_inconsistencies == []
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
    answer = review_answer(
        informationGaps=[gap(advice, "I have had a sore throat")],
        documentationInconsistencies=[mismatch(advice, "around 101", "Temp 38.3 C")],
    )
    result = review.parse(request_for(), answer)
    assert result.information_gaps == result.documentation_inconsistencies == []
    assert result.omitted_unverified == 2


def test_documenting_what_already_happened_is_not_treatment_advice():
    answer = review_answer(
        informationGaps=[
            gap(
                "The patient stopped taking their medication, but the note omits it",
                "I have had a sore throat",
                gapType="omitted_from_note",
            )
        ]
    )
    assert len(review.parse(request_for(), answer).information_gaps) == 1


def test_asking_about_medication_is_allowed():
    answer = review_answer(
        informationGaps=[
            gap(
                "Recent medication changes not found in the transcript",
                "I have had a sore throat",
                suggestedQuestion="Did you stop taking any medication recently?",
            )
        ]
    )
    found = review.parse(request_for(), answer).information_gaps
    assert found[0].suggested_question == "Did you stop taking any medication recently?"


def test_ids_are_stable_across_reruns_and_duplicates_are_merged():
    answer = review_answer(
        informationGaps=[
            gap("Fever course unclear", "felt feverish last night"),
            gap("fever course unclear", "felt feverish last night"),  # same suggestion, different case
        ]
    )
    first = review.parse(request_for(), answer)
    again = review.parse(request_for({**NOTE, "plan": "Fluids, rest and review."}), answer)  # note edited
    assert len(first.information_gaps) == 1
    assert first.information_gaps[0].id == again.information_gaps[0].id


def test_each_list_is_capped():
    many = [gap(f"Gap number {i}", "sore throat") for i in range(9)]
    assert len(review.parse(request_for(), review_answer(informationGaps=many)).information_gaps) == 5


def test_reviewing_never_changes_the_note():
    request = request_for()
    before = copy.deepcopy(request.model_dump())
    review.parse(request, review_answer(informationGaps=[gap("Gap", "sore throat")]))
    assert request.model_dump() == before


@pytest.mark.parametrize(
    "answer",
    [
        {},
        {"informationGaps": []},  # a list missing
        review_answer(informationGaps="not a list"),
        review_answer(informationGaps=[{"description": "no other fields"}]),
        review_answer(informationGaps=[{**gap("x", "sore throat"), "gapType": "made_up"}]),
        review_answer(documentationInconsistencies=[{**mismatch("x", "a", "b"), "kind": "made_up"}]),
        review_answer(documentationInconsistencies=[{"description": "no quotes"}]),
    ],
)
def test_malformed_answers_are_rejected(answer):
    with pytest.raises(InvalidOutput):
        review.parse(request_for(), answer)


def test_mock_suggestions_are_backed_by_the_transcript():
    result = review.parse(request_for(), review.TASK.mock(request_for()))
    assert result.information_gaps
    assert result.information_gaps[0].evidence[0].segment_id == "seg-1"
    assert result.information_gaps[0].suggested_question
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
