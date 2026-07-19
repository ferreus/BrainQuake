import os
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import Job
from app.schemas import JobResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])

@router.get("", response_model=List[JobResponse])
def list_jobs(
    subject_id: Optional[int] = None,
    state: Optional[str] = None,
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
    return FileResponse(job.log_path, media_type="text/plain")

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
        except Exception as e:
            # We can log this
            pass
            
    db.commit()
    db.refresh(job)
    return {"message": "Job cancelled", "job": JobResponse.model_validate(job)}
