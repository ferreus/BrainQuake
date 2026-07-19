from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import Subject, Job
from app.schemas import JobResponse

router = APIRouter(prefix="/subjects", tags=["electrodes"])


@router.post("/{subject_id}/electrodes/register-ct", response_model=JobResponse)
def register_ct(subject_id: int, db: Session = Depends(get_db)):
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")

    # Check if there is already an active job (queued or running) of this type
    active_job = db.query(Job).filter(
        Job.subject_id == subject_id,
        Job.job_type == "ct_register",
        Job.state.in_(["queued", "running"])
    ).first()

    if active_job:
        raise HTTPException(status_code=400, detail="A CT registration job is already in progress for this subject")

    # Create the job
    job = Job(
        subject_id=subject.id,
        job_type="ct_register",
        state="queued",
        params_json={},
        progress_pct=0.0,
        progress_message="Job queued"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job
