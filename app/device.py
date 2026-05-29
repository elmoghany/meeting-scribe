"""Device auto-detection for local ML. Heavy import (torch) is lazy so the core
package imports fine on a machine without torch installed (e.g. the capture-only
client, or this dev box whose GT 710 can't run CUDA torch anyway)."""
from __future__ import annotations


def detect() -> tuple[str, str]:
    """Return (device, compute_type) suitable for faster-whisper.

    Uses **CTranslate2's own** CUDA detection, NOT torch. faster-whisper runs on
    CTranslate2, which ships its own CUDA runtime — so a node where *torch*
    can't see the GPU (e.g. torch built for cu130 but the driver is cu12) can
    still run faster-whisper on the GPU. Keying this off torch wrongly forced
    CPU int8 on the Cornell A40 (large-v3 WER 15.9% on CPU vs 8.1% on GPU).

    Caller (asr._load) still falls back to CPU if the GPU init actually fails
    (e.g. a too-old local card), so over-reporting CUDA here is safe.
    """
    try:
        import ctranslate2  # noqa: PLC0415

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"
