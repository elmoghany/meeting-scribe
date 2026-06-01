# Changelog

All notable changes to MeetingScribe are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/). Dates are ISO-8601.

## [Unreleased]

### Fixed
- **Key-free diarization no longer collapses every meeting to one speaker.** The
  default Resemblyzer path reported a single speaker for *all* multi-speaker
  meetings. Two root causes, both fixed: (1) `preprocess_wav` trims silences and
  shifted the waveform off the Whisper timeline, so per-segment audio was sliced
  wrong — now we slice the untrimmed original; (2) the cluster-merge threshold
  `0.55` was miscalibrated for d-vectors (cosine range tops ~0.55) and merged
  everyone — recalibrated to `0.40` (`MEETINGSCRIBE_DIAR_THRESHOLD`). Verified on
  real 2/3/4-speaker clips: speaker-count accuracy went 0% → 40% exact / 80%
  within-1. See `docs/VERIFICATION.md`.
- ASR device detection uses CTranslate2's CUDA runtime, not torch's, so
  faster-whisper runs on GPU where torch can't see it; CPU fallback on GPU init
  failure (incl. CTranslate2 rejecting float16 on older cards).
- Action-item detection false positives ("we'll"→"well", "i'll"→"ill").
- **Transcript over-merging.** A full-pipeline run on real clips showed
  single-speaker stretches collapsing into giant segments (one clip became a
  single 150s segment with no chapters / unusable subtitle cues). `merge_adjacent`
  now caps merged length (30s) and splits any still-overlong segment, so
  transcripts keep click-to-seek anchors. Found by end-to-end testing, not unit
  tests — see `docs/VERIFICATION.md`.
- **SRT/VTT readability.** Subtitle exports split long segments into ~8s cues
  instead of showing a 30s paragraph on screen at once.

### Added
- **Auto chapters** — jump-to-topic timeline, keyword-titled, click to seek;
  included in the Markdown, HTML, and JSON exports.
- **Merge speakers** — one-click fix when diarization over-splits a speaker.
- **Ask across all meetings** — cross-meeting Q&A with citations, fully local.
- **Summary templates** — standup / 1:1 / interview / retro / sales, auto-detected.
- **Editable transcript & notes** — fix a mis-transcription or the AI summary inline.
- **Custom vocabulary** — bias transcription toward names/jargon.
- **Clips & highlight reels** — export a line or all starred lines as audio.
- **Self-contained HTML export** — a single shareable file, no external assets.
- **Bulk meeting delete** and a **sidebar title filter**.
- **Multi-speaker verification harness** (`scripts/verify_multispeaker.py`) —
  scores ASR WER + diarization speaker-count accuracy; the tool that surfaced the
  diarization bug above.

## [0.1.0]
- Initial local meeting-notes pipeline: mic + system-audio capture,
  faster-whisper transcription, diarization, extractive/LLM notes, action items,
  FastAPI dashboard with live transcript, search (FTS5), and exports.
