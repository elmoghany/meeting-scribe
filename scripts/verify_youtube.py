"""End-to-end MeetingScribe verification against a real YouTube video.

Usage:
    python -m scripts.verify_youtube <youtube-url> [--seconds N] [--model NAME]

Downloads the video's audio + auto-captions via yt-dlp, runs MeetingScribe's
full batch pipeline on it, and computes Word Error Rate (WER) between our
transcript and YouTube's captions as ground truth. Outputs a Markdown report.

Requires: yt-dlp (`pip install yt-dlp`) and ffmpeg on PATH (yt-dlp uses it
to extract audio; if missing we fall back to downloading m4a and letting
faster-whisper decode it via PyAV).
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402


# --------------------------------------------------------------------------- #
# WebVTT caption parsing
# --------------------------------------------------------------------------- #
_TAG = re.compile(r"<[^>]+>")
_BRACKET_CUE = re.compile(r"\[[^\]]+\]")  # [Music], [Applause], ...


def parse_vtt(text: str) -> str:
    """Return the cleaned, deduped concatenated transcript from a WebVTT file."""
    out: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or "-->" in line or line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        if line.isdigit():
            continue
        if "Kind:" in line or "Language:" in line:
            continue
        clean = _TAG.sub("", line)
        clean = _BRACKET_CUE.sub("", clean).strip()
        if clean and clean not in seen:  # YouTube auto-caps duplicate every line
            seen.add(clean)
            out.append(clean)
    return " ".join(out)


# --------------------------------------------------------------------------- #
# Word Error Rate (Levenshtein over words; stdlib only)
# --------------------------------------------------------------------------- #
_WORD = re.compile(r"[a-z0-9']+")


def normalize_words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def wer(ref: list[str], hyp: list[str]) -> dict:
    n, m = len(ref), len(hyp)
    if n == 0:
        return {"wer": 1.0 if m else 0.0, "edits": m, "ref_words": 0, "hyp_words": m}
    # dynamic programming
    prev = list(range(m + 1))
    cur = [0] * (m + 1)
    for i in range(1, n + 1):
        cur[0] = i
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev, cur = cur, prev
    edits = prev[m]
    return {"wer": round(edits / n, 3), "edits": edits, "ref_words": n, "hyp_words": m}


# --------------------------------------------------------------------------- #
# yt-dlp orchestration
# --------------------------------------------------------------------------- #
def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def yt_dlp_download(url: str, out_dir: Path, seconds: int) -> tuple[Path, Path | None]:
    """Download audio (best-effort) + English auto-captions. Returns (audio_path,
    captions_path|None)."""
    if not _have("yt-dlp"):
        raise SystemExit("yt-dlp is not installed. `pip install yt-dlp`")
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_tmpl = str(out_dir / "system.%(ext)s")
    caps_tmpl = str(out_dir / "captions.%(ext)s")
    section = f"*0-{seconds}" if seconds else None

    # Audio. Try wav (needs ffmpeg); fall back to best m4a (no ffmpeg required).
    audio_args = ["yt-dlp", "-f", "bestaudio", "-o", audio_tmpl, "--no-playlist",
                  "--quiet", "--no-warnings", url]
    if section:
        audio_args[-1:-1] = ["--download-sections", section]
    if _have("ffmpeg"):
        audio_args[-1:-1] = ["-x", "--audio-format", "wav", "--audio-quality", "0"]
    print(f"[verify] downloading audio ({seconds or 'full'}s) ...", flush=True)
    subprocess.run(audio_args, check=True)

    # Pick whatever ended up there
    audio_path = next(iter(sorted(p for p in out_dir.glob("system.*")
                                  if p.suffix.lower() in {".wav", ".m4a", ".webm",
                                                          ".opus", ".mp3"})), None)
    if not audio_path:
        raise SystemExit(f"yt-dlp did not produce an audio file in {out_dir}")
    # MeetingScribe's recorder expects system.wav; if we got m4a/webm just use the
    # raw file (faster-whisper handles it via PyAV).

    # Auto-captions (English). Don't fail if absent — we just won't compute WER.
    caps_args = ["yt-dlp", "--write-auto-subs", "--skip-download",
                 "--sub-langs", "en.*", "--sub-format", "vtt",
                 "-o", caps_tmpl, "--no-playlist", "--quiet", "--no-warnings", url]
    print("[verify] fetching captions ...", flush=True)
    subprocess.run(caps_args, check=False)
    caps_path = next(iter(sorted(out_dir.glob("captions*.vtt"))), None)
    return audio_path, caps_path


# --------------------------------------------------------------------------- #
# Pipeline + report
# --------------------------------------------------------------------------- #
def run_pipeline(audio_path: Path, batch_model: str) -> dict:
    """Run compute_pipeline on the YouTube audio. Works whether the file is wav
    or another format (faster-whisper decodes via PyAV either way)."""
    from app.pipeline.process import compute_pipeline

    # compute_pipeline reads `<audio_dir>/system.wav` (and optionally mic.wav).
    # If we got a non-wav file, give it a system.wav name by symlink/copy.
    audio_dir = audio_path.parent
    if audio_path.name != "system.wav":
        target = audio_dir / "system.wav"
        if not target.exists():
            try:
                target.symlink_to(audio_path.name)
            except OSError:
                shutil.copy2(audio_path, target)
    return compute_pipeline(str(audio_dir), batch_model=batch_model).to_json()


def write_report(url: str, audio_path: Path, captions_path: Path | None, result: dict,
                 wer_stats: dict | None, t_elapsed: float, out_md: Path) -> None:
    speakers = sorted({s["speaker"] for s in result["segments"]})
    n_segments = len(result["segments"])
    n_actions = len(result["action_items"])
    overview = result["summary"].get("overview", "")
    sample = result["segments"][:5]

    verdict_bits = []
    if wer_stats is not None:
        verdict_bits.append(
            f"**WER {wer_stats['wer']:.1%}** vs YouTube auto-captions "
            f"({wer_stats['ref_words']} reference words, "
            f"{wer_stats['edits']} edits)"
        )
        if wer_stats["wer"] < 0.20:
            verdict_bits.append("✅ transcription accuracy is good (<20%)")
        elif wer_stats["wer"] < 0.35:
            verdict_bits.append("⚠️ moderate WER — common for noisy or fast audio")
        else:
            verdict_bits.append("❌ high WER — investigate (model size? audio quality?)")
    verdict_bits.append(f"**{len(speakers)} speaker(s) detected**: {', '.join(speakers)}")
    verdict_bits.append(f"**{n_actions} action item(s) extracted**")

    lines = [
        "# MeetingScribe — YouTube verification report",
        "",
        f"- **Source:** {url}",
        f"- **Audio file:** `{audio_path.name}`",
        f"- **Captions file:** `{captions_path.name if captions_path else '— (none)'}`",
        f"- **Pipeline runtime:** {t_elapsed:.1f}s",
        f"- **Detected language:** {result.get('language', '?')}",
        f"- **Notes backend:** {result.get('backend', '?')}",
        "",
        "## Verdict",
        "",
        *(f"- {v}" for v in verdict_bits),
        "",
        "## Summary (extractive)",
        "",
        overview or "_(no overview generated)_",
        "",
        "## Sample of our transcript",
        "",
    ]
    for s in sample:
        lines.append(f"- **{s['speaker']}** [{s['start']:.1f}–{s['end']:.1f}s]: "
                     f"{s['text']}")
    if not sample:
        lines.append("_(no segments)_")
    lines += ["", f"## Pipeline counts", "",
              f"- {n_segments} transcript segments",
              f"- {len(speakers)} distinct speakers",
              f"- {n_actions} action items", ""]
    out_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--seconds", type=int, default=300,
                   help="download only the first N seconds (default 300)")
    p.add_argument("--model", default="small.en",
                   help="faster-whisper batch model name (default small.en)")
    p.add_argument("--out", default=None,
                   help="markdown report path (default: <data>/notes/verify-<id>.md)")
    args = p.parse_args()

    s = get_settings()
    s.ensure_dirs()
    job_id = "verify-" + uuid.uuid4().hex[:6]
    work = s.recordings_dir / job_id

    t0 = time.time()
    audio_path, caps_path = yt_dlp_download(args.url, work, args.seconds)
    print(f"[verify] audio:    {audio_path}")
    print(f"[verify] captions: {caps_path}")

    print(f"[verify] running pipeline ({args.model}) ...", flush=True)
    result = run_pipeline(audio_path, args.model)
    elapsed = time.time() - t0

    wer_stats = None
    if caps_path and caps_path.exists():
        ref_text = parse_vtt(caps_path.read_text(encoding="utf-8", errors="replace"))
        hyp_text = " ".join(seg["text"] for seg in result["segments"])
        wer_stats = wer(normalize_words(ref_text), normalize_words(hyp_text))
        print(f"[verify] WER: {wer_stats['wer']:.1%}  "
              f"(ref {wer_stats['ref_words']}, hyp {wer_stats['hyp_words']}, "
              f"edits {wer_stats['edits']})")

    out_md = Path(args.out) if args.out else s.notes_dir / f"{job_id}.md"
    write_report(args.url, audio_path, caps_path, result, wer_stats, elapsed, out_md)
    print(f"[verify] report -> {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
