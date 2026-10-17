"""End-to-end API behaviour with fake recorder and transcriber."""

from helpers import (
    FakeRecorder,
    FakeTranscriber,
    audio_files,
    record_once,
    running_app,
    wait_for_status,
)

from airsoap.transcription import TranscriptionError


def test_public_health_reveals_nothing_but_liveness(settings):
    with running_app(settings, login_as=None) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_reports_components_and_state(settings):
    with running_app(
        settings, recorder=FakeRecorder(), transcriber=FakeTranscriber(reachable=False)
    ) as client:
        response = client.get("/api/system/status")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "recorder": "fake-recorder",
        "recorderAvailable": True,
        "transcriber": "fake-transcriber",
        "transcriberReachable": False,
        "transcriberLoaded": False,
        "ai": "mock",
        "aiReady": True,
        "recording": "idle",
    }


def test_health_defaults_to_mock_services(settings):
    with running_app(settings) as client:
        body = client.get("/api/system/status").json()
    assert (body["recorder"], body["transcriber"]) == ("mock", "mock")


def test_record_and_transcribe_whole_recording(settings):
    transcriber = FakeTranscriber()
    with running_app(settings, recorder=FakeRecorder(duration_ms=4200), transcriber=transcriber) as client:
        started = client.post("/api/recording/start").json()
        assert client.get("/api/system/status").json()["recording"] == "recording"

        stopped = client.post("/api/recording/stop", json={"recordingId": started["recordingId"]}).json()
        assert stopped["recordingId"] == started["recordingId"]
        assert stopped["durationMs"] == 4200

        body = wait_for_status(client, stopped["jobId"], {"completed"})
        assert client.get("/api/system/status").json()["recording"] == "idle"

    # One request for the whole file, not chunks.
    assert len(transcriber.calls) == 1
    assert transcriber.calls[0][1] == 4200
    assert body["progress"] == 100
    assert body["audioDurationMs"] == 4200
    assert body["error"] is None
    assert body["transcript"]["text"] == "Transcript 1."
    assert body["transcript"]["segments"] == [
        {"id": "seg-1", "startMs": 0, "endMs": 4200, "text": "Transcript 1."}
    ]


def test_duplicate_start_is_rejected(settings):
    with running_app(settings, recorder=FakeRecorder(), transcriber=FakeTranscriber()) as client:
        assert client.post("/api/recording/start").status_code == 200
        second = client.post("/api/recording/start")
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "RECORDING_IN_PROGRESS"


def test_stop_without_active_recording(settings):
    with running_app(settings, recorder=FakeRecorder(), transcriber=FakeTranscriber()) as client:
        response = client.post("/api/recording/stop", json={"recordingId": "0" * 32})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NO_ACTIVE_RECORDING"


def test_stop_with_wrong_recording_id_keeps_recording(settings):
    with running_app(settings, recorder=FakeRecorder(), transcriber=FakeTranscriber()) as client:
        started = client.post("/api/recording/start").json()
        wrong = client.post("/api/recording/stop", json={"recordingId": "f" * 32})
        assert wrong.status_code == 409
        assert client.get("/api/system/status").json()["recording"] == "recording"
        ok = client.post("/api/recording/stop", json={"recordingId": started["recordingId"]})
    assert ok.status_code == 200


def test_malformed_recording_id_is_rejected(settings):
    with running_app(settings, recorder=FakeRecorder(), transcriber=FakeTranscriber()) as client:
        response = client.post("/api/recording/stop", json={"recordingId": "../../etc/passwd"})
    assert response.status_code == 422


def test_failed_start_leaves_no_file_and_allows_retry(settings):
    recorder = FakeRecorder(fail_on_start=True)
    with running_app(settings, recorder=recorder, transcriber=FakeTranscriber()) as client:
        response = client.post("/api/recording/start")
        assert response.status_code == 503
        assert response.json()["error"] == {
            "code": "RECORDING_FAILED",
            "message": "The microphone could not be opened.",
            "retryable": False,
        }
        assert client.get("/api/system/status").json()["recording"] == "idle"
        assert audio_files(settings) == []

        recorder.fail_on_start = False
        assert client.post("/api/recording/start").status_code == 200


def test_failed_stop_deletes_audio_and_resets(settings):
    with running_app(
        settings, recorder=FakeRecorder(fail_on_stop=True), transcriber=FakeTranscriber()
    ) as client:
        started = client.post("/api/recording/start").json()
        assert len(audio_files(settings)) == 1
        response = client.post("/api/recording/stop", json={"recordingId": started["recordingId"]})
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "RECORDING_FAILED"
        assert client.get("/api/system/status").json()["recording"] == "idle"
        assert audio_files(settings) == []


def test_empty_recording_is_rejected_and_deleted(settings):
    transcriber = FakeTranscriber()
    with running_app(settings, recorder=FakeRecorder(duration_ms=0), transcriber=transcriber) as client:
        started = client.post("/api/recording/start").json()
        response = client.post("/api/recording/stop", json={"recordingId": started["recordingId"]})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "EMPTY_RECORDING"
        assert audio_files(settings) == []
    assert transcriber.calls == []


def test_cancel_recording_deletes_audio_without_transcribing(settings):
    recorder, transcriber = FakeRecorder(), FakeTranscriber()
    with running_app(settings, recorder=recorder, transcriber=transcriber) as client:
        started = client.post("/api/recording/start").json()
        response = client.post("/api/recording/cancel", json={"recordingId": started["recordingId"]})
        assert response.status_code == 200
        assert response.json() == {"recordingId": started["recordingId"], "status": "cancelled"}
        assert client.get("/api/system/status").json()["recording"] == "idle"
        assert audio_files(settings) == []
    assert recorder.processes[0].aborted
    assert transcriber.calls == []


