"""Speaker suggestions: turn-numbered answers, strict validation, batching, and the API."""

import pytest
from helpers import FakeAiClient, running_app, wait_for_ai_job

from airsoap.ai.errors import InvalidOutput
from airsoap.ai.schemas import SpeakerRequest, SpeakerSegment
from airsoap.ai.tasks import speakers


def request(n: int) -> SpeakerRequest:
    return SpeakerRequest(
        segments=[SpeakerSegment(id=f"seg-{i}", text=f"Synthetic turn {i}.") for i in range(1, n + 1)]
    )


def answer(*speakers_: str) -> dict:
    return {str(n): s for n, s in enumerate(speakers_, 1)}


def test_a_long_visit_is_labelled_in_batches_with_context() -> None:
    parts = speakers.batches(request(60))
    assert [len(turns) for turns, _ in parts] == [25, 25, 10]
    assert parts[0][1] == []
    assert [s.id for s in parts[1][1]] == ["seg-22", "seg-23", "seg-24", "seg-25"]


def test_the_prompt_numbers_the_turns_and_marks_context_as_not_to_label() -> None:
    turns, earlier = speakers.batches(request(30))[1]
    user = speakers.build_messages((turns, earlier))[1].content
    assert "do not label these" in user
    assert user.splitlines()[-1] == "5. Synthetic turn 30."
    assert "Label these 5 turns:" in user


def test_the_schema_requires_exactly_one_speaker_per_turn() -> None:
    schema = speakers.schema(3)
    assert schema["required"] == ["1", "2", "3"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["2"]["enum"] == ["clinician", "patient", "unknown"]


def test_answers_map_back_to_segment_ids_without_touching_the_words() -> None:
    batch = speakers.batches(request(3))[0]
    result = speakers.parse(batch, answer("clinician", "patient", "unknown"))
    assert [(s.id, s.speaker) for s in result] == [
        ("seg-1", "clinician"),
        ("seg-2", "patient"),
        ("seg-3", "unknown"),
    ]
    assert not hasattr(result[0], "text")


@pytest.mark.parametrize(
    "bad",
    [
        answer("clinician", "patient"),  # a turn missing
        {**answer("clinician", "patient", "unknown"), "4": "patient"},  # an extra turn
        {"1": "clinician", "2": "patient", "4": "unknown"},  # a turn renumbered
        answer("clinician", "nurse", "unknown"),  # not a role we allow
    ],
)
def test_unusable_answers_are_rejected(bad) -> None:
    with pytest.raises(InvalidOutput):
        speakers.parse(speakers.batches(request(3))[0], bad)


PAYLOAD = {
    "segments": [
        {"id": "seg-1", "text": "Do you have any allergies?"},
        {"id": "seg-2", "text": "Not that I know of."},
    ]
}


def test_suggestions_come_back_through_an_ai_job(settings) -> None:
    client_ai = FakeAiClient([answer("clinician", "patient")])
    with running_app(settings, ai_client=client_ai) as client:
        job_id = client.post("/api/ai/speakers", json=PAYLOAD).json()["jobId"]
        body = wait_for_ai_job(client, job_id, {"completed", "error"})
    assert body["status"] == "completed"
    assert body["speakerResult"] == {
        "suggestions": [{"id": "seg-1", "speaker": "clinician"}, {"id": "seg-2", "speaker": "patient"}]
    }
    assert client_ai.calls == [("speakers", 0)]


def test_an_unusable_answer_is_retried_once_then_reported(settings) -> None:
    client_ai = FakeAiClient([answer("clinician"), answer("clinician")])
    with running_app(settings, ai_client=client_ai) as client:
        job_id = client.post("/api/ai/speakers", json=PAYLOAD).json()["jobId"]
        body = wait_for_ai_job(client, job_id, {"completed", "error"})
    assert body["status"] == "error"
    assert body["error"]["code"] == "AI_INVALID_OUTPUT"
    assert client_ai.calls == [("speakers", 0), ("speakers", 1)]


def test_every_batch_is_sent_one_after_another(settings) -> None:
    segments = [{"id": f"seg-{i}", "text": f"Synthetic turn {i}."} for i in range(1, 31)]
    client_ai = FakeAiClient([answer(*["patient"] * 25), answer(*["unknown"] * 5)])
    with running_app(settings, ai_client=client_ai) as client:
        job_id = client.post("/api/ai/speakers", json={"segments": segments}).json()["jobId"]
        body = wait_for_ai_job(client, job_id, {"completed", "error"})
    suggestions = body["speakerResult"]["suggestions"]
    assert len(suggestions) == 30
    assert [s["id"] for s in suggestions] == [s["id"] for s in segments]
    assert client_ai.max_active == 1


def test_an_empty_or_oversized_request_is_refused(settings) -> None:
    with running_app(settings, ai_client=FakeAiClient()) as client:
        assert client.post("/api/ai/speakers", json={"segments": []}).status_code == 422
        too_long = [{"id": f"seg-{i}", "text": "x" * 4000} for i in range(4)]
        assert client.post("/api/ai/speakers", json={"segments": too_long}).status_code == 422
