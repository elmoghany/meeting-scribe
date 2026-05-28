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
