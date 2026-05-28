"""Plain dataclasses shared across capture, pipeline, storage, and the API."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Platform = Literal["meet", "zoom", "other"]
Source = Literal["live", "batch"]


@dataclass
class Segment:
    """One contiguous span of transcribed speech by one speaker."""

    start: float  # seconds from meeting start
    end: float
    text: str
    speaker: str = "Unknown"  # "Me", "Speaker 1", a resolved name, ...
    source: Source = "live"
    id: int | None = None
    meeting_id: str | None = None
    confidence: float | None = None  # 0..1, from faster-whisper avg_logprob

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "text": self.text,
            "speaker": self.speaker,
            "source": self.source,
        }
        if self.confidence is not None:
            d["confidence"] = round(self.confidence, 3)
        return d


@dataclass
class ActionItem:
    text: str
    owner: str | None = None
    due: str | None = None
    done: bool = False
    id: int | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "owner": self.owner,
            "due": self.due,
            "done": self.done,
        }


@dataclass
class Summary:
    overview: str = ""
    key_points: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "overview": self.overview,
            "key_points": self.key_points,
            "decisions": self.decisions,
        }


@dataclass
class Meeting:
    id: str
    title: str = "Untitled meeting"
    platform: Platform = "other"
    started_at: float = 0.0  # epoch seconds
    ended_at: float | None = None
    status: str = "recording"  # recording | processing | done | error
    language: str | None = None
    duration_sec: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "platform": self.platform,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status,
            "language": self.language,
            "duration_sec": self.duration_sec,
        }
