import os
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import Subject, Job, Artifact
from app.schemas import JobResponse, ArtifactResponse

router = APIRouter(prefix="/subjects", tags=["recon"])

class ReconRequest(BaseModel):
    recon_type: Optional[str] = "recon-all"  # recon-all, fast-surfer, infant-surfer
    # Required for infant-surfer: age at scan, in months. infant_recon_all picks
    # its age-specific atlas/template from this -- see
    # https://surfer.nmr.mgh.harvard.edu/fswiki/infantFS ("--age" section). The
    # tool will run without it (falling back to volume-based age estimation) but
    # the wiki recommends always passing it explicitly, so we require it here.
    age_months: Optional[float] = None

@router.post("/{subject_id}/recon", response_model=JobResponse)
def run_recon(
    subject_id: int,
    request: ReconRequest,
    db: Session = Depends(get_db)
):
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")

    if request.recon_type not in ["recon-all", "fast-surfer", "infant-surfer"]:
        raise HTTPException(status_code=400, detail="Invalid recon_type")

    if request.recon_type == "infant-surfer":
        if request.age_months is None:
            raise HTTPException(status_code=400, detail="age_months is required for infant-surfer recon_type")
        if not (0 <= request.age_months <= 60):
            raise HTTPException(status_code=400, detail="age_months must be between 0 and 60")

    # Check if there is already an active job (queued or running) of this type
    active_job = db.query(Job).filter(
        Job.subject_id == subject_id,
        Job.job_type == "recon",
        Job.state.in_(["queued", "running"])
    ).first()

    if active_job:
        raise HTTPException(status_code=400, detail="A reconstruction job is already in progress for this subject")

    # Update subject's recon_type
    subject.recon_type = request.recon_type
    db.add(subject)

    # Create the job
    params_json = {"recon_type": request.recon_type}
    if request.recon_type == "infant-surfer":
        params_json["age_months"] = request.age_months

    job = Job(
        subject_id=subject.id,
        job_type="recon",
        state="queued",
        params_json=params_json,
        progress_pct=0.0,
        progress_message="Job queued"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("/{subject_id}/recon/result", response_model=List[ArtifactResponse])
def get_recon_result(subject_id: int, db: Session = Depends(get_db)):
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    job = (
        db.query(Job)
        .filter(Job.subject_id == subject_id, Job.job_type == "recon", Job.state == "finished")
        .order_by(Job.created_at.desc())
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="No finished reconstruction found for this subject")
    return db.query(Artifact).filter(Artifact.job_id == job.id).all()
