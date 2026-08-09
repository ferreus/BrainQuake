import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Job
from app.services.job_control import JobCancelledError

# License path baked into the fastsurfer-worker container by docker-compose.yml,
# the same fixed path api/worker already mount FS_LICENSE at -- known in
# advance rather than introspected, since the sidecar has no other way to
# learn it (it doesn't share our config/env).
_SIDECAR_LICENSE_PATH = "/usr/local/freesurfer/license.txt"


def _run_fastsurfer_via_sidecar(t1_path: str, sid: str, job: Job, db: Session, log_file) -> None:
    """Runs FastSurfer by delegating to the fastsurfer-worker sidecar over
    HTTP and polling it to completion, instead of shelling out locally (the
    worker container never has FastSurfer installed). Raises RuntimeError
    with a clear, actionable message if the feature isn't enabled or the
    sidecar isn't reachable -- never falls back to any local filesystem path.
    """
    if not settings.FASTSURFER_ENABLED:
        raise RuntimeError(
            "FastSurfer is not enabled on this server. Set FASTSURFER_ENABLED=true "
            "and ensure the fastsurfer-worker service is running."
        )

    url = settings.FASTSURFER_WORKER_URL
    payload = {
        "job_id": str(job.id),
        "t1_path": t1_path,
        "sid": sid,
        "sd": settings.SUBJECTS_DIR,
        "license_path": _SIDECAR_LICENSE_PATH,
        "threads": settings.FASTSURFER_THREADS,
        "device": settings.FASTSURFER_DEVICE,
    }

    log_file.write(f"\n[{datetime.now(timezone.utc)}] Starting step 'fast-surfer' via sidecar at {url}: {payload}\n")
    log_file.flush()

    try:
        resp = httpx.post(f"{url}/jobs", json=payload, timeout=30.0)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        raise RuntimeError(
            f"FastSurfer is enabled but the fastsurfer-worker service at {url} is not "
            f"reachable. Is it running (docker compose --profile fastsurfer up)? ({e})"
        )

    if resp.status_code == 429:
        raise RuntimeError("fastsurfer-worker is at capacity (FASTSURFER_MAX_CONCURRENT); this run will need to be retried.")
    if resp.status_code == 409:
        raise RuntimeError(f"fastsurfer-worker already has a job tracked under id {job.id}.")
    resp.raise_for_status()

    # Mirrors the sidecar's own growing log file into ours incrementally, so
    # the job panel's log viewer (which only ever reads job.log_path) shows
    # live progress instead of getting one big dump at the very end. Tracks
    # how much we've already copied since the sidecar's /log endpoint always
    # returns the full text from the start, not just what's new.
    copied_len = 0

    def _sync_sidecar_log() -> None:
        nonlocal copied_len
        log_resp = httpx.get(f"{url}/jobs/{job.id}/log", timeout=10.0)
        if log_resp.status_code == 200 and len(log_resp.text) > copied_len:
            log_file.write(log_resp.text[copied_len:])
            log_file.flush()
            copied_len = len(log_resp.text)

    while True:
        time.sleep(settings.FASTSURFER_POLL_INTERVAL_SECONDS)

        db.refresh(job)
        if job.state == "cancelled":
            try:
                httpx.delete(f"{url}/jobs/{job.id}", timeout=10.0)
            except (httpx.ConnectError, httpx.TimeoutException):
                pass  # best-effort -- the job is being cancelled either way
            raise JobCancelledError(f"Job {job.id} was cancelled")

        status_resp = httpx.get(f"{url}/jobs/{job.id}", timeout=10.0)
        if status_resp.status_code == 404:
            raise RuntimeError(
                f"FastSurfer sidecar restarted or lost track of job {job.id}; "
                "it cannot be resumed and must be retried."
            )
        status_resp.raise_for_status()
        status = status_resp.json()

        if status["state"] == "running":
            job.progress_message = status.get("progress_message", "Running FastSurfer")
            db.commit()
            _sync_sidecar_log()
            continue

        _sync_sidecar_log()

        if status["state"] == "failed":
            raise RuntimeError(f"fast-surfer step failed (sidecar returncode {status.get('returncode')})")

        return  # finished
