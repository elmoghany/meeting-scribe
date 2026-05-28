# MeetingScribe headless join-bot

A Zoom Linux Meeting SDK participant that joins meetings without a human, captures
the mixed audio stream, and feeds it to MeetingScribe's batch pipeline. This is
the only piece that needs Zoom-issued credentials (the rest of MeetingScribe is
key-free).

## What's here

- `jwt_sign.py` — mint Meeting SDK JWTs (stdlib, no deps; unit-tested).
- `runner.py` — Python orchestrator that spawns the bot binary (or its Docker
  image) and returns the captured WAV path.
- `main.cpp` — the C++ bot that links against Zoom's Linux SDK.
- `CMakeLists.txt` — build the binary against an unpacked SDK.
- `Dockerfile` — reproducible build container (Ubuntu 22.04 + SDK + bot).

## One-time setup

### 1. Create a Zoom Meeting SDK app
1. Go to https://marketplace.zoom.us/develop/create.
2. **"General App" → enable "Meeting SDK"**.
3. From the app's *App Credentials* page, copy the **Client ID (SDK Key)** and
   **Client Secret (SDK Secret)** — these are different from the OAuth app's
   credentials.
4. Add `ZOOM_SDK_KEY=...` and `ZOOM_SDK_SECRET=...` to your `.env`.

> Audio raw-data: in your Zoom account *Settings → Recording*, enable
> **"Local recording"** and **"Allow apps to access raw video/audio"** (the SDK
> can't read audio frames without this; the SDK call succeeds silently with no
> audio otherwise).

### 2. Download the Linux Meeting SDK
Zoom doesn't permit redistribution, so this is manual:
1. https://developers.zoom.us/docs/meeting-sdk/linux/ → **Download**.
2. Unpack the zip to `bot/sdk/` so that `bot/sdk/h/zoom_sdk.h` and
   `bot/sdk/libmeetingsdk.so` exist.

### 3. Build
**Docker (recommended)** — reproducible across machines, including the Cornell
cluster (use apptainer/singularity equivalently):
```bash
cd bot && docker build -t meetingscribe-bot -f Dockerfile .
echo "MEETINGSCRIBE_BOT_DOCKER_IMAGE=meetingscribe-bot" >> ../.env
```

**Native build** — directly on Ubuntu 22.04 (or compatible):
```bash
cd bot && cmake -S . -B build -DZOOMSDK_ROOT=$PWD/sdk
cmake --build build -j
# add bot/build to your PATH, or:
echo "MEETINGSCRIBE_BOT_BINARY=$PWD/build/meetingscribe-bot" >> ../.env
```

### 4. Enable the bot
In `.env`:
```
MEETINGSCRIBE_BOT_ENABLED=1
MEETINGSCRIBE_BOT_NAME=MeetingScribe Bot
MEETINGSCRIBE_BOT_MAX_SEC=14400      # 4h cap per meeting
```

## Use it

From Python:
```python
from bot.runner import run_bot
info = run_bot(meeting_number="1234567890", passcode="abc",
               meeting_id="2026-05-26-1530-demo", name="MeetingScribe Bot",
               join_url="https://us05web.zoom.us/j/123...?pwd=...")
# info["audio"] is recordings/<meeting_id>/system.wav
```

Then run MeetingScribe's batch pipeline on it:
```python
from app.batch import run_batch
run_batch(info["meeting_id"], info["audio_dir"])
```

## Scheduler integration (auto-join)

`app.scheduler.AutoRecorder` already pulls upcoming Zoom meetings (via OAuth)
and `.ics` events. To make those auto-recorded **without a human present**, wire
`bot.runner.run_bot` into the candidate dispatch:

```python
# In scheduler when a Zoom candidate fires and s.bot_enabled:
if s.bot_enabled and c.get("join_url"):
    info = run_bot(meeting_number=parse_zoom_id(c["join_url"]),
                   passcode=parse_pwd(c["join_url"]),
                   meeting_id=new_id,
                   join_url=c["join_url"])
    run_batch(info["meeting_id"], info["audio_dir"])
```

(Left as a small wiring change so the bot is opt-in and doesn't affect anyone
who hasn't built the SDK.)

## How it works

```
runner.run_bot()
  ├─ jwt_sign.sign_meeting_sdk_jwt(SDK_KEY, SDK_SECRET, meeting_no)
  ├─ spawn  meetingscribe-bot --meeting N --passcode P --jwt T --audio-out W
  │        │
  │        ▼  (in the Docker image / native binary)
  │  InitSDK() → IAuthService::SDKAuth(jwt)
  │  IAuthServiceEvent::onAuthenticationReturn  →  IMeetingService::Join()
  │  IMeetingServiceEvent::onMeetingStatusChanged(INMEETING)
  │     → GetAudioRawdataHelper()->subscribe(AudioDelegate)
  │  AudioDelegate::onMixedAudioRawDataReceived(AudioRawData*)
  │     → WavWriter::write(PCM16 @ 32 kHz mono)
  │  IMeetingServiceEvent::onMeetingStatusChanged(ENDED|FAILED) → exit 0
  │
  ▼
{audio_dir, audio: system.wav}  →  app.batch.run_batch()  → notes
```

## Caveats

- Some symbol names in the Zoom SDK (e.g. `IZoomSDKAudioRawDataDelegate` ↔
  `IZoomSDKRawAudioRecordingDelegate`) shift between SDK versions. If the
  compile fails on a name, grep `bot/sdk/h/` for the equivalent and adjust
  `main.cpp` — the structure is the same.
- Zoom can detect headless clients and require an account-level approval; for
  internal use this is usually fine.
- The bot SDK requires the host account to have "Allow apps to access raw
  video/audio" enabled (see setup step 1).
