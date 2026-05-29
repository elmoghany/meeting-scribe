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
_CUE_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})\.\d{3}\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})\.\d{3}")


def _hms_to_sec(h: str, m: str, s: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s)


def parse_vtt(text: str, time_cap_sec: float | None = None) -> str:
    """Return the cleaned, deduped concatenated transcript from a WebVTT file.

    If ``time_cap_sec`` is set, only include cues whose start time is < cap
    (so WER compares against the same audio window we transcribed)."""
    out: list[str] = []
    seen: set[str] = set()
    include = True       # current cue is within the time cap
    for line in text.splitlines():
        line = line.strip()
        if "-->" in line:
            if time_cap_sec is None:
                include = True
            else:
                m = _CUE_TIME.search(line)
                include = bool(m) and _hms_to_sec(*m.group(1, 2, 3)) < time_cap_sec
            continue
        if not line or line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        if line.isdigit():
            continue
        if "Kind:" in line or "Language:" in line:
            continue
        if not include:
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


def _ytdlp_cmd() -> list[str]:
    """Resolve yt-dlp deterministically: prefer `python -m yt_dlp` (same
    interpreter => same venv, no PATH ambiguity between a stale global yt-dlp
    and the one we installed), falling back to a `yt-dlp` binary on PATH."""
    try:
        import yt_dlp  # noqa: F401
        return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        if _have("yt-dlp"):
            return ["yt-dlp"]
        raise SystemExit("yt-dlp is not installed. `pip install yt-dlp`")


def _run_ytdlp(args: list[str], *, required: bool) -> subprocess.CompletedProcess:
    """Run yt-dlp, surfacing its stderr on failure instead of an opaque
    CalledProcessError traceback. If ``required`` and it fails, exit cleanly."""
    proc = subprocess.run([*_ytdlp_cmd(), *args], capture_output=True, text=True)
    if proc.returncode != 0 and required:
        tail = "\n".join((proc.stderr or proc.stdout or "").splitlines()[-8:])
        raise SystemExit(f"yt-dlp failed (exit {proc.returncode}):\n{tail}")
    return proc


_AUDIO_EXTS = {".wav", ".m4a", ".webm", ".opus", ".mp3"}


def _find_audio(out_dir: Path) -> Path | None:
    return next(iter(sorted(p for p in out_dir.glob("system.*")
                            if p.suffix.lower() in _AUDIO_EXTS)), None)


def _find_captions(out_dir: Path) -> Path | None:
    # Prefer user-uploaded "en" (no auto- prefix) over autogenerated variants.
    user_first = sorted(
        out_dir.glob("captions*.vtt"),
        key=lambda p: (0 if p.stem == "captions.en" else 1, p.name),
    )
    return user_first[0] if user_first else None


def yt_dlp_download(url: str, out_dir: Path, seconds: int) -> tuple[Path, Path | None]:
    """Download audio + English captions (user-uploaded preferred, auto as
    fallback). Idempotent — skips downloads when files already exist."""
    _ytdlp_cmd()  # fail fast with a clear message if yt-dlp is unavailable
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_tmpl = str(out_dir / "system.%(ext)s")
    caps_tmpl = str(out_dir / "captions.%(ext)s")
    section = f"*0-{seconds}" if seconds else None

    audio_path = _find_audio(out_dir)
    if audio_path:
        print(f"[verify] audio already present: {audio_path.name}", flush=True)
    else:
        audio_args = ["-f", "bestaudio", "-o", audio_tmpl, "--no-playlist",
                      "--quiet", "--no-warnings", url]
        if section:
            audio_args[-1:-1] = ["--download-sections", section]
        if _have("ffmpeg"):
            audio_args[-1:-1] = ["-x", "--audio-format", "wav", "--audio-quality", "0"]
        print(f"[verify] downloading audio ({seconds or 'full'}s) ...", flush=True)
        _run_ytdlp(audio_args, required=True)
        audio_path = _find_audio(out_dir)
        if not audio_path:
            raise SystemExit(f"yt-dlp did not produce an audio file in {out_dir}")

    caps_path = _find_captions(out_dir)
    if caps_path:
        print(f"[verify] captions already present: {caps_path.name}", flush=True)
    else:
        # Pull user-uploaded subs first (better quality, less rate-limited),
        # then auto-subs as a fallback. Either one yields captions*.vtt.
        for mode in ("--write-subs", "--write-auto-subs"):
            print(f"[verify] fetching captions: {mode} ...", flush=True)
            # Captions are optional — never fatal (we just skip WER).
            _run_ytdlp([mode, "--skip-download", "--sub-langs", "en",
                        "--sub-format", "vtt", "-o", caps_tmpl, "--no-playlist",
                        "--quiet", "--no-warnings", url], required=False)
            if _find_captions(out_dir):
                break
        caps_path = _find_captions(out_dir)
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
        ref_text = parse_vtt(caps_path.read_text(encoding="utf-8", errors="replace"),
                             time_cap_sec=args.seconds or None)
        hyp_text = " ".join(seg["text"] for seg in result["segments"])
        wer_stats = wer(normalize_words(ref_text), normalize_words(hyp_text))
        print(f"[verify] WER: {wer_stats['wer']:.1%}  "
              f"(ref {wer_stats['ref_words']}, hyp {wer_stats['hyp_words']}, "
              f"edits {wer_stats['edits']})")

    out_md = Path(args.out) if args.out else s.notes_dir / f"{job_id}.md"
    write_report(args.url, audio_path, caps_path, result, wer_stats, elapsed, out_md)
    # Persist result.json next to the report so re-runs and downstream tools
    # don't need to re-transcribe.
    import json as _json
    out_md.with_suffix(".json").write_text(_json.dumps(result, indent=2),
                                           encoding="utf-8")
    print(f"[verify] report -> {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
