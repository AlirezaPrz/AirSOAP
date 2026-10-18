"""Deciding where to cut the live recording: at the end of each stretch of speech.

Every stretch of speech is transcribed once, at full context, as soon as a pause ends it. A
stretch with no pause is cut at its quietest moment once it reaches ``max_block_ms``, so a long
monologue still appears. Silence is skipped without asking Whisper (which invents phrases such
as "Thank you." on silence and would waste CPU). This replaced a sliding window after Pi
benchmarks showed the sliding window falling behind (see scripts/bench_live.py).
"""

import math
from array import array
from dataclasses import dataclass

from ..audio import SAMPLE_RATE

BLOCK_MS = 100
SAMPLES_PER_BLOCK = SAMPLE_RATE * BLOCK_MS // 1000
MIN_BLOCK_MS = 1500
SKIP_SILENCE_AFTER_MS = 2000
LEAD_IN_MS = 300  # kept before speech and after a cut, so first and last words are not clipped
FORCED_CUT_SEARCH_MS = 3000


def rms(samples: array) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class SilenceDetector:
    """Loudness per 100 ms against an adaptive noise floor. No extra model is needed."""

    def __init__(self, minimum: float = 200.0, history: int = 600) -> None:
        self.minimum = minimum
        self._history = history
        self._levels: list[float] = []

    def observe(self, level: float) -> None:
        self._levels.append(level)
        if len(self._levels) > self._history:
            del self._levels[: len(self._levels) - self._history]

    def threshold(self) -> float:
        if len(self._levels) < 10:
            return self.minimum
        ordered = sorted(self._levels)
        floor = ordered[len(ordered) // 10]
        typical = ordered[len(ordered) // 2]
        # Never above half the typical level: if someone talks from the very first second there is
        # no quiet to learn the noise floor from, and speech must not be mistaken for background.
        return max(self.minimum, min(3.0 * floor, 0.5 * typical))

    def is_speech(self, level: float) -> bool:
        return level > self.threshold()


@dataclass(frozen=True)
class Block:
    """A stretch of audio to transcribe, in milliseconds from the start of the recording."""

    start_ms: int
    end_ms: int
    forced: bool = False


class BlockPlanner:
    """Fed the recording 100 ms at a time; says when a stretch of speech is ready to transcribe."""

    def __init__(self, *, pause_ms: int, max_block_ms: int) -> None:
        self.pause_blocks = max(1, pause_ms // BLOCK_MS)
        self.max_blocks = max(1, max_block_ms // BLOCK_MS)
        self.silence = SilenceDetector()
        self.cut_ms = 0  # everything before this has been transcribed or skipped
        self._levels: list[float] = []  # one per 100 ms block from cut_ms on

    @property
    def fed_ms(self) -> int:
        return self.cut_ms + len(self._levels) * BLOCK_MS

    @property
    def waiting_ms(self) -> int:
        """Audio received but not yet transcribed or skipped."""
        return len(self._levels) * BLOCK_MS

    def feed(self, samples: array) -> None:
        """Whole 100 ms blocks only; the caller keeps any remainder for next time."""
        for i in range(0, len(samples) - SAMPLES_PER_BLOCK + 1, SAMPLES_PER_BLOCK):
            level = rms(samples[i : i + SAMPLES_PER_BLOCK])
            self.silence.observe(level)
            self._levels.append(level)

    def _speech(self) -> list[bool]:
        return [self.silence.is_speech(level) for level in self._levels]

    def _advance(self, blocks: int) -> None:
        self.cut_ms += blocks * BLOCK_MS
        del self._levels[:blocks]

    def next_block(self) -> Block | None:
        """The next stretch to transcribe, if one is ready. Skips silence along the way."""
        speech = self._speech()
        if not any(speech):
            keep = LEAD_IN_MS // BLOCK_MS
            if len(speech) * BLOCK_MS >= SKIP_SILENCE_AFTER_MS:
                self._advance(len(speech) - keep)
            return None
        quiet = 0
        for spoke in reversed(speech):
            if spoke:
                break
            quiet += 1
        if quiet >= self.pause_blocks and len(speech) * BLOCK_MS >= MIN_BLOCK_MS:
            end_blocks = min(len(speech), len(speech) - quiet + LEAD_IN_MS // BLOCK_MS)
            return self._take(end_blocks, forced=False)
        if len(speech) >= self.max_blocks:
            return self._take(self._quietest_block(), forced=True)
        return None

    def _quietest_block(self) -> int:
        search = FORCED_CUT_SEARCH_MS // BLOCK_MS
        start = max(1, len(self._levels) - search)
        index = min(range(start, len(self._levels)), key=lambda i: self._levels[i])
        return index if index > 0 else len(self._levels)

    def _take(self, blocks: int, *, forced: bool) -> Block:
        block = Block(self.cut_ms, self.cut_ms + blocks * BLOCK_MS, forced)
        self._advance(blocks)
        return block

    def remainder(self, end_ms: int) -> Block | None:
        """What is left when the recording stops, if anyone spoke in it."""
        if end_ms <= self.cut_ms or not any(self._speech()):
            return None
        block = Block(self.cut_ms, end_ms)
        self._advance(len(self._levels))
        self.cut_ms = end_ms
        return block
