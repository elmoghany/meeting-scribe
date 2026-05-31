"""Batch ASR verification: measure Word Error Rate across many YouTube videos.

Loads the faster-whisper model ONCE and runs it over a list of videos
(gathered via yt-dlp searches, or supplied), computing WER against each video's
own captions. Resumable (skips videos already in the JSONL), and writes a
rolling aggregate summary.

Usage:
    python -m scripts.verify_batch --count 100 --seconds 120 --model large-v3
    python -m scripts.verify_batch --urls urls.txt --model small.en

Outputs (under <data_dir>/notes/):
    batch-results.jsonl   one line per video {url,title,lang,wer,ref,hyp,...}
    batch-summary.md      aggregate stats (mean/median/p90 WER, counts, by-lang)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from scripts.verify_youtube import (  # noqa: E402
    _find_audio, _find_captions, _run_ytdlp, _ytdlp_cmd, normalize_words,
    parse_vtt, wer,
)

# Diverse default search queries → varied speakers, accents, languages, domains.
_DEFAULT_QUERIES = [
    "TED talk", "TEDx charla español", "conférence TED français",
    "podcast interview", "university lecture", "keynote speech",
    "news interview", "panel discussion", "product launch keynote",
    "scientist interview", "founder interview", "documentary narration",
]


def gather_urls(count: int, queries: list[str] | None = None) -> list[str]:
    """Collect up to `count` unique video URLs via yt-dlp searches."""
    queries = queries or _DEFAULT_QUERIES
    per = max(1, count // len(queries) + 1)
    urls: list[str] = []
    seen: set[str] = set()
    for q in queries:
        if len(urls) >= count:
            break
        proc = subprocess.run(
            [*_ytdlp_cmd(), f"ytsearch{per}:{q}", "--flat-playlist",
             "--print", "%(webpage_url)s", "--no-warnings"],
            capture_output=True, text=True)
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("http") and line not in seen:
                seen.add(line)
                urls.append(line)
                if len(urls) >= count:
                    break
    return urls[:count]


def _download(url: str, out_dir: Path, seconds: int) -> tuple[Path | None, Path | None]:
    out_dir.mkdir(parents=True, exist_ok=True)
    audio = _find_audio(out_dir)
    if not audio:
        args = ["-f", "bestaudio", "-x", "--audio-format", "wav",
                "--download-sections", f"*0-{seconds}",
                "-o", str(out_dir / "system.%(ext)s"),
                "--no-playlist", "--quiet", "--no-warnings", url]
        if _run_ytdlp(args, required=False).returncode != 0:
            return None, None
        audio = _find_audio(out_dir)
    caps = _find_captions(out_dir)
    if not caps:
        for mode in ("--write-subs", "--write-auto-subs"):
            _run_ytdlp([mode, "--skip-download", "--sub-langs", "en,es,fr",
                        "--sub-format", "vtt", "-o", str(out_dir / "captions.%(ext)s"),
                        "--no-playlist", "--quiet", "--no-warnings", url], required=False)
            if _find_captions(out_dir):
                break
        caps = _find_captions(out_dir)
    return audio, caps


def run_one(model, url: str, work: Path, seconds: int) -> dict:
    """Download + transcribe + WER for one video. Returns a result dict."""
    title = ""
    try:
        p = subprocess.run([*_ytdlp_cmd(), url, "--print", "%(title)s",
                            "--skip-download", "--no-warnings"],
                           capture_output=True, text=True)
        title = p.stdout.strip().splitlines()[0] if p.stdout.strip() else ""
    except Exception:
        pass
    audio, caps = _download(url, work, seconds)
    if not audio:
        return {"url": url, "title": title, "status": "download_failed"}
    if not caps:
        return {"url": url, "title": title, "status": "no_captions"}
    segs, info = model.transcribe(str(audio), beam_size=5, vad_filter=True)
    hyp = " ".join(s.text.strip() for s in segs)
    ref = normalize_words(parse_vtt(caps.read_text(encoding="utf-8", errors="replace"),
                                    time_cap_sec=seconds))
    if len(ref) < 20:
        return {"url": url, "title": title, "status": "captions_too_short"}
    w = wer(ref, normalize_words(hyp))
    return {"url": url, "title": title, "status": "ok", "lang": info.language,
            "lang_prob": round(info.language_probability, 3),
            "wer": w["wer"], "edits": w["edits"], "ref_words": w["ref_words"],
            "hyp_words": w["hyp_words"]}


def aggregate(results: list[dict]) -> dict:
    """Summary stats over the OK results (pure, testable)."""
    ok = [r for r in results if r.get("status") == "ok"]
    wers = sorted(r["wer"] for r in ok)
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r.get("status", "?")] = by_status.get(r.get("status", "?"), 0) + 1
    by_lang: dict[str, list[float]] = {}
    for r in ok:
        by_lang.setdefault(r.get("lang", "?"), []).append(r["wer"])

    def p90(xs):
        return xs[min(len(xs) - 1, int(0.9 * len(xs)))] if xs else None

    return {
        "n_total": len(results),
        "n_ok": len(ok),
        "by_status": by_status,
        "wer_mean": round(mean(wers), 4) if wers else None,
        "wer_median": round(median(wers), 4) if wers else None,
        "wer_p90": round(p90(wers), 4) if wers else None,
        "wer_min": round(wers[0], 4) if wers else None,
        "wer_max": round(wers[-1], 4) if wers else None,
        "by_lang": {k: {"n": len(v), "wer_mean": round(mean(v), 4)}
                    for k, v in sorted(by_lang.items())},
    }


def write_summary(results: list[dict], model_name: str, out_md: Path) -> None:
    a = aggregate(results)
    lines = [
        "# Batch ASR verification", "",
        f"- Model: `{model_name}`",
        f"- Videos attempted: {a['n_total']}  ·  scored (had captions): {a['n_ok']}",
        f"- **Mean WER: {a['wer_mean']}**  ·  median {a['wer_median']}  ·  "
        f"p90 {a['wer_p90']}  ·  min {a['wer_min']}  ·  max {a['wer_max']}", "",
        "## Outcomes", "",
        *(f"- {k}: {v}" for k, v in sorted(a["by_status"].items())), "",
        "## By detected language", "",
        "| lang | n | mean WER |", "|---|--:|--:|",
        *(f"| {k} | {v['n']} | {v['wer_mean']} |" for k, v in a["by_lang"].items()),
        "",
    ]
    out_md.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--urls", default=None, help="file of URLs (one per line)")
    a = ap.parse_args(argv)

    s = get_settings()
    s.ensure_dirs()
    results_path = s.notes_dir / "batch-results.jsonl"
    summary_path = s.notes_dir / "batch-summary.md"
    work_root = s.recordings_dir / "batch"

    # resume: load already-done URLs
    done: dict[str, dict] = {}
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                done[r["url"]] = r
            except Exception:
                pass

    if a.urls:
        urls = [u.strip() for u in Path(a.urls).read_text().splitlines() if u.strip()]
    else:
        print(f"[batch] gathering {a.count} URLs ...", flush=True)
        urls = gather_urls(a.count)
    print(f"[batch] {len(urls)} URLs, {len(done)} already done", flush=True)

    from faster_whisper import WhisperModel
    from app.device import detect
    dev, comp = detect()
    print(f"[batch] loading {a.model} on {dev}/{comp} ...", flush=True)
    model = WhisperModel(a.model, device=dev, compute_type=comp,
                         download_root=str(s.models_dir / "whisper"))

    results = list(done.values())
    for i, url in enumerate(urls, 1):
        if url in done:
            continue
        t0 = time.time()
        try:
            r = run_one(model, url, work_root / f"v{i:03d}", a.seconds)
        except Exception as e:
            r = {"url": url, "status": "error", "error": f"{type(e).__name__}: {e}"}
        r["secs"] = round(time.time() - t0, 1)
        results.append(r)
        with results_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(r) + "\n")
        w = r.get("wer")
        print(f"[batch] {i}/{len(urls)} {r['status']:18} "
              f"wer={w if w is not None else '-'} {r.get('title','')[:48]}", flush=True)
        if i % 5 == 0:
            write_summary(results, a.model, summary_path)

    write_summary(results, a.model, summary_path)
    a2 = aggregate(results)
    print(f"[batch] DONE — scored {a2['n_ok']}/{a2['n_total']}, "
          f"mean WER {a2['wer_mean']}", flush=True)
    print("BATCH_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
