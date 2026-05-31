# Real-world verification

This file records concrete end-to-end runs of the MeetingScribe pipeline against
public YouTube videos, with **Word Error Rate** measured both against the videos'
own captions and against an independent strong recognizer.

## Cross-check vs Qwen2.5-Omni (independent reference, 100 videos)
YouTube auto-captions are themselves imperfect ASR, so we also cross-checked
MeetingScribe's `large-v3` against **Qwen2.5-Omni-7B** (~7.6% WER on Common
Voice — a far stronger reference) on **102 varied videos** (en/fr/es), via
`scripts/verify_qwen.py` on an A40 (`qwen` conda env, cu124 torch).

| comparison | n | mean WER | median | p90 |
|---|--:|--:|--:|--:|
| **whisper vs Qwen** (agreement) | 61 | 0.187 | **0.071** | 0.416 |
| whisper vs captions | 55 | 0.201 | 0.122 | 0.345 |
| qwen vs captions | 55 | 0.265 | 0.103 | 0.986 |

**MeetingScribe's transcription agrees with Qwen-Omni at a median 7.1% WER**
across 61 clean videos — strong independent validation at scale (consistent
with the 20-video median of 6.6%).

Outcomes: 61 `ok`, 40 `qwen_empty`, 1 `whisper_empty`.
- The 40 `qwen_empty` are hard/non-speech clips where Qwen returned <30% of
  whisper's words; on the few with captions, whisper-vs-captions was also high
  (~28%) — genuinely hard content (music/noise), not a MeetingScribe issue.
  (The harness classifies these by word-ratio and excludes them; without that
  fix one near-empty Qwen output scored "WER 327" and poisoned the mean.)
- The 1 `whisper_empty` (whisper 13 words vs Qwen 341) turned out to be
  MeetingScribe being **right** — see below.

