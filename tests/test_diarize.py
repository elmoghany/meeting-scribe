"""Diarization clustering logic — tested deterministically with synthetic
embeddings (no audio / torch / resemblyzer needed)."""
import numpy as np
import pytest

pytest.importorskip("sklearn")

from app.pipeline.diarize import labels_from_embeddings  # noqa: E402


def _v(*x):
    a = np.array(x, dtype=float)
    return a / np.linalg.norm(a)


def test_two_speakers_alternating():
    A, B = _v(1, 0, 0), _v(0, 1, 0)
    labels = labels_from_embeddings([A, B, A, B], [0, 1, 2, 3], 4, distance_threshold=0.5)
    assert labels[0] == labels[2] and labels[1] == labels[3]
    assert labels[0] == "Speaker 1" and labels[1] == "Speaker 2"  # first-appearance order


def test_three_speakers():
    A, B, C = _v(1, 0, 0), _v(0, 1, 0), _v(0, 0, 1)
    labels = labels_from_embeddings([A, B, C, A], [0, 1, 2, 3], 4, distance_threshold=0.5)
    assert len(set(labels)) == 3
    assert labels[0] == labels[3] == "Speaker 1"


def test_single_speaker():
    A = _v(1, 0, 0)
    labels = labels_from_embeddings([A, A, A], [0, 1, 2], 3, distance_threshold=0.5)
    assert set(labels) == {"Speaker 1"}


def test_short_segment_inherits_previous_label():
    A, B = _v(1, 0, 0), _v(0, 1, 0)
    # segment index 1 has no embedding (too short) → should inherit seg 0's label
    labels = labels_from_embeddings([A, B], [0, 2], 3, distance_threshold=0.5)
    assert labels == ["Speaker 1", "Speaker 1", "Speaker 2"]


def test_max_speakers_cap():
    vs = [_v(*[1 if j == i else 0 for j in range(6)]) for i in range(6)]
    labels = labels_from_embeddings(vs, list(range(6)), 6, distance_threshold=0.1,
                                    max_speakers=3)
    assert len(set(labels)) <= 3


def test_empty_returns_default():
    assert labels_from_embeddings([], [], 3, distance_threshold=0.5) == ["Speaker 1"] * 3
