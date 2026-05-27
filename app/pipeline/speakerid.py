"""Persistent speaker identification: match a meeting's diarized speakers
against named voice profiles so you "name a voice once" and it's recognized in
future meetings.

The math here is pure (cosine over d-vectors) and unit-tested. The actual
embedding extraction (Resemblyzer) lives in diarize.speaker_embeddings and runs
where torch is available (the Cornell node).
"""
from __future__ import annotations

import math


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def mean_embedding(vecs: list[list[float]]) -> list[float]:
    """Average several d-vectors (used to combine a speaker's segments and to
    update a profile's running mean)."""
    vecs = [v for v in vecs if v]
    if not vecs:
        return []
    n = len(vecs[0])
    out = [0.0] * n
    for v in vecs:
        for i, x in enumerate(v):
            out[i] += x
    return [x / len(vecs) for x in out]


def match(embedding: list[float], profiles: list[dict],
          threshold: float = 0.75) -> tuple[str, float] | None:
    """Return (name, score) of the best-matching profile above threshold, else
    None. `profiles` items are {"name": str, "embedding": list[float]}."""
    best_name, best_score = None, -1.0
    for p in profiles:
        s = cosine(embedding, p.get("embedding") or [])
        if s > best_score:
            best_score, best_name = s, p.get("name")
    if best_name is not None and best_score >= threshold:
        return best_name, best_score
    return None


def running_mean(old: list[float], n: int, new: list[float]) -> list[float]:
    """Incorporate a new sample into a profile's mean of n existing samples."""
    if not old:
        return list(new)
    if not new or len(new) != len(old):
        return old
    return [(o * n + x) / (n + 1) for o, x in zip(old, new)]
