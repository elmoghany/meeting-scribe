# Real-world verification

This file records a concrete end-to-end run of the MeetingScribe pipeline against
a public YouTube video, with **Word Error Rate** measured against the video's
own captions as ground truth. WER is the standard ASR-quality metric.

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
