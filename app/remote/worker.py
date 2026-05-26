"""Runs ON the Cornell GPU node (inside a SLURM allocation).

Usage:  python -m app.remote.worker <audio_dir> <out_json> [batch_model]

Reads mic.wav / system.wav from <audio_dir>, runs the full ASR + diarization +
notes pipeline on the GPU, and writes the serialized result to <out_json>.
The local side downloads that JSON and imports it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ..pipeline.process import compute_pipeline


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 2:
        print("usage: python -m app.remote.worker <audio_dir> <out_json> [model]",
              file=sys.stderr)
        return 2
    audio_dir, out_json = argv[0], argv[1]
    batch_model = argv[2] if len(argv) > 2 else None
    res = compute_pipeline(audio_dir, batch_model=batch_model)
    Path(out_json).write_text(json.dumps(res.to_json()), encoding="utf-8")
    print(f"wrote {out_json}: {len(res.segments)} segments, "
          f"{len(res.action_items)} action items, backend={res.backend}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
