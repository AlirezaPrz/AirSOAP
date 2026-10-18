"""Task: suggest who said each turn (clinician, patient or unknown) from the words alone.

This is not voice recognition: the model only reads the transcript. On the Pi benchmarks the
1.5B model mostly assumed speakers alternate, so this task uses the SOAP model (3B) and is run
only when the clinician asks. The answer maps each turn number to a speaker, so every label is
tied to its turn and the model never repeats, changes or reorders the words. Anything it is not
sure of stays unknown. The clinician confirms or corrects every label in review.
"""

from typing import Any

from ..base import AiTask, Message
from ..errors import InvalidOutput
from ..schemas import SpeakerRequest, SpeakerResult, SpeakerSegment, SpeakerSuggestion

SPEAKERS = ("clinician", "patient", "unknown")
BATCH = 25  # turns per request: short enough for a reliable answer, few enough requests for a long visit
CONTEXT_TURNS = 4  # the end of the previous batch, shown (not labelled) so a batch does not start blind

SYSTEM_PROMPT = """\
You label who is speaking in a transcript of a medical visit between one clinician and one patient. You only see the words, never the voices. The turns are numbered.

Answer with an object that maps every turn number to "clinician", "patient" or "unknown".

Decide from what each turn says, not from its position. Speakers do not always take turns: the same person often speaks twice in a row.
- clinician: asks about symptoms, history, medicines or allergies; examines; states findings, vitals or test results; names a diagnosis; prescribes; gives instructions or follow-up advice.
- patient: describes their own symptoms and history; answers the clinician's questions; asks about their own care or treatment.
- unknown: the words alone do not show who spoke (for example "Okay.", "Yes.", "Thank you.", "Mm-hmm."), or you are not sure. Do not guess to fill every turn.\
"""


def batches(request: SpeakerRequest) -> list[tuple[list[SpeakerSegment], list[SpeakerSegment]]]:
    """(turns to label, earlier turns shown for context) for each request."""
    segments = request.segments
    return [
        (segments[i : i + BATCH], segments[max(0, i - CONTEXT_TURNS) : i])
        for i in range(0, len(segments), BATCH)
    ]


def schema(count: int) -> dict[str, Any]:
    keys = [str(n) for n in range(1, count + 1)]
    return {
        "type": "object",
        "properties": {key: {"type": "string", "enum": list(SPEAKERS)} for key in keys},
        "required": keys,
        "additionalProperties": False,
    }


def build_messages(batch: tuple[list[SpeakerSegment], list[SpeakerSegment]]) -> list[Message]:
    turns, earlier = batch
    lines = []
    if earlier:
        lines.append("Earlier turns, for context only (do not label these):")
        lines += [f"- {segment.text}" for segment in earlier]
        lines.append("")
    lines.append(f"Label these {len(turns)} turns:")
    lines += [f"{n}. {segment.text}" for n, segment in enumerate(turns, 1)]
    return [Message("system", SYSTEM_PROMPT), Message("user", "\n".join(lines))]


def parse(
    batch: tuple[list[SpeakerSegment], list[SpeakerSegment]], data: dict[str, Any]
) -> list[SpeakerSuggestion]:
    turns, _earlier = batch
    keys = [str(n) for n in range(1, len(turns) + 1)]
    if sorted(data, key=lambda k: (len(k), k)) != keys:
        raise InvalidOutput("wrong_turns")
    if any(data[key] not in SPEAKERS for key in keys):
        raise InvalidOutput("bad_speaker")
    return [SpeakerSuggestion(id=segment.id, speaker=data[str(n)]) for n, segment in enumerate(turns, 1)]


def combine(parts: list[list[SpeakerSuggestion]]) -> SpeakerResult:
    return SpeakerResult(suggestions=[suggestion for part in parts for suggestion in part])


def _mock(batch: tuple[list[SpeakerSegment], list[SpeakerSegment]]) -> dict[str, Any]:
    """A deterministic stand-in for development (no model): questions look like the clinician."""
    turns, _earlier = batch
    answer = {}
    for n, segment in enumerate(turns, 1):
        words = segment.text.split()
        if len(words) <= 2:
            answer[str(n)] = "unknown"
        elif segment.text.rstrip().endswith("?"):
            answer[str(n)] = "clinician"
        else:
            answer[str(n)] = "patient"
    return answer


def task_for(batch: tuple[list[SpeakerSegment], list[SpeakerSegment]]) -> AiTask[Any]:
    """The schema depends on how many turns are in the batch."""
    count = len(batch[0])
    return AiTask(
        name="speakers",
        build_messages=build_messages,
        schema=schema(count),
        max_tokens=40 + 12 * count,
        mock=_mock,
    )
