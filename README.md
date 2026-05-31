<div align="center">

# 🎙️ MeetingScribe

**The open-source, key-free Otter/Gemini alternative for Google Meet & Zoom.**
Live transcription · speaker diarization · AI summaries · action items · "ask your meeting" chat.
100% local. No API keys. MIT licensed.

[![CI](https://github.com/elmoghany/meeting-scribe/actions/workflows/ci.yml/badge.svg)](https://github.com/elmoghany/meeting-scribe/actions/workflows/ci.yml) · [WER 3.8/8.4/11.5% (EN 1/2/3-spk) · 3.7% (ES) on real audio](docs/VERIFICATION.md)

🌐 **Website:** [elmoghany.github.io/meetingnotes](https://elmoghany.github.io/meetingnotes/) · 📖 **[Usage](docs/USAGE.md)** · 🏗 **[Architecture](docs/ARCHITECTURE.md)** · 🤖 **[Headless join-bot](bot/README.md)** · 🤝 **[Contributing](CONTRIBUTING.md)**

</div>

---

MeetingScribe takes notes so you can actually be in the meeting. It captures your
**microphone ("Me")** and your computer's **system audio ("Others")** during any
Google Meet or Zoom call, transcribes it, figures out who said what, and writes
you a clean summary with action items — all on hardware you control.

Because it listens to your system audio instead of using a meeting bot or vendor
API, it works with **Meet and Zoom identically, with no keys, accounts, or
calendar access**. The only token it ever uses is an optional (free) HuggingFace
token to download the gated pyannote diarization weights once.

## How it works

```
                 ┌─────────────── your Windows PC ───────────────┐
  mic  ─────────▶│  DualRecorder ──▶ live Whisper (CPU) ──▶ draft │──▶ dashboard
  speakers ─────▶│  (mic.wav +        on-screen transcript        │    (FastAPI + WS)
  (Meet/Zoom)    │   system.wav)                                  │
                 └───────────────────────┬───────────────────────┘
                                          │ on stop: upload audio
                                          ▼
                 ┌──────────── Cornell unicorn GPU (SLURM) ───────┐
                 │  large-v3 ASR · pyannote diarization · LLM     │──▶ polished
                 │  summary + action items                        │    notes JSON
                 └────────────────────────────────────────────────┘
                 (falls back to local CPU if remote is disabled)
```

* **Live (local CPU):** a small `faster-whisper` model drafts the transcript in
  near-real-time as people talk.
* **Batch (Cornell GPU, or local CPU):** `large-v3` + pyannote diarization +
  a quantized LLM produce the final speaker-labeled transcript, summary, and
  action items. Heavy ML offloads to the Cornell cluster because a typical
  laptop/old GPU can't run it well.

## Features

| | |
|---|---|
| 🎙️ **Live transcription** | Watch text appear while you talk (local, CPU). |
| 👥 **Speaker diarization** | pyannote (or a key-free fallback) separates remote speakers; your mic is anchored as "Me". |
| 🧑‍🤝‍🧑 **Remembers voices** | Name a speaker once — they're auto-recognized in your future meetings (local voice embeddings, no cloud). |
| 🧠 **AI summary + decisions** | Quantized local LLM (GGUF) — no API keys. Extractive fallback always works. |
| 🧩 **Summary templates** | Tailor notes per meeting type — standup, 1:1, interview, retro, sales; auto-suggested from the transcript. |
| ✅ **Action items** | Auto-extracted with owner + due date; add/edit/delete manually, checkable in the UI. |
| 💬 **Ask your meeting** | Chat over the transcript: "What did we decide about the budget?" |
| 🧠 **Ask *all* meetings** | Cross-meeting Q&A with citations — like Fireflies AskFred / Fathom Perfect Recall, but local. |
| 🔊 **Synced audio playback** | Click any line to jump there; the playing line highlights & auto-scrolls. |
| ✨ **Highlights & comments** | Star key lines, add notes — collaboration without the cloud. |
| ✂️ **Clips & highlight reels** | Download any line as an audio clip, or all starred lines as one reel — share a moment. |
| 📊 **Talk-time analytics** | Per-speaker share, word counts, words-per-minute. |
| ✏️ **Rename & merge speakers** | Turn "Speaker 1" into real names in one click; **merge** two labels when diarization over-splits. Flows to transcript, exports, analytics. |
| 📝 **Editable transcript & notes** | Double-click a line to fix a mis-transcription; edit the AI summary/key-points/decisions inline. |
| 🔤 **Custom vocabulary** | Teach it names/jargon (dashboard or `.env`) to bias transcription — better proper nouns. |
| 🔎 **Search everything** | SQLite FTS5 full-text search across every past meeting. |
| 🗂️ **Bulk manage** | Multi-select meetings in the sidebar and delete in one go (audio + notes). |
| 📝 **Export anywhere** | Markdown, plain text, **SRT/VTT subtitles**, a self-contained **HTML page**, and JSON. |
| 🖥️ **Local web dashboard** | Start/stop, live transcript, browse, rename speakers, chat. |
| 🔐 **100% local / MIT** | No cloud, no accounts. Data stays in `C:\cornell\meetingnotes`. |

## Quickstart

```bash
# 1. install (CPU-safe core; faster-whisper needs no torch)
python -m venv .venv && .venv/Scripts/activate        # Windows
pip install -e .

# 2. configure
cp .env.example .env        # set HUGGINGFACE_TOKEN; pick MEETINGSCRIBE_REMOTE

# 3. (optional) pre-download models so it runs fully offline
python -m scripts.download_models --whisper base.en --llm Qwen/Qwen2.5-3B-Instruct-GGUF
#    then add the printed MEETINGSCRIBE_GGUF_PATH to .env

# 4. check your setup (audio devices, deps, Cornell reachability)
meetingscribe doctor
meetingscribe devices

# 5. go
meetingscribe serve         # → http://127.0.0.1:8765
#    or, headless:
meetingscribe record -t "Team standup" -p meet
```

> **Windows audio note:** "Others" is captured via WASAPI loopback of your
> default playback device. Make sure Meet/Zoom is playing through that device.

## Cornell GPU offload (optional, recommended)

A weak local GPU can't run `large-v3` + pyannote + an LLM well, so the batch
pass can run on the Cornell unicorn SLURM cluster:

```bash
# one-time (VPN up; passwordless key already installed)
bash scripts/cornell_setup.sh          # syncs code + builds conda env `mscribe`
# in .env:  MEETINGSCRIBE_REMOTE=1  and  CORNELL_GRES=gpu:nvidia_geforce_rtx_3090:1
```

When `MEETINGSCRIBE_REMOTE=1`, stopping a recording uploads the audio, submits an
sbatch job, waits, and pulls the result back. If the cluster is unreachable it
falls back to local CPU automatically.

## Configuration (`.env`)

| Var | Default | Meaning |
|---|---|---|
| `MEETINGSCRIBE_DATA_DIR` | `C:\cornell\meetingnotes` | Where DB, audio, notes live |
| `HUGGINGFACE_TOKEN` | — | Only to fetch gated pyannote weights |
| `MEETINGSCRIBE_LIVE_MODEL` | `base.en` | Live (CPU) Whisper model |
| `MEETINGSCRIBE_BATCH_MODEL` | `large-v3` | Batch Whisper model |
| `MEETINGSCRIBE_LLM_BACKEND` | `llamacpp` | `llamacpp` · `transformers` · `extractive` |
| `MEETINGSCRIBE_GGUF_PATH` | — | Path to quantized GGUF chat model |
| `MEETINGSCRIBE_REMOTE` | `0` | `1` = run batch on Cornell |
| `CORNELL_GRES` | `gpu:1` | SLURM gres (use full model name) |

## Auto-record scheduled Zoom meetings (optional)

Connect Zoom once and MeetingScribe will auto-start system-audio capture when a
scheduled meeting begins (and auto-stop after its window). You attend the call;
it records hands-free.

```bash
# in .env — Server-to-Server OAuth (simplest, no browser):
ZOOM_CLIENT_ID=...   ZOOM_CLIENT_SECRET=...   ZOOM_ACCOUNT_ID=...
# …or User-managed OAuth (browser): set CLIENT_ID + CLIENT_SECRET +
ZOOM_REDIRECT_URI=http://localhost:8765/oauth/zoom/callback
```
For the user-managed app, in the Zoom Marketplace set the **OAuth Redirect URL**
and **Allow List** to `http://localhost:8765/oauth/zoom/callback`, add scopes
`user:read` + `meeting:read`, then start the dashboard and visit
`http://localhost:8765/oauth/zoom/start` to authorize.

> **Auto-*record* vs auto-*join*:** this reads your schedule and records the
> meeting you're in. A bot that joins a call **on its own** (you absent) needs
> the separate **Zoom Meeting SDK** + a headless bot runtime — deliberately out
> of scope for the key-free design.

## Roadmap / not-yet

* Headless meeting-join bot — **scaffolded** ([`bot/`](bot/README.md): JWT,
  Python runner, C++ SDK glue, Docker, scheduler dispatch); pending a one-time
  Zoom Meeting SDK download + build to run end-to-end.
* Google Calendar auto-record (same scheduler, Google OAuth backend).
* Real-time diarization (currently live = "Me"/"Others", full diarization in batch).
* Multi-meeting concurrent recording.

## License

[MIT](LICENSE). Depends only on permissively-licensed components
(faster-whisper MIT, CTranslate2 MIT, FastAPI MIT, pyannote MIT, soundcard BSD,
llama-cpp-python MIT).
