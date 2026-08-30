import logging
import os
import socket
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import update

from app.config import settings
from app.db import SessionLocal
from app.models import Job
from app.services.ct_register import run_ct_register_job
from app.services.electrodes import (
    run_elec_detect_job,
    run_elec_import_job,
    run_elec_segment_job,
    run_slicer_mrb_parse_job,
)
from app.services.fragility import run_fragility_compute_job
from app.services.ictal import run_ei_compute_job
from app.services.interictal import run_hfo_compute_job
from app.services.job_control import JobCancelledError
from app.services.subject_io import run_export_subject_job, run_import_subject_job
from app.services.recon import run_recon_job
from app.services.soz import run_soz_fuse_job
from app.services.surface import run_surface_export_job

POLL_INTERVAL_SECONDS = 2
_POLL_PAGE_SIZE = 200

# job_type -> handler. A registry rather than an if/elif chain so the set of
# valid job types is introspectable: routers create jobs by writing this string
# into a row, and a typo there previously produced a job that queued happily and
# then failed only once a worker picked it up. Tests substitute handlers with
# patch.dict on this mapping.
JOB_HANDLERS = {
    "recon": run_recon_job,
    "ct_register": run_ct_register_job,
    "elec_detect": run_elec_detect_job,
    "elec_segment": run_elec_segment_job,
    "elec_import": run_elec_import_job,
    "slicer_mrb_parse": run_slicer_mrb_parse_job,
    "ei_compute": run_ei_compute_job,
    "hfo_compute": run_hfo_compute_job,
    "fragility_compute": run_fragility_compute_job,
    "soz_fuse": run_soz_fuse_job,
    "surface_export": run_surface_export_job,
    "export_subject": run_export_subject_job,
    "import_subject": run_import_subject_job,
}

# Set up logging for the worker itself
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("jobs_worker")

def cleanup_stale_jobs():
    """Fail jobs left in the 'running' state by a previous worker run.

    FIXME(correctness): this fails *every* running job, with no way to tell a
    crashed worker's zombies from another worker's live work. One worker
    container is the current deployment (docker-compose), so it is safe today,
    but starting a second worker -- or scaling the service -- would mark
    healthy in-flight jobs as failed the moment it booted. The `host` column is
    recorded but not consulted, and would not settle it either since two
    workers can share a host. A real fix needs a worker identity plus a
    heartbeat or lease, so only jobs whose owner has stopped renewing are
    reaped.
    """
    db = SessionLocal()
    try:
        stale_jobs = db.query(Job).filter(Job.state == "running").all()
        for job in stale_jobs:
            logger.info(f"Cleaning up stale running job ID {job.id} (type: {job.job_type})")
            job.state = "failed"
            job.progress_message = "Worker restarted while job was running."
            job.finished_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as e:
        logger.error(f"Error during stale jobs cleanup: {e}")
    finally:
        db.close()

def run_job(job_id: int):
    """Atomically claims job_id (no-op if it's not 'queued' anymore -- e.g.
    already claimed by a concurrent call, or cancelled before being picked
    up -- or if another job for the same subject is currently running), then
    executes it synchronously end to end. Safe to call directly (existing
    tests do this) or have it submitted by either dispatch loop's thread
    pool -- both paths go through the same atomic claim below, so a job can
    never be double-executed and two jobs for one subject can never run
    concurrently, no matter which loop (or how many) submitted it."""
    db = SessionLocal()
    job_row = db.query(Job).filter(Job.id == job_id).first()
    if not job_row:
        db.close()
        return

    logs_dir = os.path.join(settings.DATA_ROOT, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, f"job_{job_id}.log")

    # pid is left unset here -- it's populated only while a tracked
    # subprocess step is actually running (see
    # services/job_control.run_and_track_subprocess), never the worker's own
    # pid: SIGTERM'ing the worker itself on cancel would kill every other
    # queued/running job with it.
    other_running_for_subject = (
        db.query(Job.id)
        .filter(Job.subject_id == job_row.subject_id, Job.state == "running", Job.id != job_id)
        .exists()
    )
    claim = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.state == "queued", ~other_running_for_subject)
        .values(
            state="running",
            started_at=datetime.now(timezone.utc),
            pid=None,
            host=socket.gethostname(),
            log_path=log_path,
            progress_pct=0.0,
            progress_message="Starting execution",
        )
    )
    db.commit()

    if claim.rowcount != 1:
        # Lost the race to another claim, was cancelled while queued, or a
        # job for the same subject is currently running -- either way,
        # nothing for this call to do.
        db.close()
        return

    job = db.query(Job).filter(Job.id == job_id).first()

    logger.info(f"Executing job {job.id} of type '{job.job_type}' for subject ID {job.subject_id}")

    try:
        with open(log_path, "w") as log_file:
            log_file.write(f"--- Job {job.id} Started at {job.started_at} ---\n")
            log_file.write(f"Type: {job.job_type}\n")
            log_file.write(f"Subject ID: {job.subject_id}\n")
            log_file.write(f"Parameters: {job.params_json}\n\n")
            log_file.flush()

            handler = JOB_HANDLERS.get(job.job_type)
            if handler is None:
                raise ValueError(
                    f"Unknown job type {job.job_type!r}; known types are "
                    f"{sorted(JOB_HANDLERS)}"
                )
            handler(db, job, log_file)

            job.state = "finished"
            job.progress_pct = 100.0
            job.progress_message = "Job completed successfully"
            job.finished_at = datetime.now(timezone.utc)
            log_file.write(f"\n--- Job {job.id} Completed successfully at {job.finished_at} ---\n")

    except JobCancelledError as e:
        logger.info(f"Job {job.id} cancelled: {e}")

        # Reload job in case session was closed/messed up
        db.rollback()
        job = db.query(Job).filter(Job.id == job_id).first()
        if job is None:
            logger.error(f"Job {job_id} vanished while being cancelled")
            db.close()
            return
        job.state = "cancelled"
        job.progress_message = "Job cancelled by user"
        job.finished_at = datetime.now(timezone.utc)

        try:
            with open(log_path, "a") as log_file:
                log_file.write(f"\n--- Job {job.id} Cancelled at {job.finished_at} ---\n")
        except OSError as log_err:
            logger.warning(f"could not append cancellation note to {log_path}: {log_err}")

    except Exception as e:
        logger.error(f"Job {job.id} failed with error: {e}")
        logger.error(traceback.format_exc())

        # Reload job in case session was closed/messed up
        db.rollback()
        job = db.query(Job).filter(Job.id == job_id).first()
        if job is None:
            logger.error(f"Job {job_id} vanished while failing: {e}")
            db.close()
            return
        job.state = "failed"
        job.progress_message = f"Error: {str(e)}"
        job.finished_at = datetime.now(timezone.utc)

        # Append error stacktrace to the job log
        try:
            with open(log_path, "a") as log_file:
                log_file.write(f"\n--- Job {job.id} Failed at {job.finished_at} ---\n")
                log_file.write(traceback.format_exc())
        except OSError as log_err:
            logger.warning(f"could not append traceback to {log_path}: {log_err}")

    finally:
        db.commit()
        db.close()