def test_transcription_failure_is_reported_safely(settings):
    error = TranscriptionError("TRANSCRIBER_UNAVAILABLE", "The local transcription service is not running.")
    with running_app(settings, recorder=FakeRecorder(), transcriber=FakeTranscriber(error=error)) as client:
        job_id = record_once(client)["jobId"]
        body = wait_for_status(client, job_id, {"error"})
        assert audio_files(settings) == []
    assert body["transcript"] is None
    assert body["progress"] is None
    assert body["error"] == {
        "code": "TRANSCRIBER_UNAVAILABLE",
        "message": "The local transcription service is not running.",
        "retryable": False,
    }


def test_unexpected_failure_hides_details(settings):
    secret = RuntimeError("/home/ai-innovate/patient-jordan.wav")
    with running_app(settings, recorder=FakeRecorder(), transcriber=FakeTranscriber(error=secret)) as client:
        body = wait_for_status(client, record_once(client)["jobId"], {"error"})
    assert body["error"]["code"] == "TRANSCRIPTION_FAILED"
    assert "patient" not in body["error"]["message"]
    assert "/" not in body["error"]["message"]


def test_status_progresses_from_processing_to_completed(settings):
    transcriber = FakeTranscriber(gated=True)
    with running_app(settings, recorder=FakeRecorder(), transcriber=transcriber) as client:
        job_id = record_once(client)["jobId"]
        processing = wait_for_status(client, job_id, {"processing"})
        assert processing["progress"] is None
        assert processing["transcript"] is None
        transcriber.let_finish(client)
        completed = wait_for_status(client, job_id, {"completed"})
    assert completed["progress"] == 100


def test_jobs_run_one_at_a_time_in_order(settings):
    transcriber = FakeTranscriber(gated=True)
    with running_app(settings, recorder=FakeRecorder(), transcriber=transcriber) as client:
        first = record_once(client)["jobId"]
        second = record_once(client)["jobId"]
        wait_for_status(client, first, {"processing"})
        queued = client.get(f"/api/transcriptions/{second}").json()
        assert queued["status"] == "queued"
        assert queued["progress"] == 0
        transcriber.let_finish(client)
        first_body = wait_for_status(client, first, {"completed"})
        second_body = wait_for_status(client, second, {"completed"})
    assert transcriber.max_active == 1
    assert first_body["transcript"]["text"] == "Transcript 1."
    assert second_body["transcript"]["text"] == "Transcript 2."


def test_cancel_processing_job(settings):
    transcriber = FakeTranscriber(gated=True)
    with running_app(settings, recorder=FakeRecorder(), transcriber=transcriber) as client:
        job_id = record_once(client)["jobId"]
        wait_for_status(client, job_id, {"processing"})
        response = client.post(f"/api/transcriptions/{job_id}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        assert audio_files(settings) == []
        # Cancelling again is harmless.
        assert client.post(f"/api/transcriptions/{job_id}/cancel").json()["status"] == "cancelled"


def test_cancel_queued_job_skips_transcription(settings):
    transcriber = FakeTranscriber(gated=True)
    with running_app(settings, recorder=FakeRecorder(), transcriber=transcriber) as client:
        first = record_once(client)["jobId"]
        second = record_once(client)["jobId"]
        wait_for_status(client, first, {"processing"})
        assert client.post(f"/api/transcriptions/{second}/cancel").json()["status"] == "cancelled"
        assert len(audio_files(settings)) == 1  # only the first job's audio remains
        transcriber.let_finish(client)
        wait_for_status(client, first, {"completed"})
    assert len(transcriber.calls) == 1


def test_unknown_job_and_invalid_job_id(settings):
    with running_app(settings, recorder=FakeRecorder(), transcriber=FakeTranscriber()) as client:
        missing = client.get(f"/api/transcriptions/{'a' * 32}")
        invalid = client.get("/api/transcriptions/..%2F..%2Fetc")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "JOB_NOT_FOUND"
    assert invalid.status_code in {404, 422}


def test_queue_limit(settings):
    limited = settings.model_copy(update={"max_pending_jobs": 1})
    transcriber = FakeTranscriber(gated=True)
    with running_app(limited, recorder=FakeRecorder(), transcriber=transcriber) as client:
        record_once(client)
        started = client.post("/api/recording/start").json()
        response = client.post("/api/recording/stop", json={"recordingId": started["recordingId"]})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "QUEUE_FULL"
        assert len(audio_files(settings)) == 1  # the rejected recording was deleted
        transcriber.let_finish(client)


def test_audio_is_deleted_after_success_and_on_shutdown(settings):
    transcriber = FakeTranscriber()
    with running_app(settings, recorder=FakeRecorder(), transcriber=transcriber) as client:
        wait_for_status(client, record_once(client)["jobId"], {"completed"})
        assert audio_files(settings) == []
        client.post("/api/recording/start")
        assert len(audio_files(settings)) == 1
    # Leaving the context shuts the app down mid-recording.
    assert audio_files(settings) == []
    assert transcriber.files_existed == [True]


def test_leftover_audio_is_swept_on_startup(settings):
    settings.work_dir.mkdir(parents=True)
    stale = settings.work_dir / f"rec-{'b' * 32}.wav"
    unrelated = settings.work_dir / "notes.txt"
    stale.write_bytes(b"old audio")
    unrelated.write_text("keep me")
    with running_app(settings, recorder=FakeRecorder(), transcriber=FakeTranscriber()):
        assert not stale.exists()
        assert unrelated.exists()
