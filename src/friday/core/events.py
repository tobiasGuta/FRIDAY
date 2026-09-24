"""Normalized signals flowing from a provider to FRIDAY's interface."""

from dataclasses import dataclass
from enum import StrEnum


class EventKind(StrEnum):
    STATE = "state"
    TRANSCRIPT = "transcript"
    AUDIO = "audio"
    TURN_COMPLETE = "turn_complete"
    INTERRUPTED = "interrupted"
    NOTICE = "notice"
    GROUNDING = "grounding"
    ERROR = "error"


class SessionState(StrEnum):
    NEW = "new"
    CONNECTING = "connecting"
    READY = "ready"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class SearchSource:
    title: str
    url: str


@dataclass(frozen=True, slots=True)
class VoiceEvent:
    kind: EventKind
    text: str | None = None
    speaker: str | None = None
    audio: bytes | None = None
    sample_rate: int | None = None
    state: SessionState | None = None
    sources: tuple[SearchSource, ...] = ()
    search_suggestions_html: str | None = None

    def __repr__(self) -> str:
        # Audio contents are deliberately not included in event repr/logs.
        audio_info = f"<{len(self.audio)} bytes>" if self.audio is not None else None
        return (
            f"VoiceEvent(kind={self.kind!r}, text={self.text!r}, "
            f"speaker={self.speaker!r}, audio={audio_info}, "
            f"sample_rate={self.sample_rate!r}, state={self.state!r}, "
            f"source_count={len(self.sources)}, "
            f"has_search_suggestions={self.search_suggestions_html is not None})"
        )
