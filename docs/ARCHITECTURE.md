# Architecture

MeetingScribe is a **local-first, key-free** meeting notes tool. It captures
audio on your machine, transcribes and analyzes it locally (or on a GPU node you
control), and never sends data to a third-party service. This document maps the
codebase and the key design decisions.

## Big picture

```
                  ┌──────────────── your machine ─────────────────┐
  mic ───────────▶│ capture/recorder.py  ──▶ live ASR (CPU)  ──┐   │
  speakers ──────▶│ (mic.wav + system.wav, WASAPI loopback)    │   │
  (Meet/Zoom)     │                                            ▼   │
                  │ session.py ──emit──▶ server (FastAPI+WS) ──▶ dashboard
                  └──────────────────────┬─────────────────────────┘
                                         │ on stop: batch.run_batch()
                                         ▼
            remote.cornell (SLURM GPU)  OR  pipeline.process (local CPU)
              ASR (large-v3) · diarization (pyannote/resemblyzer) · notes
                                         │
                                         ▼
                         db.py (SQLite + FTS5) + notes/*.md
```

Two compute tiers, by design:
- **Live (local CPU):** a small `faster-whisper` model drafts the transcript in
  near-real-time so you see text immediately. No GPU needed.
- **Batch (Cornell GPU, or local CPU fallback):** `large-v3` + speaker
  diarization + the notes LLM produce the polished result. Offloaded because a
  typical laptop can't run this well; falls back to local CPU if the cluster is
  unreachable.

## Module map (`app/`)

| Module | Responsibility |
|---|---|
| `config.py` | All settings from env/`.env`; resolved at instantiation (testable). |
| `models.py` | Plain dataclasses: `Meeting`, `Segment`, `ActionItem`, `Summary`. |
| `db.py` | SQLite storage + FTS5 search; meetings, segments, summaries, action items, annotations, tags, profiles-ready. Pure stdlib. |
| `device.py` | CPU/CUDA auto-detection (lazy torch import). |
| `capture/recorder.py` | `DualRecorder`: mic + WASAPI-loopback → two WAVs; emits live windows. |
| `session.py` | `MeetingSession`: capture + live ASR thread + rolling live-notes; emits events. |
| `pipeline/asr.py` | `faster-whisper` wrapper (live windows + batch file). |
| `pipeline/diarize.py` | pyannote (default) + key-free resemblyzer fallback; mic VAD anchors "Me". |
| `pipeline/assemble.py` | Pure transcript assembly: overlap speaker-assignment, merge, renumber, render. |
| `pipeline/notes.py` | Summary/action-items/chat backends (llamacpp · transformers · extractive) + topic keywords. |
| `pipeline/exporters.py` | SRT/VTT/TXT/JSON/CSV + talk-time analytics (pure). |
| `pipeline/audiomix.py` | Mix mic+system → one playable `meeting.wav`. |
| `pipeline/process.py` | `compute_pipeline` (pure, no DB) + `process_local` (persist) + Markdown export. |
| `batch.py` | Decide local vs Cornell for the batch pass. |
| `remote/cornell.py` | paramiko SSH: upload audio, submit sbatch, poll, fetch result JSON. |
| `remote/worker.py` | Runs `compute_pipeline` on the GPU node, writes result JSON. |
| `integrations/zoom.py` | Zoom OAuth (server-to-server or user) → upcoming meetings. |
| `integrations/ics.py` | Dependency-free `.ics` parser + recurrence expansion. |
| `integrations/webhook.py` | Post notes to Slack/Discord/Notion/Zapier on finish. |
| `scheduler.py` | `AutoRecorder`: poll calendar sources → auto start/stop recording. |
| `server/main.py` | FastAPI app: REST + WebSocket + static dashboard; lifespan startup. |
| `server/static/` | Vanilla-JS dashboard (no build step). |
| `cli.py` | `serve`, `record`, `process`, `search`, `devices`, `fetch-model`, `doctor`. |

## Key decisions

- **Key-free by default.** No paid APIs. faster-whisper needs no torch for live;
  the extractive notes backend always works; resemblyzer diarization needs no
  token. The only optional token is a free HuggingFace one for pyannote weights.
- **Graceful degradation everywhere.** pyannote → resemblyzer → "Others";
  LLM backend → extractive; Cornell → local CPU; webhook failures are swallowed.
- **Pure cores, lazy heavy imports.** `assemble`, `exporters`, `ics`, the
  extractive notes, and the diarization clustering are pure and unit-tested;
  torch/faster-whisper/pyannote/soundcard import lazily so the package and tests
  run on a machine without them.
- **One serialization boundary.** `PipelineResult.to_json/from_json` is the
  contract between the remote worker and the local importer.
- **Single active recording.** The server holds one `MeetingSession`; live
  events fan out to all WebSocket clients via a thread-safe queue.

## Data & storage

Everything lives under `MEETINGSCRIBE_DATA_DIR` (default `C:\cornell\meetingnotes`):
`meetingscribe.db` (SQLite, WAL), `recordings/<id>/` (WAVs), `notes/<id>.md`,
`models/` (downloaded weights). Cross-meeting search uses FTS5 with triggers.

## Adding things

- **A notes backend:** implement `summarize()` + `chat()` like the classes in
  `pipeline/notes.py`, wire it into `get_notes_backend()`.
- **A diarizer:** add a function in `pipeline/diarize.py` and a branch in
  `label_speakers()`.
- **A calendar source:** return `{id, topic, start, dur}` candidates and add it
  to `scheduler.AutoRecorder._candidates()`.

## Testing

`pytest` (70 tests) covers the pure logic and API error paths without needing
GPU/models. The full GPU pipeline is verified on the Cornell cluster
(`compute_pipeline` + pyannote) out of band.
