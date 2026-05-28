# Build container for the Zoom Meeting SDK headless bot.
#
# One-time inputs you provide (Zoom requires you to download the SDK manually
# from their developer portal — it's gated, no public mirror):
#
#   1. Download "Zoom Meeting SDK for Linux" from
#      https://developers.zoom.us/docs/meeting-sdk/linux/ → unpack to ./sdk/
#      so ./sdk/h/zoom_sdk.h and ./sdk/libmeetingsdk.so exist.
#   2. docker build -t meetingscribe-bot -f bot/Dockerfile bot/
#
# Run:
#   docker run --rm -v $PWD/recordings:/recordings \
#     -e BOT_JWT="$(python -m bot.jwt_sign ...)" \
#     meetingscribe-bot --meeting 1234 --passcode "" \
#       --jwt $BOT_JWT --audio-out /recordings/meeting.wav --max-sec 3600
FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates \
        # Zoom SDK runtime deps (per the SDK's "system requirements"):
        libxcb-shape0 libxcb-shm0 libxcb-xfixes0 libxcb-randr0 libxcb-image0 \
        libxcb-keysyms1 libxcb-xtest0 libxcb-icccm4 libxcb-xkb1 libxkbcommon-x11-0 \
        libxcb-render-util0 libxcb-cursor0 libgl1 libglib2.0-0 libfontconfig1 \
        libnspr4 libnss3 libasound2 libxslt1.1 libxcomposite1 libxdamage1 \
        libxrandr2 libxtst6 libxi6 pulseaudio \
    && rm -rf /var/lib/apt/lists/*

# Bring in the unpacked Zoom Meeting SDK + the bot source
COPY sdk/ /opt/zoom-meeting-sdk-linux/
COPY main.cpp CMakeLists.txt /src/

WORKDIR /src
RUN cmake -S . -B build -DZOOMSDK_ROOT=/opt/zoom-meeting-sdk-linux && cmake --build build -j

# Copy the SDK shared lib next to the binary so $ORIGIN rpath works
RUN cp /opt/zoom-meeting-sdk-linux/libmeetingsdk.so /src/build/

ENV LD_LIBRARY_PATH=/src/build

ENTRYPOINT ["/src/build/meetingscribe-bot"]
