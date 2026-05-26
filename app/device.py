"""Device auto-detection for local ML. Heavy import (torch) is lazy so the core
package imports fine on a machine without torch installed (e.g. the capture-only
client, or this dev box whose GT 710 can't run CUDA torch anyway)."""
from __future__ import annotations


def detect() -> tuple[str, str]:
    """Return (device, compute_type) suitable for faster-whisper.

    Falls back cleanly to CPU+int8 when torch/CUDA are absent — which is the
    expected case locally; the GPU path is exercised on the Cornell node.
    """
    try:
        import torch  # noqa: PLC0415  (intentionally lazy)

        if torch.cuda.is_available():
            return "cuda", "float16"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "cpu", "int8"  # CTranslate2 has no MPS path; CPU int8 is best on Mac
    except Exception:
        pass
    return "cpu", "int8"