def _is_fastsurfer_job(job: Job) -> bool:
    return job.job_type == "recon" and (job.params_json or {}).get("recon_type") == "fast-surfer"


def _dispatch_loop(loop_name: str, job_predicate, pool_size: int):
    """Generic poll-claim-submit loop. job_predicate(job) -> bool selects
    which queued jobs this loop is responsible for. Two independent
    instances of this loop run concurrently in the same process (see
    worker_loop): one for every job type except fast-surfer recon jobs, one
    exclusively for fast-surfer recon jobs -- so a multi-hour FastSurfer run
    never occupies a slot in the general pool, and the general dispatcher
    never claims a fast-surfer job at all. Per-subject mutual exclusion is
    enforced entirely by run_job's own atomic claim, not by anything here,
    so it's safe for the two loops to be completely unaware of each other.
    """
    executor = ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix=loop_name)
    in_flight: set[int] = set()
    lock = threading.Lock()

    def _on_done(job_id: int, fut):
        with lock:
            in_flight.discard(job_id)
        exc = fut.exception()
        if exc is not None:
            logger.error(f"[{loop_name}] job {job_id} thread raised unhandled exception: {exc}")

    while True:
        with lock:
            capacity = pool_size - len(in_flight)
            already_submitted = set(in_flight)

        if capacity > 0:
            db = SessionLocal()
            try:
                # Paged rather than a single limited query: this loop only takes
                # jobs matching its predicate, and the predicate depends on
                # params_json, so it cannot be pushed into SQL. With a fixed
                # LIMIT, enough queued jobs belonging to the *other* loop sitting
                # at the head of the queue would fill every page and this loop
                # would never see its own jobs at all -- starving them for as
                # long as the others stayed queued.
                #
                # already_submitted jobs still show up here (they're 'running'
                # by now, or momentarily still 'queued' until their thread gets
                # to run_job's atomic claim) -- filtered out below rather than
                # in SQL, since it's small in-memory state, not a DB column.
                offset = 0
                while capacity > 0:
                    page = (
                        db.query(Job)
                        .filter(Job.state == "queued")
                        .order_by(Job.created_at.asc())
                        .offset(offset)
                        .limit(_POLL_PAGE_SIZE)
                        .all()
                    )
                    if not page:
                        break
                    offset += len(page)
                    for job in page:
                        if capacity <= 0:
                            break
                        if job.id in already_submitted or not job_predicate(job):
                            continue
                        with lock:
                            in_flight.add(job.id)
                        fut = executor.submit(run_job, job.id)
                        fut.add_done_callback(lambda f, jid=job.id: _on_done(jid, f))
                        already_submitted.add(job.id)
                        capacity -= 1
                    if len(page) < _POLL_PAGE_SIZE:
                        break
            except Exception as e:
                logger.error(f"[{loop_name}] error polling for queued jobs: {e}")
            finally:
                db.close()

        time.sleep(POLL_INTERVAL_SECONDS)


def worker_loop():
    logger.info(
        f"Starting jobs worker loop (MAX_CONCURRENT_JOBS={settings.MAX_CONCURRENT_JOBS}, "
        f"FASTSURFER_MAX_CONCURRENT={settings.FASTSURFER_MAX_CONCURRENT})"
    )
    cleanup_stale_jobs()

    fastsurfer_thread = threading.Thread(
        target=_dispatch_loop,
        args=("fastsurfer-dispatcher", _is_fastsurfer_job, settings.FASTSURFER_MAX_CONCURRENT),
        daemon=True,
    )
    fastsurfer_thread.start()

    # General dispatcher runs on the main thread; never claims fast-surfer jobs.
    _dispatch_loop("general-dispatcher", lambda job: not _is_fastsurfer_job(job), settings.MAX_CONCURRENT_JOBS)

if __name__ == "__main__":
    # Add project root to sys.path so we can import app.*
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    worker_loop()
