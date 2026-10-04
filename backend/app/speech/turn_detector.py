"""Turn detection: decides when an interviewer utterance is complete.

Inputs are streaming STT events (partials, final segments) and VAD events
(speech start/stop). An utterance is finalised only after a configurable
silence window following the last final segment, so short pauses mid-question
do not trigger answer generation. Self-corrections ("Can you explain your -
actually, let's talk about...") are trimmed to the completed question.

The detector is pure and clock-injected so it is fully unit-testable; the
WebSocket handler drives `tick()` on a timer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class SttState(StrEnum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    SPEAKING_DETECTED = "SPEAKING_DETECTED"
    TRANSCRIBING = "TRANSCRIBING"
    QUESTION_READY = "QUESTION_READY"
    PROCESSING = "PROCESSING"
    ANSWER_READY = "ANSWER_READY"
    ERROR = "ERROR"


# Restart markers: the speaker abandons what they were saying and starts over.
_RESTART = re.compile(
    r"(?:^|\s*[,.;:!?—–-]+\s*|\s+-+\s*)(?:actually|sorry|wait|no wait|scratch that|"
    r"let me rephrase(?: that)?|let me put it (?:another|a different) way|i mean|never ?mind|on second thought)"
    r"\b[\s,.;:!—–-]*",
    re.I,
)
_DANGLING = re.compile(r"\b(the|a|an|your|my|of|to|and|or|but|with|about|for|in|on|is|are|was|how|what|why)\s*[—–-]?\s*$", re.I)


def clean_utterance(text: str) -> str:
    """Resolve self-corrections and strip disfluencies."""
    t = re.sub(r"\s+", " ", text).strip()
    t = re.sub(r"\b(um+|uh+|erm+|hmm+)\b[,.]?\s*", "", t, flags=re.I).strip()
    matches = list(_RESTART.finditer(t))
    if matches:
        last = matches[-1]
        before = t[: last.start()].strip()
        after = t[last.end():].strip()
        # Only treat it as a restart if something substantial follows and the
        # earlier part looks abandoned (dash, dangling word) or is itself a question start.
        abandoned = bool(re.search(r"[—–-]\s*$", t[: last.end()].rstrip(" ,.;:")) or _DANGLING.search(before)
                         or re.search(r"[—–]", t[last.start(): last.end()]))
        if len(after.split()) >= 3 and (abandoned or not before.endswith("?")):
            t = after[0].upper() + after[1:] if after else after
    t = re.sub(r"\s*[—–]\s*$", "", t)
    return t.strip()


@dataclass
class DetectorConfig:
    endpoint_ms: int = 1200          # silence after the last final segment
    question_endpoint_ms: int = 700  # faster when the text clearly ends with a question mark
    min_words: int = 2
    max_utterance_ms: int = 45_000   # force-finalise very long monologues


@dataclass
class TurnDetector:
    config: DetectorConfig = field(default_factory=DetectorConfig)
    state: SttState = SttState.IDLE
    segments: list[str] = field(default_factory=list)
    partial: str = ""
    last_final_at: float | None = None
    last_voice_at: float | None = None
    utterance_started_at: float | None = None
    speaking: bool = False
    stt_latency_ms: list[float] = field(default_factory=list)

    def start(self) -> None:
        self.state = SttState.LISTENING

    def pause(self) -> None:
        self.state = SttState.IDLE
        self.reset_buffer()

    def reset_buffer(self) -> None:
        self.segments.clear()
        self.partial = ""
        self.last_final_at = None
        self.utterance_started_at = None

    @property
    def buffer_text(self) -> str:
        return " ".join([*self.segments, self.partial]).strip()

    def on_vad(self, speaking: bool, now: float) -> None:
        if self.state == SttState.IDLE:
            return
        self.speaking = speaking
        if speaking:
            self.last_voice_at = now
            if self.utterance_started_at is None:
                self.utterance_started_at = now
            if self.state in (SttState.LISTENING, SttState.ANSWER_READY, SttState.PROCESSING):
                self.state = SttState.SPEAKING_DETECTED
        else:
            self.last_voice_at = now

    def on_partial(self, text: str, now: float) -> None:
        if self.state == SttState.IDLE or not text.strip():
            return
        self.partial = text.strip()
        self.last_voice_at = now
        if self.utterance_started_at is None:
            self.utterance_started_at = now
        self.state = SttState.TRANSCRIBING

    def on_final(self, text: str, now: float, stt_ms: float | None = None) -> None:
        if self.state == SttState.IDLE:
            return
        text = text.strip()
        self.partial = ""
        if text:
            self.segments.append(text)
            if self.utterance_started_at is None:
                self.utterance_started_at = now
        self.last_final_at = now
        self.last_voice_at = now
        if stt_ms is not None:
            self.stt_latency_ms.append(stt_ms)
        self.state = SttState.TRANSCRIBING

    def tick(self, now: float) -> str | None:
        """Return a finalised utterance when the endpoint condition is met."""
        if self.state == SttState.IDLE or not self.segments:
            return None
        if self.speaking or self.partial:
            # still talking: only force-finalise extremely long turns
            if self.utterance_started_at and (now - self.utterance_started_at) * 1000 > self.config.max_utterance_ms and self.segments:
                return self._finalise()
            return None
        assert self.last_final_at is not None
        silence_ms = (now - max(self.last_final_at, self.last_voice_at or 0)) * 1000
        text = " ".join(self.segments)
        window = self.config.question_endpoint_ms if text.rstrip().endswith("?") else self.config.endpoint_ms
        if silence_ms >= window:
            return self._finalise()
        return None

    def flush(self) -> str | None:
        """Finalise whatever is buffered (e.g. the user pressed 'answer now')."""
        if not self.segments and not self.partial:
            return None
        if self.partial:
            self.segments.append(self.partial)
            self.partial = ""
        return self._finalise()

    def _finalise(self) -> str | None:
        text = clean_utterance(" ".join(self.segments))
        self.reset_buffer()
        if len(text.split()) < self.config.min_words:
            self.state = SttState.LISTENING
            return None
        self.state = SttState.QUESTION_READY
        return text
