"""The AI endpoints end to end, with a fake model. Covers jobs, sequencing, cleanup and logging."""

import logging

from helpers import (
    NOTE,
    SOAP_ANSWER,
    FakeAiClient,
    FakeRecorder,
    FakeTranscriber,
    item,
    record_once,
    review_answer,
    review_payload,
    running_app,
    soap_payload,
    wait_for_ai_job,
    wait_for_status,
)

from airsoap.ai.errors import AiError

MARKER = "ZEBRA-MARKER-4471"  # synthetic text that must never reach logs or error bodies


def start(client, path: str, payload: dict) -> str:
    response = client.post(path, json=payload)
    assert response.status_code == 202, response.text
    return response.json()["jobId"]


def test_soap_job_runs_to_completion_and_returns_four_sections(settings):
    ai = FakeAiClient([SOAP_ANSWER])
    with running_app(settings, ai_client=ai) as client:
        accepted = client.post("/api/ai/soap", json=soap_payload()).json()
        assert accepted["kind"] == "soap"
        body = wait_for_ai_job(client, accepted["jobId"], {"completed"})
    assert body["error"] is None
    assert body["reviewResult"] is None
    assert set(body["soapResult"]["soap"]) == {"subjective", "objective", "assessment", "plan"}
    assert body["soapResult"]["soap"]["objective"] == "Temperature 38.3. Tonsils swollen."
    assert body["soapResult"]["warnings"] == []
    assert ai.calls == [("soap", 0)]


def test_soap_job_reports_unverified_numbers_as_warnings(settings):
    answer = {**SOAP_ANSWER, "plan": "Review in 14 days."}
    with running_app(settings, ai_client=FakeAiClient([answer])) as client:
        body = wait_for_ai_job(client, start(client, "/api/ai/soap", soap_payload()), {"completed"})
    assert [w["field"] for w in body["soapResult"]["warnings"]] == ["plan"]


def test_clinical_review_returns_suggestions_and_never_the_note(settings):
    answer = review_answer(followUpQuestions=[item("Has the fever persisted?", "felt feverish last night")])
    with running_app(settings, ai_client=FakeAiClient([answer])) as client:
        job_id = start(client, "/api/ai/clinical-review", review_payload())
        body = wait_for_ai_job(client, job_id, {"completed"})
    assert body["kind"] == "clinical-review"
    assert body["soapResult"] is None
    suggestion = body["reviewResult"]["followUpQuestions"][0]
    assert suggestion["evidence"][0]["segmentId"] == "seg-4"
    assert body["reviewResult"]["omittedUnverified"] == 0
    assert "subjective" not in str(body["reviewResult"])  # the note is not echoed back or changed


def test_rerunning_the_review_after_an_edit_keeps_suggestion_ids_stable(settings):
    answer = review_answer(followUpQuestions=[item("Has the fever persisted?", "felt feverish last night")])
    ids = []
    with running_app(settings, ai_client=FakeAiClient([answer])) as client:
        for note in (NOTE, {**NOTE, "plan": "Fluids, rest and a review in two days."}):
            job_id = start(client, "/api/ai/clinical-review", review_payload(note))
            ids.append(
                wait_for_ai_job(client, job_id, {"completed"})["reviewResult"]["followUpQuestions"][0]["id"]
            )
    assert ids[0] == ids[1]


def test_unusable_model_output_is_retried_then_reported_as_an_error(settings):
    ai = FakeAiClient([{"subjective": "only one section"}])
    with running_app(settings, ai_client=ai) as client:
        body = wait_for_ai_job(client, start(client, "/api/ai/soap", soap_payload()), {"error"})
    assert [c[1] for c in ai.calls] == [0, 1]  # one retry, then give up
    assert body["soapResult"] is None  # an invalid draft is never shown as a result
    assert body["error"]["code"] == "AI_INVALID_OUTPUT"
    assert "only one section" not in str(body)


def test_service_failures_become_safe_errors(settings):
    failure = AiError("AI_UNAVAILABLE", "The local AI model service is not running.", retryable=True)
    with running_app(settings, ai_client=FakeAiClient([failure])) as client:
        body = wait_for_ai_job(client, start(client, "/api/ai/soap", soap_payload()), {"error"})
    assert body["error"] == {
        "code": "AI_UNAVAILABLE",
        "message": "The local AI model service is not running.",
        "retryable": True,
    }


def test_unexpected_exceptions_hide_their_details(settings):
    boom = RuntimeError(f"prompt contained {MARKER} at /home/ai-innovate/secret")
    with running_app(settings, ai_client=FakeAiClient([boom])) as client:
        body = wait_for_ai_job(client, start(client, "/api/ai/soap", soap_payload()), {"error"})
    assert body["error"]["code"] == "AI_FAILED"
    assert MARKER not in str(body)
    assert "/home" not in str(body)


