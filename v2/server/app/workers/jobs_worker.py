import os
import sys
import time
import socket
import logging
import traceback
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.db import SessionLocal
from app.config import settings
from app.models import Job, Subject
from app.services.recon import run_recon_job
from app.services.ct_register import run_ct_register_job
from app.services.electrodes import run_elec_detect_job, run_elec_segment_job
from app.services.ictal import run_ei_compute_job
from app.services.interictal import run_hfo_compute_job
from app.services.soz import run_soz_fuse_job
from app.services.surface import run_surface_export_job
from app.services.job_control import JobCancelledError

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
    """Fail or requeue jobs that were left in the 'running' state from a previous run."""
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
    db = SessionLocal()
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        db.close()
        return

    # Create logs directory if it doesn't exist
    logs_dir = os.path.join(settings.DATA_ROOT, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, f"job_{job.id}.log")
    
    # Update job state to running. pid is left unset here -- it's populated
    # only while a tracked subprocess step is actually running (see
    # services/job_control.run_and_track_subprocess), never the worker's own
    # pid: SIGTERM'ing the worker itself on cancel would kill every other
    # queued/running job with it.
    job.state = "running"
    job.started_at = datetime.now(timezone.utc)
    job.pid = None
    job.host = socket.gethostname()
    job.log_path = log_path
    job.progress_pct = 0.0
    job.progress_message = "Starting execution"
    db.commit()

    logger.info(f"Executing job {job.id} of type '{job.job_type}' for subject ID {job.subject_id}")
    
    try:
        with open(log_path, "w") as log_file:
            log_file.write(f"--- Job {job.id} Started at {job.started_at} ---\n")
            log_file.write(f"Type: {job.job_type}\n")
            log_file.write(f"Subject ID: {job.subject_id}\n")
            log_file.write(f"Parameters: {job.params_json}\n\n")
            log_file.flush()

            if job.job_type == "recon":
                run_recon_job(db, job, log_file)
            elif job.job_type == "ct_register":
                run_ct_register_job(db, job, log_file)
            elif job.job_type == "elec_detect":
                run_elec_detect_job(db, job, log_file)
            elif job.job_type == "elec_segment":
                run_elec_segment_job(db, job, log_file)
            elif job.job_type == "ei_compute":
                run_ei_compute_job(db, job, log_file)
            elif job.job_type == "hfo_compute":
                run_hfo_compute_job(db, job, log_file)
            elif job.job_type == "soz_fuse":
                run_soz_fuse_job(db, job, log_file)
            elif job.job_type == "surface_export":
                run_surface_export_job(db, job, log_file)
            else:
                raise ValueError(f"Unknown job type: {job.job_type}")

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
        job.state = "cancelled"
        job.progress_message = "Job cancelled by user"
        job.finished_at = datetime.now(timezone.utc)

        try:
            with open(log_path, "a") as log_file:
                log_file.write(f"\n--- Job {job.id} Cancelled at {job.finished_at} ---\n")
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Job {job.id} failed with error: {e}")
        logger.error(traceback.format_exc())
        
        # Reload job in case session was closed/messed up
        db.rollback()
        job = db.query(Job).filter(Job.id == job_id).first()
        job.state = "failed"
        job.progress_message = f"Error: {str(e)}"
        job.finished_at = datetime.now(timezone.utc)
        
        # Append error stacktrace to the job log
        try:
            with open(log_path, "a") as log_file:
                log_file.write(f"\n--- Job {job.id} Failed at {job.finished_at} ---\n")
                log_file.write(traceback.format_exc())
        except Exception:
            pass
            
    finally:
        db.commit()
        db.close()

def worker_loop():
    logger.info("Starting jobs worker loop")
    cleanup_stale_jobs()
    
    while True:
        db = SessionLocal()
        try:
            # Poll for the oldest queued job
            job = db.query(Job).filter(Job.state == "queued").order_by(Job.created_at.asc()).first()
            if job:
                # Claim job and run it
                job_id = job.id
                db.close()  # Close session while running job to avoid keeping connection open
                run_job(job_id)
            else:
                db.close()
                time.sleep(2)
        except Exception as e:
            logger.error(f"Error in worker loop: {e}")
            db.close()
            time.sleep(5)

if __name__ == "__main__":
    # Add project root to sys.path so we can import app.*
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    worker_loop()
