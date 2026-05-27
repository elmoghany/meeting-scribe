"""Pure transcript-assembly logic: combine ASR segments with diarization turns
into clean, speaker-labeled segments. No ML imports → fully unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import Segment


@dataclass
class Turn:
    """A diarization turn: who spoke when (speaker is a raw label like 'SPEAKER_00')."""

    start: float
    end: float
    speaker: str


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def assign_speakers(asr: list[Segment], turns: list[Turn]) -> list[Segment]:
    """Label each ASR segment with the diarization speaker it overlaps most.

    Segments with no overlap keep their existing speaker label.
    """
    if not turns:
        return asr
    out: list[Segment] = []
    for seg in asr:
        best_label, best_ov = seg.speaker, 0.0
        for t in turns:
            ov = _overlap(seg.start, seg.end, t.start, t.end)
            if ov > best_ov:
                best_ov, best_label = ov, t.speaker
        out.append(Segment(start=seg.start, end=seg.end, text=seg.text,
                           speaker=best_label, source=seg.source))
    return out


def apply_me_prior(segments: list[Segment], mic_turns: list[Turn],
                   min_overlap: float = 0.5) -> list[Segment]:
    """Relabel segments to "Me" when they overlap the user's own mic activity.

    The mic stream is unambiguously the local user, so this is a free, accurate
    anchor that fixes who "Me" is among the diarized speakers.
    """
    if not mic_turns:
        return segments
    out: list[Segment] = []
    for seg in segments:
        dur = max(1e-6, seg.end - seg.start)
        mic_ov = sum(_overlap(seg.start, seg.end, t.start, t.end) for t in mic_turns)
        speaker = "Me" if (mic_ov / dur) >= min_overlap else seg.speaker
        out.append(Segment(start=seg.start, end=seg.end, text=seg.text,
                           speaker=speaker, source=seg.source))
    return out


def merge_adjacent(segments: list[Segment], max_gap: float = 1.0) -> list[Segment]:
    """Merge consecutive same-speaker segments separated by <= max_gap seconds."""
    if not segments:
        return []
    ordered = sorted(segments, key=lambda s: s.start)
    merged: list[Segment] = [
        Segment(start=ordered[0].start, end=ordered[0].end, text=ordered[0].text.strip(),
                speaker=ordered[0].speaker, source=ordered[0].source)
    ]
    for seg in ordered[1:]:
        last = merged[-1]
        if seg.speaker == last.speaker and (seg.start - last.end) <= max_gap:
            last.end = max(last.end, seg.end)
            last.text = (last.text + " " + seg.text.strip()).strip()
        else:
            merged.append(Segment(start=seg.start, end=seg.end, text=seg.text.strip(),
                                  speaker=seg.speaker, source=seg.source))
    return merged


def renumber_speakers(segments: list[Segment], keep: tuple[str, ...] = ("Me",),
                      ) -> list[Segment]:
    """Relabel raw diarizer labels (e.g. pyannote 'SPEAKER_00') to friendly
    'Speaker 1', 'Speaker 2', … by first appearance. Labels in `keep` (e.g. the
    mic-anchored 'Me') are left untouched. Makes pyannote output match the
    key-free backend's labeling."""
    order: dict[str, str] = {}
    for s in segments:
        if s.speaker in keep:
            continue
        if s.speaker not in order:
            order[s.speaker] = f"Speaker {len(order) + 1}"
    for s in segments:
        if s.speaker not in keep:
            s.speaker = order[s.speaker]
    return segments


def rename_speakers(segments: list[Segment], mapping: dict[str, str]) -> list[Segment]:
    """Apply a {raw_label: display_name} rename, e.g. {'SPEAKER_00': 'Sam'}."""
    for s in segments:
        s.speaker = mapping.get(s.speaker, s.speaker)
    return segments


def to_transcript(segments: list[Segment], with_timestamps: bool = True) -> str:
    """Render labeled segments as a readable transcript (also used as LLM input)."""
    lines = []
    for s in sorted(segments, key=lambda x: x.start):
        if with_timestamps:
            mm, ss = divmod(int(s.start), 60)
            lines.append(f"[{mm:02d}:{ss:02d}] {s.speaker}: {s.text.strip()}")
        else:
            lines.append(f"{s.speaker}: {s.text.strip()}")
    return "\n".join(lines)
