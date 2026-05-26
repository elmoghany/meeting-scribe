#!/usr/bin/env bash
# One-time setup of the MeetingScribe batch pipeline on the Cornell unicorn cluster.
# Run this FROM your local machine (Git Bash) with the VPN up. It rsyncs the code
# to $CORNELL_REMOTE_DIR and builds a conda env `mscribe` with the GPU ML deps.
#
#   bash scripts/cornell_setup.sh
#
# Env (override as needed): CORNELL_USER, CORNELL_LOGIN_HOST, CORNELL_REMOTE_DIR
set -euo pipefail

USER_="${CORNELL_USER:-me484}"
HOST="${CORNELL_LOGIN_HOST:-unicorn-login-04.coecis.cornell.edu}"
REMOTE_DIR="${CORNELL_REMOTE_DIR:-/home/me484/meetingnotes}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> syncing code to ${USER_}@${HOST}:${REMOTE_DIR}"
ssh -o BatchMode=yes "${USER_}@${HOST}" "mkdir -p '${REMOTE_DIR}'"
# rsync the source (skip venvs, data, caches)
rsync -az --delete \
  --exclude '.venv' --exclude 'data' --exclude '__pycache__' \
  --exclude '.pytest_cache' --exclude '*.wav' --exclude '.git' \
  "${HERE}/" "${USER_}@${HOST}:${REMOTE_DIR}/"

echo "==> building conda env 'mscribe' on the cluster"
ssh -o BatchMode=yes "${USER_}@${HOST}" bash -lc "'
set -euo pipefail
source ~/miniconda3/etc/profile.d/conda.sh
if ! conda env list | grep -q \"^mscribe \"; then
  conda create -y -n mscribe python=3.11
fi
conda activate mscribe
cd ${REMOTE_DIR}
# torch matched to the cluster CUDA (cu121 wheels work on the unicorn GPUs)
pip install --quiet torch torchaudio --index-url https://download.pytorch.org/whl/cu121 || pip install --quiet torch torchaudio
pip install --quiet faster-whisper \"pyannote.audio>=3.1\" transformers accelerate \
  soundfile numpy huggingface-hub sentencepiece
echo \"mscribe env ready on \$(hostname)\"
conda run -n mscribe python -c \"import torch; print(\\\"torch\\\", torch.__version__, \\\"cuda\\\", torch.cuda.is_available())\"
'"

echo "==> done. Set MEETINGSCRIBE_REMOTE=1 in your .env to route batch jobs here."
echo "    Pick a GPU with ~/unicorn-gpus.sh and set CORNELL_GRES (e.g. gpu:nvidia_geforce_rtx_3090:1)."
