"""The Pi benchmark's logic, checked on the Mac with a fake whisper-server (no model, no Ollama)."""

import sys
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import bench_live as bench  # noqa: E402
from bench_dialogue import DIALOGUE  # noqa: E402

RATE = bench.RATE


def tone(ms: int, level: int = 6000) -> array:
    return array("h", [level if (i // 8) % 2 else -level for i in range(ms * RATE // 1000)])


def quiet(ms: int) -> array:
    return array("h", [0] * (ms * RATE // 1000))


def test_word_error_rate_counts_substitutions_insertions_and_deletions() -> None:
    assert bench.word_error_rate("the throat hurts", "the throat hurts") == 0.0
    assert bench.word_error_rate("the throat hurts", "the throat") == 1 / 3
    assert bench.word_error_rate("the throat hurts", "a throat hurts badly") == 2 / 3
    assert bench.word_error_rate("Hello, there!", "hello there") == 0.0


def test_audio_round_trips_through_wav() -> None:
    samples = tone(500)
    wav = bench.Audio.to_wav(samples)
    path = Path(__file__).parent / "_bench_tmp.wav"
    try:
        path.write_bytes(wav)
        loaded = bench.Audio.load(path)
    finally:
        path.unlink(missing_ok=True)
    assert loaded.samples == samples
    assert loaded.duration_ms == 500
    assert len(loaded.slice(100, 300)) == 200 * RATE // 1000


def test_silence_detector_finds_the_pause_after_speech() -> None:
    detector = bench.SilenceDetector()
    assert detector.is_silent(quiet(1000))
    assert not detector.is_silent(tone(300) + quiet(700))
    assert bench.SilenceDetector().silent_lead_ms(quiet(900) + tone(300)) == 900


def test_matched_audio_context_covers_the_window_and_stops_at_30_seconds() -> None:
    assert bench.matched_audio_ctx(10) == 564
    assert bench.matched_audio_ctx(30) == 0


def test_whisper_response_is_parsed_without_non_speech_tags() -> None:
    segments = bench.parse_segments(
        {
            "segments": [
                {"start": 0.0, "end": 1.5, "text": " Hello there. [BLANK_AUDIO]"},
                {"text": " (silence) "},
            ]
        }
    )
    assert segments == [bench.Segment(0, 1500, "Hello there.")]


def test_multipart_body_carries_fields_and_audio() -> None:
    body, content_type = bench.multipart({"language": "en"}, b"RIFFdata")
    boundary = content_type.split("boundary=")[1]
    assert body.startswith(f"--{boundary}".encode())
    assert b'name="language"\r\n\r\nen' in body
    assert b"RIFFdata" in body
    assert body.endswith(f"--{boundary}--\r\n".encode())


def never_quiet(_at: int) -> int:
    return 0


def test_text_is_confirmed_only_when_two_passes_agree() -> None:
    state = bench.LiveState()
    first = [bench.Segment(0, 2000, "Good morning."), bench.Segment(2000, 3500, "What brings")]
    assert state.update(first, 5000, False, never_quiet) == []
    second = [bench.Segment(0, 2000, "Good morning."), bench.Segment(2000, 4200, "What brings you in?")]
    confirmed = state.update(second, 6000, False, never_quiet)
    assert [s.text for s in confirmed] == ["Good morning."]
    assert state.committed_until_ms == 2000
    assert [s.text for s in state.previous] == ["What brings you in?"]


def test_a_revision_replaces_provisional_text_instead_of_duplicating_it() -> None:
    state = bench.LiveState()
    state.update([bench.Segment(0, 1500, "I have a sore")], 4000, False, never_quiet)
    state.update([bench.Segment(0, 2500, "I have a sore throat.")], 6000, False, never_quiet)
    state.update([bench.Segment(0, 2500, "I have a sore throat.")], 8000, False, never_quiet)
    assert state.text == "I have a sore throat."


def test_a_pause_confirms_a_short_utterance_at_once() -> None:
    state = bench.LiveState()
    confirmed = state.update([bench.Segment(0, 600, "Okay.")], 3000, False, lambda _at: 2000)
    assert [s.text for s in confirmed] == ["Okay."]


def test_a_full_window_forces_progress_and_counts_it() -> None:
    state = bench.LiveState()
    confirmed = state.update([bench.Segment(0, 9000, "Fast talking")], 20000, True, never_quiet)
    assert [s.text for s in confirmed] == ["Fast talking"]
    assert state.low_agreement == 1


def test_speaker_labels_are_validated_strictly() -> None:
    ids = ["t0", "t1", "t2"]
    good = {
        "labels": [
            {"id": "t0", "speaker": "clinician", "confidence": "high"},
            {"id": "t1", "speaker": "patient", "confidence": "medium"},
            {"id": "t2", "speaker": "patient", "confidence": "low"},
        ]
    }
    assert bench.validate_labels(good, ids) == ["clinician", "patient", "unknown"]
    reordered = {"labels": [good["labels"][1], good["labels"][0], good["labels"][2]]}
    missing = {"labels": good["labels"][:2]}
    extra = {"labels": [*good["labels"], {"id": "t3", "speaker": "patient", "confidence": "high"}]}
    invented = {"labels": [{**good["labels"][0], "speaker": "nurse"}, *good["labels"][1:]]}
    for bad in (reordered, missing, extra, invented, {"labels": "x"}, None):
        assert bench.validate_labels(bad, ids) is None


def test_label_scoring_separates_unknown_from_wrong() -> None:
    score = bench.score_labels(["clinician", "unknown", "clinician"], ["clinician", "patient", "patient"])
    assert score == {"turns": 3, "labelled": 2, "unknown": 1, "accuracy_of_labelled": 0.5, "wrong": 1}


def test_the_dialogue_has_both_roles_and_ambiguous_turns() -> None:
    speakers = {speaker for speaker, _ in DIALOGUE}
    assert speakers == {"clinician", "patient"}
    assert any(text in {"Okay.", "Thank you."} for _, text in DIALOGUE)


class TimelineAudio(bench.Audio):
    """Remembers the last window sliced, so the fake server knows where it is in the recording."""

    last = (0, 0)

    def slice(self, start_ms: int, end_ms: int) -> array:
        self.last = (start_ms, end_ms)
        return super().slice(start_ms, end_ms)


class TimelineServer:
    """Answers like whisper-server: the spoken segments inside the window, relative to its start.

    A segment still being spoken when the window ends comes back cut short, as Whisper's partial
    hypothesis would.
    """

    def __init__(self, audio: TimelineAudio, spoken: list[tuple[int, int, str]]) -> None:
        self.audio, self.spoken, self.requests = audio, spoken, 0

    def transcribe(self, samples: array, audio_ctx: int = 0):
        self.requests += 1
        start, end = self.audio.last
        segments = []
        for s, e, text in self.spoken:
            if s >= start and s < end:
                partial = e > end
                words = text.split()
                said = " ".join(words[: max(1, len(words) // 2)]) if partial else text
                segments.append(bench.Segment(s - start, min(e, end) - start, said))
        return 0.01, segments


def test_live_simulation_confirms_each_utterance_once_and_keeps_up() -> None:
    audio = TimelineAudio(tone(1200) + quiet(1200) + tone(1500) + quiet(1300))
    spoken = [(0, 1200, "Good morning, what brings you in?"), (2400, 3900, "I have a sore throat today.")]
    server = TimelineServer(audio, spoken)
    result, state = bench.live_simulation(server, audio, step_s=0.4, reduced_ctx=False)
    assert server.requests >= 2
    assert result.behind_events == 0
    assert [s.text for s in state.committed] == [text for _s, _e, text in spoken]  # no duplicates, in order
    assert result.final_delay_s is not None and result.final_delay_s["max"] < 3.0
    assert result.inference_s is not None and result.inference_s["max"] < 0.1
    assert result.skipped_silent >= 1  # the pauses were not sent to Whisper


def test_recommendations_follow_the_numbers() -> None:
    keeps_up = {
        "step_s": 3.0,
        "audio_ctx": "matched",
        "inference_s": {"p90": 1.5},
        "behind_events": 0,
        "final_delay_s": {"median": 4.2},
    }
    advice = bench.recommend({"live": [keeps_up]})
    assert "keeps up" in advice[0] and "3 s step" in advice[0]
    falls_behind = {**keeps_up, "inference_s": {"p90": 3.5}, "behind_events": 4}
    assert "fell behind" in bench.recommend({"live": [falls_behind]})[0]
    concurrent = {**keeps_up, "final_delay_s": {"median": 9.0}, "resources": {"mem_available_min_mb": 150}}
    advice = bench.recommend({"live": [keeps_up], "concurrent": concurrent})
    assert "after the recording stops" in advice[1]
