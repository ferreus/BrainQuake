import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Job
from app.schemas import JobResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])

@router.get("", response_model=list[JobResponse])
def list_jobs(
    subject_id: int | None = None,
    state: str | None = None,
    db: Session = Depends(get_db)
):
    query = db.query(Job)
    if subject_id is not None:
        query = query.filter(Job.subject_id == subject_id)
    if state is not None:
        query = query.filter(Job.state == state)
    return query.all()

@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@router.get("/{job_id}/log")
def get_job_log(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.log_path or not os.path.exists(job.log_path):
        raise HTTPException(status_code=404, detail="Log file not found or not yet created")
    # FileResponse stats the file and sets Last-Modified/ETag, which is
    # enough for the browser to heuristically cache and reuse a stale copy
    # across the log viewer's poll interval while the worker keeps
    # appending -- explicitly opt this endpoint out of that.
    return FileResponse(
        job.log_path,
        media_type="text/plain",
        headers={"Cache-Control": "no-store"},
    )

@router.delete("/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.state not in ["finished", "failed", "cancelled"]:
        raise HTTPException(status_code=409, detail="Cannot delete a queued or running job; cancel it first")

    if job.log_path and os.path.exists(job.log_path):
        try:
            os.remove(job.log_path)
        except OSError:
            pass  # orphaned log file is not worth failing the delete

    db.delete(job)
    db.commit()
    return {"message": "Job deleted"}

@router.post("/{job_id}/cancel")
def cancel_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.state in ["finished", "failed"]:
        raise HTTPException(status_code=400, detail="Cannot cancel a completed job")

    # Update job state
    job.state = "cancelled"

    # If the job is running, try to terminate its process
    if job.pid:
        try:
            import signal
            os.kill(job.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass  # Process already finished
        except Exception:
            # We can log this
            pass

    db.commit()
    db.refresh(job)
    return {"message": "Job cancelled", "job": JobResponse.model_validate(job)}
