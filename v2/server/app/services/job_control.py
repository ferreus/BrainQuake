import subprocess
from sqlalchemy.orm import Session
from app.models import Job


class JobCancelledError(Exception):
    """Raised to unwind a running job when the user requested cancellation via
    POST /jobs/{id}/cancel. jobs_worker.run_job() catches this separately from
    a generic failure so the job's terminal state stays 'cancelled' instead of
    being overwritten to 'failed'."""


def check_cancelled(db: Session, job: Job):
    """Cooperative-cancellation checkpoint for in-process job steps (no
    subprocess to SIGTERM). Call this between expensive iterations, or after
    each progress commit; raises JobCancelledError if a cancel request landed
    on this job (from another DB session/connection) since it started."""
    db.refresh(job)
    if job.state == "cancelled":
        raise JobCancelledError(f"Job {job.id} was cancelled")


def set_running_pid(db: Session, job: Job, pid: int):
    job.pid = pid
    db.commit()


def clear_running_pid(db: Session, job: Job):
    job.pid = None
    db.commit()


def run_and_track_subprocess(cmd, job: Job, db: Session, stdout=None, stderr=None,
                              executable=None, shell=True, text=False):
    """Runs `cmd` via Popen, recording the real child pid onto the job row so
    POST /jobs/{id}/cancel's SIGTERM reaches the actual subprocess instead of
    the worker process itself (the previous behavior -- job.pid = os.getpid()
    in jobs_worker.run_job() -- meant cancelling any job SIGTERM'd the whole
    worker, killing every other queued/running job with it).

    Returns an object with .returncode (and .stdout/.stderr when stdout/stderr
    is subprocess.PIPE). If the process died with a nonzero return code
    *because* the job was cancelled while it was running, raises
    JobCancelledError instead of surfacing it as an ordinary crash.
    """
    proc = subprocess.Popen(cmd, shell=shell, executable=executable, stdout=stdout, stderr=stderr, text=text)
    set_running_pid(db, job, proc.pid)
    try:
        out, err = proc.communicate()
    finally:
        clear_running_pid(db, job)

    if proc.returncode != 0:
        db.refresh(job)
        if job.state == "cancelled":
            raise JobCancelledError(f"Job {job.id} was cancelled")

    class _Result:
        pass

    result = _Result()
    result.returncode = proc.returncode
    result.stdout = out
    result.stderr = err
    return result
