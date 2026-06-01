"""Diarization clustering logic — tested deterministically with synthetic
embeddings (no audio / torch / resemblyzer needed)."""
import numpy as np
import pytest

pytest.importorskip("sklearn")
pytest.importorskip("soundfile")

from app.models import Segment  # noqa: E402
from app.pipeline.diarize import DEFAULT_DIAR_THRESHOLD, diarize_segments, labels_from_embeddings  # noqa: E402


def test_diarize_segments_slices_by_timeline(tmp_path, monkeypatch):
    """Cover the key-free diarize_segments orchestration (the timeline-alignment
    fix) with a mocked Resemblyzer: two time regions with distinct audio must
    yield distinct speakers (correct per-segment slicing), and a sub-min segment
    inherits the previous label."""
    import sys
    import types

    import soundfile as sf
    sr = 16000
    audio = np.concatenate([np.full(sr * 2, 0.5, np.float32),    # 0-2s "A"
                            np.full(sr * 2, -0.5, np.float32)])   # 2-4s "B"
    wav = tmp_path / "two.wav"
    sf.write(str(wav), audio, sr)

    fake = types.ModuleType("resemblyzer")

    class FakeEnc:
        def __init__(self, verbose=False):
            pass

        def embed_utterance(self, clip):
            m = float(np.mean(clip))                 # region-distinct vector
            return np.array([m, 1.0 - abs(m), 0.0])

    fake.VoiceEncoder = FakeEnc
    fake.preprocess_wav = lambda x, source_sr=None: np.asarray(x, dtype=np.float32)
    monkeypatch.setitem(sys.modules, "resemblyzer", fake)

    segs = [Segment(start=0, end=2, text="a", speaker="?", source="batch"),
            Segment(start=2, end=4, text="b", speaker="?", source="batch"),
            Segment(start=3.9, end=4.0, text="x", speaker="?", source="batch")]  # <0.6s
    labels = diarize_segments(str(wav), segs, distance_threshold=0.4)
    assert len(labels) == 3                          # one label per segment
    assert labels[0] != labels[1]                    # distinct regions -> distinct speakers
    assert labels[2] == labels[1]                    # too-short segment inherits previous


def test_speaker_embeddings_per_label(tmp_path, monkeypatch):
    """speaker_embeddings (voice-profile d-vectors) shares the timeline fix:
    one mean embedding per speaker label, sub-min segments ignored."""
    import sys
    import types

    import soundfile as sf
    from app.pipeline.diarize import speaker_embeddings
    sr = 16000
    sf.write(str(tmp_path / "p.wav"), np.full(sr * 4, 0.3, np.float32), sr)

    fake = types.ModuleType("resemblyzer")

    class FakeEnc:
        def __init__(self, verbose=False):
            pass

        def embed_utterance(self, clip):
            return np.array([float(np.mean(clip)), 0.0, 0.0])

    fake.VoiceEncoder = FakeEnc
    fake.preprocess_wav = lambda x, source_sr=None: np.asarray(x, dtype=np.float32)
    monkeypatch.setitem(sys.modules, "resemblyzer", fake)

    segs = [Segment(start=0, end=2, text="a", speaker="Me", source="batch"),
            Segment(start=2, end=4, text="b", speaker="Sam", source="batch"),
            Segment(start=3.95, end=4.0, text="x", speaker="Ghost", source="batch")]  # <0.6s
    embs = speaker_embeddings(str(tmp_path / "p.wav"), segs)
    assert set(embs) == {"Me", "Sam"}                # one entry per labeled speaker
    assert "Ghost" not in embs                       # too-short -> no embedding -> dropped
    assert len(embs["Me"]) == 3                       # a d-vector


def _v(*x):
    a = np.array(x, dtype=float)
    return a / np.linalg.norm(a)


def test_default_threshold_is_calibrated():
    # Verified on real 2/3/4-speaker clips: distinct speakers separate in the
    # 0.35-0.45 band; >=0.50 collapses everyone into one. The default must stay
    # in the calibrated window — a regression to the old 0.55 merged 8/8 clips.
    assert 0.30 < DEFAULT_DIAR_THRESHOLD <= 0.45


def test_default_threshold_separates_realistic_speakers_but_055_collapses():
    # Two speakers whose d-vectors sit at cosine distance ~0.45 (typical for
    # Resemblyzer), each with three near-identical segment embeddings.
    base_a = _v(1, 0, 0, 0, 0, 0)
    base_b = _v(0.55, 0.835, 0, 0, 0, 0)  # cosine distance ~0.45 from base_a

    def spk(base, dim):
        out = []
        for k in range(3):
            v = base.copy()
            v[dim + k] += 0.02 * (k + 1)  # tiny intra-speaker jitter
            out.append(v / np.linalg.norm(v))
        return out

    embeds = spk(base_a, 2) + spk(base_b, 2)  # 3 of A, then 3 of B
    idx = list(range(6))
    at_default = labels_from_embeddings(embeds, idx, 6, distance_threshold=DEFAULT_DIAR_THRESHOLD)
    at_old = labels_from_embeddings(embeds, idx, 6, distance_threshold=0.55)
    assert len(set(at_default)) == 2, "default threshold must keep two real speakers apart"
    assert len(set(at_old)) == 1, "0.55 is the broken setting that merged them"


def test_single_speaker_not_over_segmented_at_default():
    # Regression guard for lowering the threshold to 0.40: ONE speaker's segment
    # d-vectors vary (different content/tone) with intra-speaker cosine distance
    # up to ~0.30 (observed min/mean ~0.05/0.23 on a real clip). The default must
    # still keep a cohesive single speaker as ONE cluster, not split a monologue
    # into phantom "Speaker 2/3".
    base = _v(1, 0, 0, 0, 0, 0, 0)
    embeds = []
    for k in range(6):
        v = base.copy()
        v[1 + k] += 0.62           # distinct orthogonal jitter -> pairwise cos dist ~0.28
        embeds.append(v / np.linalg.norm(v))
    # sanity: intra-speaker spread sits below the default so the guard is meaningful
    from scipy.spatial.distance import pdist
    assert float(pdist(np.vstack(embeds), metric="cosine").max()) < DEFAULT_DIAR_THRESHOLD
    labels = labels_from_embeddings(embeds, list(range(6)), 6,
                                    distance_threshold=DEFAULT_DIAR_THRESHOLD)
    assert len(set(labels)) == 1, "single speaker must not over-segment at the default"


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