def test_invalid_requests_are_rejected_without_echoing_the_input(settings):
    bad = soap_payload([{"id": "seg-1", "speaker": "robot", "text": f"{MARKER} some text"}])
    with running_app(settings, ai_client=FakeAiClient()) as client:
        response = client.post("/api/ai/soap", json=bad)
    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "INVALID_REQUEST", "message": "The request was not valid.", "retryable": False}
    }
    assert MARKER not in response.text


def test_oversized_requests_get_413(settings):
    with running_app(settings, ai_client=FakeAiClient()) as client:
        response = client.post(
            "/api/ai/soap",
            content=b"{}",
            headers={"Content-Type": "application/json", "Content-Length": "500000"},
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_a_note_with_nothing_in_it_cannot_be_reviewed(settings):
    empty = review_payload(dict.fromkeys(NOTE, ""))
    with running_app(settings, ai_client=FakeAiClient()) as client:
        assert client.post("/api/ai/clinical-review", json=empty).status_code == 422


def test_unknown_and_malformed_job_ids(settings):
    with running_app(settings, ai_client=FakeAiClient()) as client:
        assert client.get(f"/api/ai/jobs/{'a' * 32}").json()["error"]["code"] == "JOB_NOT_FOUND"
        assert client.get("/api/ai/jobs/not-an-id").status_code == 422
        assert client.delete(f"/api/ai/jobs/{'a' * 32}").status_code == 204


# --- one model call at a time ------------------------------------------------------


def test_jobs_never_run_concurrently(settings):
    ai = FakeAiClient([SOAP_ANSWER], gated=True)
    with running_app(settings, ai_client=ai) as client:
        first = start(client, "/api/ai/soap", soap_payload())
        second = start(client, "/api/ai/soap", soap_payload())
        wait_for_ai_job(client, first, {"processing"})
        assert client.get(f"/api/ai/jobs/{second}").json()["status"] == "queued"
        ai.let_finish(client)
        wait_for_ai_job(client, first, {"completed"})
        wait_for_ai_job(client, second, {"completed"})
    assert ai.max_active == 1
    assert len(ai.calls) == 2


def test_the_queue_is_limited(settings):
    ai = FakeAiClient([SOAP_ANSWER], gated=True)
    with running_app(settings, ai_client=ai) as client:
        start(client, "/api/ai/soap", soap_payload())
        start(client, "/api/ai/soap", soap_payload())
        response = client.post("/api/ai/soap", json=soap_payload())
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "AI_QUEUE_FULL"
        ai.let_finish(client)


def test_an_ai_job_waits_for_a_running_transcription_and_whisper_is_freed_first(settings):
    ai = FakeAiClient([SOAP_ANSWER])
    transcriber = FakeTranscriber(gated=True)
    with running_app(settings, recorder=FakeRecorder(), transcriber=transcriber, ai_client=ai) as client:
        transcription = record_once(client)["jobId"]
        wait_for_status(client, transcription, {"processing"})
        assert ai.unloads >= 1  # Qwen was unloaded before whisper-server was used

        job_id = start(client, "/api/ai/soap", soap_payload())
        assert client.get(f"/api/ai/jobs/{job_id}").json()["status"] == "queued"
        assert ai.calls == []  # not started while whisper holds the memory

        transcriber.let_finish(client)
        wait_for_status(client, transcription, {"completed"})
        wait_for_ai_job(client, job_id, {"completed"})
        assert transcriber.released >= 1  # whisper-server was stopped before Qwen ran
    assert transcriber.max_active == 1
    assert ai.max_active == 1


def test_a_transcription_waits_for_a_running_ai_job(settings):
    ai = FakeAiClient([SOAP_ANSWER], gated=True)
    transcriber = FakeTranscriber()
    with running_app(settings, recorder=FakeRecorder(), transcriber=transcriber, ai_client=ai) as client:
        job_id = start(client, "/api/ai/soap", soap_payload())
        wait_for_ai_job(client, job_id, {"processing"})
        transcription = record_once(client)["jobId"]
        assert client.get(f"/api/transcriptions/{transcription}").json()["status"] != "completed"
        assert transcriber.calls == []
        ai.let_finish(client)
        wait_for_ai_job(client, job_id, {"completed"})
        wait_for_status(client, transcription, {"completed"})


# --- cancellation and cleanup --------------------------------------------------------


def test_cancelling_a_running_job(settings):
    ai = FakeAiClient([SOAP_ANSWER], gated=True)
    with running_app(settings, ai_client=ai) as client:
        job_id = start(client, "/api/ai/soap", soap_payload())
        wait_for_ai_job(client, job_id, {"processing"})
        body = client.post(f"/api/ai/jobs/{job_id}/cancel").json()
        assert body["status"] == "cancelled"
        assert body["soapResult"] is None
        assert client.post(f"/api/ai/jobs/{job_id}/cancel").json()["status"] == "cancelled"  # idempotent
        # The model is free again for the next job.
        ai._gated = False
        next_id = start(client, "/api/ai/soap", soap_payload())
        wait_for_ai_job(client, next_id, {"completed"})


def test_cancelling_a_queued_job_never_calls_the_model(settings):
    ai = FakeAiClient([SOAP_ANSWER], gated=True)
    with running_app(settings, ai_client=ai) as client:
        first = start(client, "/api/ai/soap", soap_payload())
        second = start(client, "/api/ai/soap", soap_payload())
        wait_for_ai_job(client, first, {"processing"})
        assert client.post(f"/api/ai/jobs/{second}/cancel").json()["status"] == "cancelled"
        ai.let_finish(client)
        wait_for_ai_job(client, first, {"completed"})
    assert len(ai.calls) == 1


def test_finished_jobs_drop_their_input_and_can_be_deleted(settings):
    with running_app(settings, ai_client=FakeAiClient([SOAP_ANSWER])) as client:
        job_id = start(client, "/api/ai/soap", soap_payload())
        wait_for_ai_job(client, job_id, {"completed"})
        services = client.app.state.services
        assert services.ai_jobs.get(job_id).request is None  # transcript and note no longer held
        assert client.get(f"/api/ai/jobs/{job_id}").json()["soapResult"] is not None

        assert client.delete(f"/api/ai/jobs/{job_id}").status_code == 204
        assert client.get(f"/api/ai/jobs/{job_id}").status_code == 404  # the result is gone


def test_deleting_a_running_job_cancels_it(settings):
    ai = FakeAiClient([SOAP_ANSWER], gated=True)
    with running_app(settings, ai_client=ai) as client:
        job_id = start(client, "/api/ai/soap", soap_payload())
        wait_for_ai_job(client, job_id, {"processing"})
        assert client.delete(f"/api/ai/jobs/{job_id}").status_code == 204
        assert client.get(f"/api/ai/jobs/{job_id}").status_code == 404
        assert ai.active == 0


def test_shutdown_discards_jobs_and_unloads_the_model(settings):
    ai = FakeAiClient([SOAP_ANSWER], gated=True)
    with running_app(settings, ai_client=ai) as client:
        start(client, "/api/ai/soap", soap_payload())
        services = client.app.state.services
        before = ai.unloads
    assert services.ai_jobs._jobs == {}
    assert ai.unloads > before


# --- privacy ---------------------------------------------------------------------


def test_logs_never_contain_transcript_note_or_model_output(settings, caplog):
    caplog.set_level(logging.DEBUG)
    segs = [
        {"id": "seg-1", "speaker": "patient", "text": f"{MARKER} patient statement about the sore throat."},
        {"id": "seg-2", "speaker": "clinician", "text": "Any fever?"},
    ]
    note = {**NOTE, "plan": f"{MARKER} plan text"}
    leak = {**SOAP_ANSWER, "subjective": f"{MARKER} model output"}
    failures = [
        {"subjective": f"{MARKER} unusable"},
        RuntimeError(f"{MARKER} crash"),
        AiError("AI_TIMEOUT", "slow"),
    ]
    with running_app(settings, ai_client=FakeAiClient([leak])) as client:
        wait_for_ai_job(client, start(client, "/api/ai/soap", soap_payload(segs)), {"completed"})
        answer = review_answer(followUpQuestions=[item("Q?", f"{MARKER} patient statement")])
        client.app.state.services.ai_client.responses = [answer]
        wait_for_ai_job(
            client, start(client, "/api/ai/clinical-review", review_payload(note, segs)), {"completed"}
        )
        for failing in failures:
            client.app.state.services.ai_client.responses = [failing]
            wait_for_ai_job(client, start(client, "/api/ai/soap", soap_payload(segs)), {"error"})
    assert MARKER not in caplog.text
    assert "ai job=" in caplog.text  # something useful is logged: IDs, kinds and timings


def test_health_reports_the_ai_service(settings):
    with running_app(settings, ai_client=FakeAiClient(ready=False)) as client:
        body = client.get("/api/health").json()
    assert (body["ai"], body["aiReady"]) == ("fake-ai", False)
