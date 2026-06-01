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
- **Notes precision** (found by content audits — running the full pipeline on
  real clips and reading the actual output): decisions no longer match bare
  "final" ("our final episode" / Spanish "al final" were false-flagged); action
  items reject speech acts ("I'll say/admit/be honest") and transitions ("let's
  go to the next clip") while keeping real commitments; over-long unpunctuated
  "sentences" (e.g. Spanish Whisper output without `.!?`) are wrapped so
  overviews/decisions stay readable instead of a 200-word run.
- **Regenerate no longer destroys your work.** Re-running notes wiped
  manually-added action items and reset every done checkmark. Items now track an
  auto/manual source; regenerate replaces only the auto ones and carries over
  done-state by text — manual items and completions survive.
- **Rename/merge speakers keeps highlights & comments.** It used to replace
  segments (new IDs), orphaning every annotation. Now it relabels in place,
  preserving segment IDs so your stars and notes stay attached.
- **Reprocess keeps highlights & comments.** Re-transcription necessarily makes
  new segments, but annotations are now re-anchored to the segment covering the
  same moment instead of being orphaned.
- **Hardening / escaping.** Search no longer 500s on a stray quote (FTS
  fallback); a configured LLM that errors at inference falls back to extractive
  so the transcript is preserved; user-controlled values are escaped everywhere
  (speaker names in the transcript/search, the OAuth-callback error param); and
  every path-using endpoint validates the meeting before touching the filesystem.
- **Extraction accuracy (content-audit pass).** Running the pipeline on realistic
  transcripts and reading the output surfaced several recall/precision gaps, all
  fixed and pinned with positive + negative tests:
  - *Action items* now detect the canonical "**Sarah will handle the migration**"
    third-person assignment (the whitelist gained task verbs like
    handle/own/lead/coordinate, while prediction verbs stay excluded) and
    attribute the owner to the **named person**, not the speaker.
  - *Due dates* now parse "by next Tuesday", "before the 15th", and "in two
    weeks" (qualified weekdays, ordinals, relative durations) — while "next
    steps" / "on it" / "this quarter" stay unmatched, and a vague relative
    duration no longer overrides a hedge ("maybe … in two weeks or so" stays a
    musing).
  - *Decisions* detect more phrasings ("the team chose", "it was decided", "we
    are going with", "settled on") and no longer false-flag the bare noun
    ("we need to **make a decision**", "the decision is pending").
  - *Sentiment* dropped neutral product-logistics terms (ship/launch) from the
    positive lexicon — they were biasing every product meeting positive ("the
    launch failed" had cancelled out) — and added missing negatives
    (disaster/crisis/angry).
  - *Chapters* merge adjacent sections that resolve to the same title, so one
    topic spilling across time buckets no longer shows as duplicate chapters.
  - *WebVTT export* escapes `& < >` in cue text and speaker names; raw markup
    was producing spec-invalid cues the in-browser player silently dropped.

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
- **Stemmed full-text search** — searching "hire" now finds "hiring/hired/hires"
  (and "decide"→"decided", "migrate"→"migrating"). Existing databases are
  upgraded in place; no re-transcription needed.
- **Bulk meeting delete** and a **sidebar title filter**.
- **Multi-speaker verification harness** (`scripts/verify_multispeaker.py`) —
  scores ASR WER + diarization speaker-count accuracy; the tool that surfaced the
  diarization bug above.
- **Task-driven review** — meetings show an open-action-item count badge and a
  "needs follow-up" filter.
- **Jump to source** — global search and ask-all citations scroll to and flash
  the matching line; results are keyboard-accessible.
- **Friendly export filenames** — downloads use the meeting title
  (`Q3-Planning.md`) instead of the internal id.
- Clearer states — a "processing…" hint while a meeting is transcribing, and an
  "audio removed" note after freeing a recording's WAVs.
- `meetingscribe doctor` now also checks the DB (opens + migrates), free disk
  space, and the key-free diarizer deps + ffmpeg.

## [0.1.0]
- Initial local meeting-notes pipeline: mic + system-audio capture,
  faster-whisper transcription, diarization, extractive/LLM notes, action items,
  FastAPI dashboard with live transcript, search (FTS5), and exports.
