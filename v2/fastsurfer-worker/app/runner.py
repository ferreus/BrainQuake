import os
import subprocess
import threading
from dataclasses import dataclass
from typing import Dict, Optional

LOG_DIR = os.environ.get("FASTSURFER_LOG_DIR", "/tmp/fastsurfer-jobs")
os.makedirs(LOG_DIR, exist_ok=True)

# Verify this path and the flag names below against the real deepmi/fastsurfer
# image before deploying -- carried over from FastSurfer's documented CLI
# shape, not confirmed against the actual image contents.
FASTSURFER_ENTRYPOINT = os.environ.get("FASTSURFER_ENTRYPOINT", "/fastsurfer/run_fastsurfer.sh")


class JobNotFound(Exception):
    pass


class JobAlreadyExists(Exception):
    pass


class AtCapacity(Exception):
    pass


@dataclass
class JobRecord:
    proc: subprocess.Popen
    log_path: str
    log_fh: object


class JobRunner:
    """Tracks FastSurfer subprocesses in-memory, keyed by the caller's own
    job id. No persistence -- a container restart loses all tracked jobs,
    which the main worker detects as a 404 on its next status poll and turns
    into a clean job failure (see server/app/services/fastsurfer_client.py).
    """

    def __init__(self, max_concurrent: int):
        self.max_concurrent = max_concurrent
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    def _running_count_locked(self) -> int:
        return sum(1 for r in self._jobs.values() if r.proc.poll() is None)

    def start(self, job_id: str, t1_path: str, sid: str, sd: str, license_path: str,
              threads: int, device: str) -> None:
        with self._lock:
            if job_id in self._jobs:
                raise JobAlreadyExists(job_id)
            if self._running_count_locked() >= self.max_concurrent:
                raise AtCapacity()

            log_path = os.path.join(LOG_DIR, f"{job_id}.log")
            log_fh = open(log_path, "w")
            # --parallel and --surfreg were dropped after checking the real
            # run_fastsurfer.sh (Deep-MI/FastSurfer, stable branch):
            # --surfreg doesn't exist as a flag at all -- surface
            # registration already runs by default, only --no_surfreg turns
            # it off; --parallel is deprecated ("obsolete, will be removed
            # in FastSurfer 3") and unnecessary since --threads alone
            # already triggers parallel hemisphere processing.
            # --allow_root: this container runs as root (see
            # fastsurfer-worker.Dockerfile) to match how api/worker already
            # run elsewhere in this repo, rather than mapping a per-service
            # non-root UID/GID just for FastSurfer -- without this flag,
            # run_fastsurfer.sh's own check_allow_root refuses to run as
            # root at all.
            cmd = [
                FASTSURFER_ENTRYPOINT,
                "--t1", t1_path,
                "--sid", sid,
                "--sd", sd,
                "--fs_license", license_path,
                "--threads", str(threads),
                "--device", device,
                "--py", "python3",
                "--allow_root",
            ]
            proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT)
            self._jobs[job_id] = JobRecord(proc=proc, log_path=log_path, log_fh=log_fh)

    def status(self, job_id: str) -> dict:
        with self._lock:
            record = self._jobs.get(job_id)
        if record is None:
            raise JobNotFound(job_id)

        returncode = record.proc.poll()
        if returncode is None:
            return {"state": "running", "progress_message": self._tail(record.log_path)}

        if not record.log_fh.closed:
            record.log_fh.close()

        if returncode == 0:
            return {"state": "finished"}
        return {"state": "failed", "returncode": returncode}

    def log(self, job_id: str) -> str:
        with self._lock:
            record = self._jobs.get(job_id)
        if record is None:
            raise JobNotFound(job_id)
        try:
            with open(record.log_path) as f:
                return f.read()
        except OSError:
            return ""

    def cancel(self, job_id: str) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
        if record is None:
            raise JobNotFound(job_id)
        if record.proc.poll() is None:
            record.proc.terminate()

    @staticmethod
    def _tail(log_path: str) -> Optional[str]:
        try:
            with open(log_path) as f:
                lines = [line.strip() for line in f if line.strip()]
        except OSError:
            return None
        return lines[-1] if lines else None
