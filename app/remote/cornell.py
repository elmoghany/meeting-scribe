"""Submit a meeting's batch job to the Cornell unicorn SLURM cluster over SSH.

Prereqs (one-time, see scripts/cornell_setup.sh):
  * Cornell VPN up (the `login` skill handles WARP→AnyConnect→SSH).
  * Passwordless ed25519 key installed for me484@unicorn-login (already done).
  * Project code synced to $CORNELL_REMOTE_DIR with a conda env `mscribe`.

Per meeting we: SFTP the WAVs up → write+submit an sbatch script → poll squeue →
download the result JSON. Returns a PipelineResult ready for persist_result().

GPU/resources are configurable via env (CORNELL_GRES, CORNELL_PARTITION,
CORNELL_MEM, CORNELL_CPUS, CORNELL_TIME). gres needs the full SLURM model name,
e.g. gpu:nvidia_geforce_rtx_3090:1 (run ~/unicorn-gpus.sh to see what's free).
"""
from __future__ import annotations

import json
import os
import posixpath
import time

from ..config import get_settings
from ..pipeline.process import PipelineResult

_SBATCH = """#!/bin/bash
#SBATCH -J mscribe-{mid}
#SBATCH -p {partition}
#SBATCH --gres={gres}
#SBATCH --cpus-per-task={cpus}
#SBATCH --mem={mem}
#SBATCH --time={time}
#SBATCH -o {remote_dir}/out/{mid}/slurm.log
set -euo pipefail
source ~/miniconda3/etc/profile.d/conda.sh
conda activate mscribe
cd {remote_dir}
export HUGGINGFACE_TOKEN="{hf_token}"
export MEETINGSCRIBE_LLM_BACKEND="{llm_backend}"
export MEETINGSCRIBE_DIARIZER="{diarizer}"
export MEETINGSCRIBE_PYANNOTE_MODEL="{pyannote_model}"
export MEETINGSCRIBE_DATA_DIR="{remote_dir}/data"
python -m app.remote.worker "{remote_dir}/recordings/{mid}" \\
    "{remote_dir}/out/{mid}/result.json" "{batch_model}"
echo "MSCRIBE_DONE"
"""


def _connect():
    import paramiko  # noqa: PLC0415

    s = get_settings()
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    pw = os.getenv("CORNELL_SSH_PASSWORD") or None
    cli.connect(
        s.cornell_host, username=s.cornell_user, password=pw,
        look_for_keys=True, allow_agent=True, timeout=25,
    )
    return cli


def _run(cli, cmd: str, timeout: int = 120) -> tuple[int, str, str]:
    _in, out, err = cli.exec_command(f"bash -lc {json.dumps(cmd)}", timeout=timeout)
    rc = out.channel.recv_exit_status()
    return rc, out.read().decode(errors="replace"), err.read().decode(errors="replace")


def submit_and_fetch(meeting_id: str, audio_dir: str, batch_model: str | None = None,
                     poll_sec: int = 15, max_wait_sec: int = 3600,
                     progress=print) -> PipelineResult:
    s = get_settings()
    remote_dir = s.cornell_remote_dir
    mid = meeting_id
    rrec = posixpath.join(remote_dir, "recordings", mid)
    rout = posixpath.join(remote_dir, "out", mid)

    cli = _connect()
    try:
        sftp = cli.open_sftp()
        for sub in (rrec, rout):
            _run(cli, f"mkdir -p {sub}")
        # Upload audio
        for name in ("mic.wav", "system.wav"):
            local = os.path.join(audio_dir, name)
            if os.path.exists(local):
                progress(f"[cornell] uploading {name} ...")
                sftp.put(local, posixpath.join(rrec, name))

        # Write + submit the sbatch script
        script = _SBATCH.format(
            mid=mid, remote_dir=remote_dir,
            partition=os.getenv("CORNELL_PARTITION", "gpu"),
            gres=os.getenv("CORNELL_GRES", "gpu:1"),
            cpus=os.getenv("CORNELL_CPUS", "8"),
            mem=os.getenv("CORNELL_MEM", "32g"),
            time=os.getenv("CORNELL_TIME", "2:00:00"),
            hf_token=s.hf_token or "",
            llm_backend=os.getenv("CORNELL_LLM_BACKEND", "transformers"),
            diarizer=os.getenv("CORNELL_DIARIZER", s.diarizer),
            pyannote_model=os.getenv("MEETINGSCRIBE_PYANNOTE_MODEL",
                                     "pyannote/speaker-diarization-community-1"),
            batch_model=batch_model or s.batch_model,
        )
        rscript = posixpath.join(rout, "job.sbatch")
        with sftp.open(rscript, "w") as f:
            f.write(script)

        rc, sout, serr = _run(cli, f"sbatch --parsable {rscript}")
        if rc != 0:
            raise RuntimeError(f"sbatch failed: {serr or sout}")
        job_id = sout.strip().split(";")[0]
        progress(f"[cornell] submitted SLURM job {job_id}; polling ...")

        # Poll until the job leaves the queue
        deadline = time.time() + max_wait_sec
        while time.time() < deadline:
            _rc, st, _ = _run(cli, f"squeue -h -j {job_id} -o %T")
            state = st.strip()
            if not state:  # gone from queue → finished (or failed)
                break
            progress(f"[cornell] job {job_id}: {state}")
            time.sleep(poll_sec)
        else:
            raise TimeoutError(f"job {job_id} did not finish within {max_wait_sec}s")

        # Fetch result
        rjson = posixpath.join(rout, "result.json")
        rc, _o, _e = _run(cli, f"test -f {rjson}")
        if rc != 0:
            _rc, log, _ = _run(cli, f"tail -n 40 {posixpath.join(rout, 'slurm.log')}")
            raise RuntimeError(f"remote job produced no result.json. Log tail:\n{log}")
        with sftp.open(rjson, "r") as f:
            data = json.loads(f.read().decode())
        progress("[cornell] downloaded result.json")
        return PipelineResult.from_json(data)
    finally:
        cli.close()


def process_remote(meeting_id: str, audio_dir: str, batch_model: str | None = None,
                   progress=print):
    """Convenience: submit on Cornell, fetch, and persist into the local DB."""
    from ..pipeline.process import persist_result

    res = submit_and_fetch(meeting_id, audio_dir, batch_model=batch_model,
                           progress=progress)
    persist_result(meeting_id, res)
    return {"segments": len(res.segments), "action_items": len(res.action_items),
            "language": res.language, "backend": res.backend, "via": "cornell"}
