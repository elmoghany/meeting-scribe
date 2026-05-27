"""Speaker diarization — figuring out who (among the remote participants) said what.

Two backends, selected by MEETINGSCRIBE_DIARIZER:

  * ``resemblyzer`` (DEFAULT, key-free): Resemblyzer's pretrained voice encoder
    (weights bundled in the pip package — no token, no gated terms) produces a
    d-vector per ASR segment; agglomerative clustering groups them into
    Speaker 1, Speaker 2, …. Fully offline, fits the "no API keys" goal.

  * ``pyannote`` (optional, higher quality): pyannote/speaker-diarization-3.1.
    Needs a HF token AND one-time acceptance of the model's gated terms at
    https://hf.co/pyannote/speaker-diarization-3.1 .

``mic_activity`` is a dependency-free energy VAD on the mic stream, used as a
free, exact anchor for who "Me" is (see assemble.apply_me_prior).
"""
from __future__ import annotations

import numpy as np

from ..config import get_settings
from ..models import Segment
from .assemble import Turn


# --------------------------------------------------------------------------- #
# mic energy VAD (no deps) — anchors "Me"
# --------------------------------------------------------------------------- #
def mic_activity(mic_wav_path: str, frame_ms: int = 30, thresh_db: float = -45.0,
                 min_speech_ms: int = 250) -> list[Turn]:
    import soundfile as sf

    audio, sr = sf.read(mic_wav_path, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    frame = max(1, int(sr * frame_ms / 1000))
    turns: list[Turn] = []
    start = None
    for i in range(0, len(audio) - frame, frame):
        rms = float(np.sqrt(np.mean(audio[i: i + frame] ** 2)) + 1e-9)
        db = 20 * np.log10(rms)
        t = i / sr
        if db > thresh_db:
            if start is None:
                start = t
        else:
            if start is not None:
                if (t - start) * 1000 >= min_speech_ms:
                    turns.append(Turn(start=start, end=t, speaker="Me"))
                start = None
    if start is not None:
        turns.append(Turn(start=start, end=len(audio) / sr, speaker="Me"))
    return _merge_turns(turns, max_gap=0.4)


def _merge_turns(turns: list[Turn], max_gap: float) -> list[Turn]:
    if not turns:
        return []
    out = [turns[0]]
    for t in turns[1:]:
        if t.speaker == out[-1].speaker and t.start - out[-1].end <= max_gap:
            out[-1].end = t.end
        else:
            out.append(t)
    return out


# --------------------------------------------------------------------------- #
# key-free diarization: Resemblyzer embeddings + clustering
# --------------------------------------------------------------------------- #
def diarize_segments(wav_path: str, segments: list[Segment], max_speakers: int = 8,
                     distance_threshold: float | None = None, min_seg_sec: float = 0.6,
                     ) -> list[str]:
    """Return a speaker label ("Speaker 1"…) for each input segment, key-free.

    Embeds each segment's audio with Resemblyzer and clusters the d-vectors.
    Segments too short to embed inherit the previous labeled segment's speaker.
    """
    import os

    from resemblyzer import VoiceEncoder, preprocess_wav
    from sklearn.cluster import AgglomerativeClustering

    if distance_threshold is None:
        # cosine-distance merge threshold; ~0.55 separates distinct real speakers.
        distance_threshold = float(os.getenv("MEETINGSCRIBE_DIAR_THRESHOLD", "0.55"))

    wav = preprocess_wav(wav_path)  # mono float32 @ 16 kHz
    sr = 16000
    encoder = VoiceEncoder(verbose=False)

    embeds, idx_with_embed = [], []
    for i, seg in enumerate(segments):
        s0, s1 = int(seg.start * sr), int(seg.end * sr)
        clip = wav[s0:s1]
        if len(clip) >= int(min_seg_sec * sr):
            embeds.append(encoder.embed_utterance(clip))
            idx_with_embed.append(i)

    return labels_from_embeddings(embeds, idx_with_embed, len(segments),
                                  distance_threshold, max_speakers)


def labels_from_embeddings(embeds, idx_with_embed: list[int], n_segments: int,
                           distance_threshold: float, max_speakers: int = 8,
                           ) -> list[str]:
    """Cluster per-segment d-vectors → "Speaker N" per segment (pure, testable).

    Segments without an embedding (too short) inherit the previous label.
    """
    from sklearn.cluster import AgglomerativeClustering

    labels_out = ["Speaker 1"] * n_segments
    if not embeds:
        return labels_out

    X = np.vstack(embeds)
    if len(embeds) == 1:
        cluster_ids = [0]
    else:
        clusterer = AgglomerativeClustering(
            n_clusters=None, distance_threshold=distance_threshold,
            metric="cosine", linkage="average")
        cluster_ids = clusterer.fit_predict(X)
        if len(set(cluster_ids)) > max_speakers:
            clusterer = AgglomerativeClustering(
                n_clusters=max_speakers, metric="cosine", linkage="average")
            cluster_ids = clusterer.fit_predict(X)

    # stable label numbering by first appearance
    order: dict[int, int] = {}
    for cid in cluster_ids:
        if cid not in order:
            order[cid] = len(order) + 1
    embedded_labels = {idx_with_embed[k]: f"Speaker {order[cluster_ids[k]]}"
                       for k in range(len(idx_with_embed))}

    last = "Speaker 1"
    for i in range(n_segments):
        if i in embedded_labels:
            last = embedded_labels[i]
        labels_out[i] = last
    return labels_out


# --------------------------------------------------------------------------- #
# optional pyannote backend (gated)
# --------------------------------------------------------------------------- #
def diarize_pyannote(wav_path: str, num_speakers: int | None = None,
                     hf_token: str | None = None) -> list[Turn]:
    import os

    from pyannote.audio import Pipeline
    import torch

    token = hf_token or get_settings().hf_token
    if not token:
        raise RuntimeError("HUGGINGFACE_TOKEN required for the pyannote backend.")
    # pyannote 3.x → "speaker-diarization-3.1"; pyannote 4.x bundles everything in
    # "speaker-diarization-community-1". Configurable so you only accept the gated
    # terms for whichever your installed version needs.
    model = os.getenv("MEETINGSCRIBE_PYANNOTE_MODEL", "pyannote/speaker-diarization-3.1")
    try:
        pipeline = Pipeline.from_pretrained(model, token=token)
    except TypeError:
        pipeline = Pipeline.from_pretrained(model, use_auth_token=token)
    if torch.cuda.is_available():
        pipeline.to(torch.device("cuda"))
    kwargs = {"num_speakers": num_speakers} if num_speakers else {}
    result = pipeline(wav_path, **kwargs)
    # pyannote 3.x returns an Annotation; 4.x returns a DiarizeOutput wrapper whose
    # Annotation is at .speaker_diarization.
    annotation = getattr(result, "speaker_diarization", result)
    turns = [Turn(start=float(seg.start), end=float(seg.end), speaker=str(label))
             for seg, _, label in annotation.itertracks(yield_label=True)]
    turns.sort(key=lambda t: t.start)
    return turns


# --------------------------------------------------------------------------- #
# dispatcher used by the pipeline
# --------------------------------------------------------------------------- #
def label_speakers(wav_path: str, segments: list[Segment]) -> list[Segment]:
    """Assign speaker labels to `segments` in place using the configured backend.

    Returns the same segments (mutated). Raises on backend failure so the caller
    can fall back to a generic 'Others' label.
    """
    import sys

    from .assemble import assign_speakers, renumber_speakers

    backend = get_settings().diarizer
    if backend == "pyannote":
        try:
            turns = diarize_pyannote(wav_path, hf_token=get_settings().hf_token)
            # pyannote emits SPEAKER_00/01/…; normalize to "Speaker 1/2/…".
            return renumber_speakers(assign_speakers(segments, turns))
        except Exception as e:
            # pyannote weights gated/unavailable → degrade to key-free resemblyzer,
            # which still attempts real multi-speaker separation.
            print(f"[diarize] pyannote unavailable ({type(e).__name__}); "
                  f"falling back to resemblyzer.", file=sys.stderr)
    labels = diarize_segments(wav_path, segments)
    for seg, lab in zip(segments, labels):
        seg.speaker = lab
    return segments