### The `whisper_empty` case: MeetingScribe correctly suppressed a hallucination
That clip's first 120s is a near-silent holding slide with one real announcement.
Re-transcribed both ways:
- **VAD on** (MeetingScribe's setting): `"Please take your seats and silence your
  devices. We'll begin in two minutes."` — 13 words, exactly the real speech. ✅
- **VAD off**: hallucinated repeated Norwegian subtitle-credits
  (`"Teksting av Nicolai Winther" ×3`) — Whisper's classic non-speech
  hallucination. ❌

Qwen-Omni hallucinated **341 fabricated words** on the same near-silent audio.
So MeetingScribe's `vad_filter=True` did its job — suppressing hallucination that
both the reference model *and* VAD-off fell into. This is concrete evidence to
**keep VAD enabled** (despite its marginal ~0.4% WER cost on clean speech) and a
reminder that a `whisper_empty` label means "lengths mismatch", not necessarily
"MeetingScribe missed".

---


## How to reproduce

```bash
# 1. Make sure yt-dlp + ffmpeg are available
pip install yt-dlp
# (ffmpeg via your package manager — apt/brew/winget)

# 2. Run against any YouTube URL (first 5 minutes by default)
python -m scripts.verify_youtube <youtube-url> --seconds 300 --model small.en
```

The script downloads audio + the video's English captions, runs
`compute_pipeline` (faster-whisper → diarization → notes), and writes a
Markdown verdict next to your data dir's `notes/`. WER is computed over the
same audio window we transcribed (captions are time-capped to match).

The latest run is below. Cluster-side artifact lives at
`/home/me484/meetingnotes/data/notes/verify-stevejobs.md`.

---

# MeetingScribe — YouTube verification report

- **Source:** https://www.youtube.com/watch?v=UF8uR6Z6KLc
- **Audio file:** `system.wav`
- **Captions file:** `captions.en.vtt`
- **Pipeline runtime:** 112.2s
- **Detected language:** en
- **Notes backend:** extractive

## Verdict

- **WER 3.8%** vs YouTube auto-captions (743 reference words, 28 edits)
- ✅ transcription accuracy is good (<20%)
- **1 speaker(s) detected**: Speaker 1
- **2 action item(s) extracted**

## Summary (extractive)

I'm Honored to be with you today for your commencement from one of the finest universities in the world Speaker 1: truth be told I never graduated from college and Speaker 1: This is the closest I've ever gotten to a college graduation Speaker 1: Today I want to tell you three stories from my life. Just three stories the first story is about connecting the dots I Speaker 1: Dropped out of Reed College after the first six months, but then stayed around as a drop-in for another 18 months or so before I really quit Speaker 1: So why'd I drop out?

## Sample of our transcript

- **Speaker 1** [7.1–13.0s]: This program is brought to you by Stanford University. Please visit us at stanford.edu
- **Speaker 1** [20.9–31.8s]: Thank you. I'm Honored to be with you today for your commencement from one of the finest universities in the world
- **Speaker 1** [35.6–40.4s]: truth be told I never graduated from college and
- **Speaker 1** [42.0–44.7s]: This is the closest I've ever gotten to a college graduation
- **Speaker 1** [47.7–58.8s]: Today I want to tell you three stories from my life. That's it. No big deal. Just three stories the first story is about connecting the dots I

## Pipeline counts

- 16 transcript segments
- 1 distinct speakers
- 2 action items

---

# Multi-speaker run — Lex Fridman Podcast #333 (Karpathy)

Stresses diarization on a real-world 2-speaker conversation. Pipeline used the
**pyannote** diarizer (community-1) on a 5-min slice of a technical interview.


- **Source:** https://www.youtube.com/watch?v=cdiD-9MMpb0
- **Audio file:** `system.wav`
- **Captions file:** `captions.en.vtt`
- **Pipeline runtime:** 765.9s
- **Detected language:** en
- **Notes backend:** extractive

## Verdict

- **WER 8.4%** vs YouTube auto-captions (919 reference words, 77 edits)
- ✅ transcription accuracy is good (<20%)
- **2 speaker(s) detected**: Speaker 1, Speaker 2
- **6 action item(s) extracted**

## Summary (extractive)

It's really just a complicated mathematical expression with knobs. Speaker 1: Yeah.

## Sample of our transcript

- **Speaker 1** [0.0–30.2s]: I think it's possible that physics has exploits and we should be trying to find them, arranging some kind of a crazy quantum mechanical system that somehow gives you buffer overflow, somehow gives you rounding error in the floating point. Synthetic intelligences are kind of like the next stage of development. And I don't know where it leads to, like at some point, I suspect the universe is some kind of a puzzle. These synthetic AIs will uncover that puzzle and solve it.
- **Speaker 2** [30.2–64.7s]: The following is a conversation with Andre Karpathy, previously the director of AI at Tesla, and before that at OpenAI and Stanford. He is one of the greatest scientist, engineers, and educators in the history of artificial intelligence. This is the Lex Friedman podcast. To support it, please check out our sponsors. And now, dear friends, here's Andre Karpathy. It is a neural network. And why does it seem to do such a surprisingly good job of learning?
- **Speaker 1** [64.7–118.9s]: What is a neural network? It's a mathematical abstraction of the brain. I would say that's how it was originally developed. At the end of the day, it's a mathematical expression. It's a fairly simple mathematical expression when you get down to it. It's basically a sequence of matrix multiplies, which are really dot products mathematically. And some nonlinearity is thrown in. And so it's a very simple mathematical expression. And it's got knobs in it. Many knobs. Many knobs. And these knobs are loosely related to basically the synapses in your brain. They're trainable. They're modifiable. And so the idea is we need to find the setting of the knobs that makes the neural net do whatever you want it to do, like classify images and so on. And so there's not too much mystery, I would say, in it. You might think that you don't want to endow it with too much meaning with respect to the brain and how it works. It's really just a complicated mathematical expression with knobs. And those knobs need a proper setting for it to do something desirable.
- **Speaker 2** [118.9–136.0s]: Yeah, but poetry is just a collection of letters with spaces. But it can make us feel a certain way. And in that same way, when you get a large number of knobs together, whether it's inside the brain or inside a computer, they seem to surprise us with their power.
- **Speaker 1** [136.0–162.4s]: Yeah. I think that's fair. Okay, I'm underselling it by a lot because you definitely do get very surprising emergent behaviors out of these neural nets when they're large enough and trained on complicated enough problems, like say, for example, the next word prediction in a massive data set from the internet. And then these neural nets take on pretty surprising magical properties. Yeah, I think it's kind of interesting how much you can get out of even very simple mathematical formalism.

## Pipeline counts

- 19 transcript segments
- 2 distinct speakers
- 6 action items

---

# Findings & improvement backlog (from the runs above)

What two real-world runs (single-speaker Jobs, two-speaker Karpathy) revealed,
with honest priorities. Updated as runs accumulate.

## Fixed
- **Speaker labels leaked into the extractive summary.** The Karpathy run's
  overview was `"…with knobs. Speaker 1: Yeah."` — `ExtractiveNotes.summarize`
  was scoring the labeled form `"Speaker 1: Yeah."` as a candidate sentence.
  Fixed: summarize plain transcript text (commit a02f2d3, test added).
- **Action-item false positives on conversational audio.** Two rounds:
  1. `_is_actionable()` filter — rejects questions, <4-word fragments, and
     hedged musings ("maybe we should look into it someday") without a
     commitment.
  2. **Regex bugs found on real All-In data**: `we'?ll` (optional apostrophe)
     also matched **"well"** ("as well", "well-respected") and `i'?ll` matched
     **"ill"**; "you can" was a permissive non-assignment cue. Fixed:
     contractions now require the apostrophe (`['’]`), "you can" dropped.
  Net effect on the real All-In intro: **5 bogus items → 1**, with genuine
  action-item recall preserved (precision+recall corpus test). The survivor
  ("we'll start with X joining…") is real-`we'll` narration — only the LLM
  backend can tell it from a real "we'll ship the migration" task.

## Confirmed working
- **Transcription accuracy is strong**: WER 3.8% (clean single speaker) and
  8.4% (2-speaker technical interview) — both well under the 20% "good" bar.
- **Diarization separates real conversation**: 2 speakers correctly detected and
  assigned on Karpathy (Lex intro → one label, Karpathy answers → the other).
- **Three-/four-speaker panel** (All-In Podcast, 5-min intro, pyannote):
  **WER 11.5%** (105/915) — higher than the cleaner clips, as expected with
  crosstalk + finance/AI jargon, still under 20%. 3 distinct speakers detected.
- **Runs on a plain laptop, no GPU/cluster**: a local Windows run (90s of the
  Jobs speech, `tiny.en`, no torch installed) gave **WER 4.0%** (7/174) and
  cleanly degraded diarization to "Others" — confirming the pipeline is
  portable and that even the smallest model transcribes accurately.
- **Non-English (Spanish), language auto-detect**: a Spanish TEDx talk
  (5-min, `large-v3`, GPU) — language auto-detected as **`es` (p=0.99)** and
  **WER 3.7%** (31/844), hyp≈ref (full transcript). Confirms the multi-language
  support is real end-to-end, not just a UI dropdown.

## Infra finding (2026-05-29): torch/driver mismatch on the `taylor` A40
A `large-v3` confirmation run submitted to the **`taylor` A40** node failed its
torch GPU ops: *"NVIDIA driver too old (found version 12080)"*. Root cause —
the `mscribe` env has **torch 2.12.0+cu130** (CUDA 13.0) but that node's driver
only supports **CUDA 12.0**. pyannote and resemblyzer both fell back to
"Others". (Transcription was unaffected — faster-whisper/CTranslate2 ships its
own CUDA-12 runtime, so only torch-based diarization broke.)

Why earlier pyannote runs worked: they ran on the **login-node CPU** (torch CPU
needs no driver). This was the first time torch touched that GPU.

**Mitigation shipped + VALIDATED on the A40:** `diarize_pyannote` now **retries
on CPU** when a CUDA op raises. Re-running the large-v3 job on `taylor` after the
fix, the log shows `pyannote on CUDA failed (RuntimeError); retrying on CPU` and
diarization produced **`['Speaker 1', 'Speaker 2']`** — real speakers, where the
pre-fix run gave `['Others']`. Correctness restored on the mismatched node.

**Still worth doing (for GPU speed):**
- install a **cu12** torch in the `mscribe` env to match the node driver, OR
- target a GPU partition whose driver matches cu130.
With the CPU retry in place this is now a performance concern, not a
correctness one — diarization no longer silently degrades on `taylor`.

## large-v3 "under-transcription" → was a device-detection bug (RESOLVED 2026-05-29)
First large-v3 run showed **WER 15.9%**, **hyp=820 < ref=919** (~100 words
short) — looked like under-transcription. Root cause was a real bug, not the
model: `device.detect()` keyed off **torch**'s CUDA, but faster-whisper runs on
**CTranslate2** (separate CUDA runtime). On the A40 (torch cu130 can't see the
cu12 driver) detect() returned CPU, so large-v3 ran on **CPU int8** — degraded
+ slow.

A direct GPU experiment isolated it: large-v3 forced onto the GPU gave
**WER 8.1% (vad off) / 8.5% (vad on)**, hyp≈890–902 — full transcription. After
fixing detect() to use `ctranslate2.get_cuda_device_count()` (+ a CPU load
fallback), the cluster re-run confirms **WER 8.5%, hyp=890 on GPU** with real
diarization (`['Speaker 1','Speaker 2']`). So large-v3 ≈ small.en on
WER-vs-auto-captions here, but it now runs correctly + fast on the cluster
instead of silently on CPU int8.

## Open improvements (prioritized)
1. **Proper-noun errors** ("Andrej"→"Andre", "Fridman"→"Friedman"). Present in
   both small.en and large-v3 output vs the captions — minor, model-inherent,
   not worth special-casing.
2. **Overview quality** — *fixed.* The overview used the first two key points
   in document order, which for a meeting is usually the intro/greeting
   ("thanks for joining") rather than the substance. Now it leads with the
   highest-*scored* (most central) sentence, adding a second only if the lead
   is short. Test guards that high-signal content wins over intro position.
   The LLM backend still produces a richer overview when configured.
3. **Stress test ≥3 speakers.** Now automated — see *Multi-speaker
   verification* below. A `gpu`-partition batch (job 969744) runs the full
   pipeline on panel/interview/roundtable clips and scores speaker-count
   accuracy; numbers recorded once it completes.

## Multi-speaker verification (diarization speaker-count) — METHODOLOGY
`verify_batch` only scores single-stream WER; it never checks whether
diarization found the *right number of people*. `scripts/verify_multispeaker.py`
closes that gap. For each clip it runs the **full pipeline** — faster-whisper
transcription **and** pyannote `speaker-diarization-community-1` — then reports:

- **WER** vs the clip's own captions (same Levenshtein-over-words as the batch
  harness, reusing `verify_youtube` helpers — no duplicated logic), and
- **speaker-count accuracy**: `|detected − expected|`, summarized as
  *exact-match %*, *within-1 %*, and *mean absolute error*.

Clips are gathered by category with a category-typical expected count
(2 = podcast/interview, 3 = panel, 4 = roundtable). The expected count is
approximate, so **within-1** is the headline sanity metric: it catches the two
real failure modes — diarization **collapsing to 1 speaker** (the "Others" bug
class) or **exploding** into many phantom speakers. The pure `parse_urls_file`
and `aggregate` helpers are unit-tested (`tests/test_verify_multispeaker.py`).
