"""Cross-check MeetingScribe's ASR against Qwen2.5-Omni as an independent
reference recognizer (Qwen2.5-Omni-7B ~7.6% WER on Common Voice — far stronger
ground truth than YouTube auto-captions).

For each video: transcribe with BOTH faster-whisper (MeetingScribe's model) and
Qwen-Omni, then report:
  - wer_whisper_vs_qwen : agreement between the two models (primary signal)
  - wer_whisper_vs_caps : whisper vs YouTube captions
  - wer_qwen_vs_caps    : qwen vs YouTube captions  (triangulation)

A high whisper-vs-qwen disagreement on clean audio flags a real MeetingScribe
issue to investigate.

Run in the `qwen` conda env (cu12 torch + transformers + qwen-omni-utils).
    python -m scripts.verify_qwen --urls urls.txt --seconds 120
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
from scripts.verify_batch import gather_urls  # noqa: E402
from scripts.verify_youtube import (  # noqa: E402
    normalize_words, parse_vtt, wer, yt_dlp_download,
)


# --------------------------------------------------------------------------- #
# Qwen2.5-Omni transcription
# --------------------------------------------------------------------------- #
class QwenOmni:
    def __init__(self, model_id: str = "Qwen/Qwen2.5-Omni-7B"):
        import torch
        from transformers import (Qwen2_5OmniForConditionalGeneration,
                                   Qwen2_5OmniProcessor)
        self._torch = torch
        self.model = Qwen2_5OmniForConditionalGeneration.from_pretrained(
            model_id, torch_dtype="auto", device_map="auto",
            attn_implementation="sdpa")
        # we only need text out — free the speech "talker" to save memory
        if hasattr(self.model, "disable_talker"):
            self.model.disable_talker()
        self.processor = Qwen2_5OmniProcessor.from_pretrained(model_id)

    def transcribe(self, wav_path: str) -> str:
        from qwen_omni_utils import process_mm_info
        conv = [{"role": "user", "content": [
            {"type": "audio", "audio": wav_path},
            {"type": "text", "text": "Transcribe this audio verbatim. Output only "
             "the transcription text, no commentary."}]}]
        text = self.processor.apply_chat_template(conv, add_generation_prompt=True,
                                                  tokenize=False)
        audios, images, videos = process_mm_info(conv, use_audio_in_video=False)
        inputs = self.processor(text=text, audio=audios, images=images, videos=videos,
                                return_tensors="pt", padding=True)
        inputs = inputs.to(self.model.device).to(self.model.dtype)
        with self._torch.no_grad():
            out = self.model.generate(**inputs, return_audio=False,
                                      max_new_tokens=2048, do_sample=False)
        full = self.processor.batch_decode(out, skip_special_tokens=True)[0]
        # the decoded text includes the prompt; keep only the assistant answer
        return full.split("assistant\n")[-1].strip() if "assistant" in full else full.strip()


def _classify(qwen_words: int, whisper_words: int, min_ratio: float = 0.3) -> str:
    """Classify a (qwen, whisper) word-count pair: 'ok' if comparable, else the
    model that produced <min_ratio of the other ('qwen_empty'/'whisper_empty')
    so degenerate transcripts are excluded from the agreement metric."""
    if not qwen_words and not whisper_words:
        return "both_empty"
    big = max(qwen_words, whisper_words)
    small = min(qwen_words, whisper_words)
    if big >= 30 and (small / big) < min_ratio:
        return "qwen_empty" if qwen_words < whisper_words else "whisper_empty"
    return "ok"


def run_one(qwen, wmodel, url: str, work: Path, seconds: int) -> dict:
    try:
        audio, caps = yt_dlp_download(url, work, seconds)
    except (SystemExit, Exception):
        return {"url": url, "status": "download_failed"}
    if not audio:
        return {"url": url, "status": "download_failed"}
    # faster-whisper (MeetingScribe ASR)
    segs, info = wmodel.transcribe(str(audio), beam_size=5, vad_filter=True)
    w_hyp = normalize_words(" ".join(s.text.strip() for s in segs))
    # Qwen-Omni reference
    try:
        q_hyp = normalize_words(qwen.transcribe(str(audio)))
    except Exception as e:
        return {"url": url, "status": "qwen_failed", "error": f"{type(e).__name__}: {e}"}

    # Guard against a degenerate transcript (one model returned far less than the
    # other — e.g. Qwen bailing on a music/non-speech intro, returning 1-16 words
    # vs whisper's 300). WER against a near-empty reference gives absurd values
    # (327!) and poisons the aggregate; classify by word-ratio + exclude instead.
    nq, nw = len(q_hyp), len(w_hyp)
    r = {"url": url, "lang": info.language, "whisper_words": nw, "qwen_words": nq}
    status = _classify(nq, nw)
    r["status"] = status
    if status != "ok":
        return r
    r["wer_whisper_vs_qwen"] = wer(q_hyp, w_hyp)["wer"]  # qwen as reference
    if caps:
        ref = normalize_words(parse_vtt(caps.read_text(encoding="utf-8", errors="replace"),
                                        time_cap_sec=seconds))
        if len(ref) >= 20:
            r["wer_whisper_vs_caps"] = wer(ref, w_hyp)["wer"]
            r["wer_qwen_vs_caps"] = wer(ref, q_hyp)["wer"]
            r["ref_words"] = len(ref)
    return r


def aggregate(results: list[dict]) -> dict:
    ok = [r for r in results if r.get("status") == "ok"]

    def stats(key):
        xs = sorted(r[key] for r in ok if key in r and r[key] is not None)
        return {"n": len(xs), "mean": round(mean(xs), 4) if xs else None,
                "median": round(median(xs), 4) if xs else None,
                "p90": round(xs[min(len(xs) - 1, int(0.9 * len(xs)))], 4) if xs else None}

    by_status: dict[str, int] = {}
    for r in results:
        by_status[r.get("status", "?")] = by_status.get(r.get("status", "?"), 0) + 1
    return {
        "n_total": len(results), "n_ok": len(ok), "by_status": by_status,
        "whisper_vs_qwen": stats("wer_whisper_vs_qwen"),
        "whisper_vs_caps": stats("wer_whisper_vs_caps"),
        "qwen_vs_caps": stats("wer_qwen_vs_caps"),
    }


def write_summary(results, out_md: Path) -> None:
    a = aggregate(results)
    wq, wc, qc = a["whisper_vs_qwen"], a["whisper_vs_caps"], a["qwen_vs_caps"]
    lines = [
        "# MeetingScribe ASR vs Qwen2.5-Omni reference", "",
        f"- Videos: {a['n_total']} attempted, {a['n_ok']} scored",
        f"- Outcomes: {a['by_status']}", "",
        "| comparison | n | mean WER | median | p90 |", "|---|--:|--:|--:|--:|",
        f"| whisper vs **qwen** (agreement) | {wq['n']} | {wq['mean']} | {wq['median']} | {wq['p90']} |",
        f"| whisper vs captions | {wc['n']} | {wc['mean']} | {wc['median']} | {wc['p90']} |",
        f"| qwen vs captions | {qc['n']} | {qc['mean']} | {qc['median']} | {qc['p90']} |",
        "",
        "Low whisper-vs-qwen WER ⇒ MeetingScribe's ASR agrees with a strong",
        "independent recognizer. High-disagreement videos are listed below.", "",
        "## Worst whisper-vs-qwen disagreements", "",
    ]
    worst = sorted((r for r in results if r.get("status") == "ok"
                    and "wer_whisper_vs_qwen" in r),
                   key=lambda r: -r["wer_whisper_vs_qwen"])[:10]
    for r in worst:
        lines.append(f"- {r['wer_whisper_vs_qwen']:.3f} [{r.get('lang','?')}] {r['url']}")
    out_md.write_text("\n".join(lines), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--whisper-model", default="large-v3")
    ap.add_argument("--qwen-model", default="Qwen/Qwen2.5-Omni-7B")
    ap.add_argument("--urls", default=None)
    a = ap.parse_args(argv)

    s = get_settings()
    s.ensure_dirs()
    results_path = s.notes_dir / "qwen-results.jsonl"
    summary_path = s.notes_dir / "qwen-summary.md"
    work_root = s.recordings_dir / "qwenbatch"

    done = {}
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                done[r["url"]] = r
            except Exception:
                pass

    urls = ([u.strip() for u in Path(a.urls).read_text().splitlines() if u.strip()]
            if a.urls else gather_urls(a.count))
    print(f"[qwen] {len(urls)} urls, {len(done)} done", flush=True)

    from faster_whisper import WhisperModel
    from app.device import detect
    dev, comp = detect()
    print(f"[qwen] loading whisper {a.whisper_model} ({dev}) + Qwen-Omni ...", flush=True)
    wmodel = WhisperModel(a.whisper_model, device=dev, compute_type=comp,
                          download_root=str(s.models_dir / "whisper"))
    qwen = QwenOmni(a.qwen_model)

    results = list(done.values())
    for i, url in enumerate(urls, 1):
        if url in done:
            continue
        t0 = time.time()
        try:
            r = run_one(qwen, wmodel, url, work_root / f"v{i:03d}", a.seconds)
        except Exception as e:
            r = {"url": url, "status": "error", "error": f"{type(e).__name__}: {e}"}
        r["secs"] = round(time.time() - t0, 1)
        results.append(r)
        with results_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(r) + "\n")
        print(f"[qwen] {i}/{len(urls)} {r['status']:14} "
              f"w_vs_q={r.get('wer_whisper_vs_qwen','-')} {r.get('secs')}s", flush=True)
        if i % 3 == 0:
            write_summary(results, summary_path)
    write_summary(results, summary_path)
    print("QWEN_BATCH_DONE", aggregate(results)["whisper_vs_qwen"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
