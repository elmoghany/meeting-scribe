"""Multi-speaker verification: ASR WER + diarization speaker-count accuracy.

Complements verify_batch (which only scores single-stream WER) by running the
full pipeline — faster-whisper transcription AND pyannote diarization — on
clips that are KNOWN to have several speakers (panels, debates, interviews),
then checking how close the detected speaker count is to the expected count.

Input file format (one clip per line):

    https://youtu.be/XXXX  3        # URL  expected_speakers
    https://youtu.be/YYYY            # expected unknown (count recorded, not scored)

Usage:
    python -m scripts.verify_multispeaker --urls multispeaker.txt \
        --seconds 180 --model large-v3

Outputs (under <data_dir>/notes/):
    multispeaker-results.jsonl   one line per clip
    multispeaker-summary.md      WER + speaker-count accuracy
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from scripts.verify_batch import _download  # noqa: E402
from scripts.verify_youtube import (  # noqa: E402
    _find_audio, normalize_words, parse_vtt, wer,
)


def parse_urls_file(text: str) -> list[tuple[str, int | None]]:
    """Each non-empty, non-# line is 'URL [expected_speakers]'."""
    out: list[tuple[str, int | None]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        url = parts[0]
        exp: int | None = None
        if len(parts) > 1:
            try:
                exp = int(parts[1])
            except ValueError:
                exp = None
        out.append((url, exp))
    return out


def detect_speakers(audio_path: str, segments) -> list[str] | None:
    """Sorted distinct speaker labels for a clip via the app's diarization
    dispatcher, or None if it fails.

    Uses `label_speakers` — the SAME path the live pipeline uses — so it honors
    the configured backend AND its key-free Resemblyzer fallback. That makes
    this verification representative of what a no-API-key user actually gets
    (MeetingScribe's whole premise), instead of a token-gated pyannote-only path.

    Returning the labels (not just a count) makes the two failure modes legible
    in the results — collapse-to-one (['Speaker 1']) vs phantom explosion
    (['Speaker 1'..'Speaker 9']) — without re-running.
    """
    try:
        from app.pipeline.diarize import label_speakers
        labeled = label_speakers(audio_path, segments)
        return sorted({s.speaker for s in labeled})
    except Exception as e:  # noqa: BLE001
        print(f"[ms] diarize failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return None


def run_one(model, url: str, expected: int | None, work: Path, seconds: int) -> dict:
    from app.models import Segment
    audio, caps = _download(url, work, seconds)
    if not audio:
        return {"url": url, "expected_speakers": expected, "status": "download_failed"}
    segs, info = model.transcribe(str(audio), beam_size=5, vad_filter=True)
    segs = list(segs)
    hyp = " ".join(s.text.strip() for s in segs)
    app_segs = [Segment(start=float(s.start), end=float(s.end), text=s.text,
                        speaker="?", source="batch") for s in segs]
    labels = detect_speakers(str(audio), app_segs)
    res = {"url": url, "expected_speakers": expected,
           "detected_speakers": len(labels) if labels is not None else None,
           "detected_labels": labels,
           "status": "ok", "lang": info.language}
    if caps:
        ref = normalize_words(parse_vtt(
            caps.read_text(encoding="utf-8", errors="replace"), time_cap_sec=seconds))
        if len(ref) >= 20:
            w = wer(ref, normalize_words(hyp))
            res.update(wer=w["wer"], ref_words=w["ref_words"], hyp_words=w["hyp_words"])
    return res


def aggregate(results: list[dict]) -> dict:
    """Pure summary: WER stats + speaker-count accuracy over scored clips."""
    ok = [r for r in results if r.get("status") == "ok"]
    wers = sorted(r["wer"] for r in ok if r.get("wer") is not None)
    # speaker-count accuracy only over clips with a known expected count + a detection
    scored = [r for r in ok if r.get("expected_speakers") is not None
              and r.get("detected_speakers") is not None]
    errs = [abs(r["detected_speakers"] - r["expected_speakers"]) for r in scored]
    exact = sum(1 for e in errs if e == 0)
    within1 = sum(1 for e in errs if e <= 1)
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r.get("status", "?")] = by_status.get(r.get("status", "?"), 0) + 1
    return {
        "n_total": len(results),
        "n_ok": len(ok),
        "by_status": by_status,
        "wer_mean": round(mean(wers), 4) if wers else None,
        "wer_median": round(median(wers), 4) if wers else None,
        "spk_scored": len(scored),
        "spk_exact": exact,
        "spk_exact_pct": round(100 * exact / len(scored), 1) if scored else None,
        "spk_within1": within1,
        "spk_within1_pct": round(100 * within1 / len(scored), 1) if scored else None,
        "spk_mean_abs_err": round(mean(errs), 3) if errs else None,
    }


def write_summary(results: list[dict], model_name: str, out_md: Path) -> None:
    a = aggregate(results)
    rows = []
    for r in results:
        if r.get("status") != "ok":
            continue
        labels = r.get("detected_labels") or []
        nlab = f"{len(labels)} ({', '.join(labels)})" if labels else r.get('detected_speakers', '?')
        rows.append(f"| {(r.get('url','') or '')[-22:]} | {r.get('expected_speakers','?')} "
                    f"| {nlab} | {r.get('wer','-')} |")
    lines = [
        "# Multi-speaker verification", "",
        f"- Model: `{model_name}`  ·  clips: {a['n_total']}  ·  ok: {a['n_ok']}",
        f"- **Mean WER: {a['wer_mean']}** (median {a['wer_median']})",
        f"- **Speaker count** — scored {a['spk_scored']}: "
        f"exact {a['spk_exact']} ({a['spk_exact_pct']}%), "
        f"±1 {a['spk_within1']} ({a['spk_within1_pct']}%), "
        f"mean abs err {a['spk_mean_abs_err']}", "",
        "## Outcomes", "",
        *(f"- {k}: {v}" for k, v in sorted(a["by_status"].items())), "",
        "## Per clip", "",
        "| clip | expected | detected | WER |", "|---|--:|--:|--:|",
        *rows, "",
    ]
    out_md.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", required=True, help="file: 'URL [expected_speakers]' per line")
    ap.add_argument("--seconds", type=int, default=180)
    ap.add_argument("--model", default="large-v3")
    a = ap.parse_args(argv)

    s = get_settings()
    s.ensure_dirs()
    results_path = s.notes_dir / "multispeaker-results.jsonl"
    summary_path = s.notes_dir / "multispeaker-summary.md"
    work_root = s.recordings_dir / "multispeaker"

    done: dict[str, dict] = {}
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                done[r["url"]] = r
            except Exception:
                pass

    clips = parse_urls_file(Path(a.urls).read_text(encoding="utf-8"))
    print(f"[ms] {len(clips)} clips, {len(done)} already done", flush=True)

    from app.device import detect
    from app.pipeline.asr import _load
    dev, comp = detect()
    # _load mirrors the app: tries the detected GPU compute type, then falls back
    # to CPU int8 if GPU init fails (e.g. CTranslate2 rejecting float16 on an old
    # Maxwell card like a TITAN X) — so the batch never crashes on whatever GPU
    # SLURM happens to assign.
    print(f"[ms] loading {a.model} on {dev}/{comp} (CPU fallback armed) ...", flush=True)
    model = _load(a.model, dev, comp)

    results = list(done.values())
    for i, (url, exp) in enumerate(clips, 1):
        if url in done:
            continue
        t0 = time.time()
        try:
            r = run_one(model, url, exp, work_root / f"c{i:03d}", a.seconds)
        except Exception as e:  # noqa: BLE001
            r = {"url": url, "expected_speakers": exp, "status": "error",
                 "error": f"{type(e).__name__}: {e}"}
        r["secs"] = round(time.time() - t0, 1)
        results.append(r)
        with results_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(r) + "\n")
        print(f"[ms] {i}/{len(clips)} {r['status']:16} "
              f"exp={r.get('expected_speakers')} det={r.get('detected_speakers')} "
              f"wer={r.get('wer','-')}", flush=True)
        write_summary(results, a.model, summary_path)

    write_summary(results, a.model, summary_path)
    a2 = aggregate(results)
    print(f"[ms] DONE — ok {a2['n_ok']}/{a2['n_total']}, mean WER {a2['wer_mean']}, "
          f"speaker exact {a2['spk_exact_pct']}%", flush=True)
    print("MULTISPEAKER_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
