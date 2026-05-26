"""Pre-download models so MeetingScribe runs fully offline / key-free afterward.

  python -m scripts.download_models                 # whisper + default GGUF LLM
  python -m scripts.download_models --whisper base.en --llm Qwen/Qwen2.5-3B-Instruct-GGUF

* Whisper weights are public (no token).
* The GGUF LLM is a quantized chat model (Q4_K_M by default) that runs on CPU.
* Only the gated pyannote diarization weights need HUGGINGFACE_TOKEN (set in .env);
  they download automatically on first diarization run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as a script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402


def download_whisper(model: str) -> None:
    from faster_whisper import WhisperModel

    s = get_settings()
    root = s.models_dir / "whisper"
    root.mkdir(parents=True, exist_ok=True)
    print(f"[whisper] downloading '{model}' → {root}")
    WhisperModel(model, device="cpu", compute_type="int8", download_root=str(root))
    print("[whisper] done.")


def download_gguf(repo: str, prefer: str = "q4_k_m") -> Path | None:
    from huggingface_hub import HfApi, hf_hub_download

    s = get_settings()
    out_dir = s.models_dir / "llm"
    out_dir.mkdir(parents=True, exist_ok=True)
    files = [f for f in HfApi().list_repo_files(repo) if f.lower().endswith(".gguf")]
    if not files:
        print(f"[llm] no .gguf files in {repo}", file=sys.stderr)
        return None
    chosen = next((f for f in files if prefer in f.lower()), files[0])
    print(f"[llm] downloading {repo}::{chosen} → {out_dir}")
    path = hf_hub_download(repo_id=repo, filename=chosen, local_dir=str(out_dir),
                           token=s.hf_token)
    print(f"[llm] done → {path}")
    print(f"\nAdd this to your .env:\n  MEETINGSCRIBE_GGUF_PATH={path}\n")
    return Path(path)


def download(whisper: str | None = None, gguf_repo: str | None = None) -> None:
    s = get_settings()
    download_whisper(whisper or s.batch_model)
    if gguf_repo:
        download_gguf(gguf_repo)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--whisper", default=None)
    ap.add_argument("--llm", default="Qwen/Qwen2.5-3B-Instruct-GGUF")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()
    download(whisper=a.whisper, gguf_repo=None if a.no_llm else a.llm)
