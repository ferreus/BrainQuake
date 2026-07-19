import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_db
from app.config import settings
from app.models import Subject, Job, Artifact
from app.schemas import JobResponse
from app.services import ictal as ictal_service

router = APIRouter(prefix="/subjects", tags=["ictal"])


def _get_subject_or_404(subject_id: int, db: Session) -> Subject:
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    return subject


class EiRequest(BaseModel):
    baseline_start: float  # seconds
    baseline_end: float
    target_start: float
    target_end: float
    band_low: float = 1.0  # Hz, bandpass filter applied before EI computation
    band_high: float = 500.0


@router.post("/{subject_id}/ictal/{edf_artifact_id}/ei", response_model=JobResponse)
def compute_ei(subject_id: int, edf_artifact_id: int, request: EiRequest, db: Session = Depends(get_db)):
    subject = _get_subject_or_404(subject_id, db)
    artifact = db.query(Artifact).filter(Artifact.id == edf_artifact_id, Artifact.subject_id == subject_id).first()
    if not artifact:
        raise HTTPException(status_code=404, detail="edf artifact not found for this subject")

    active_job = db.query(Job).filter(
        Job.subject_id == subject_id,
        Job.job_type == "ei_compute",
        Job.state.in_(["queued", "running"])
    ).first()
    if active_job:
        raise HTTPException(status_code=400, detail="An EI computation job is already in progress for this subject")

    job = Job(
        subject_id=subject.id,
        job_type="ei_compute",
        state="queued",
        params_json={"edf_artifact_id": edf_artifact_id, **request.model_dump()},
        progress_pct=0.0,
        progress_message="Job queued"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("/{subject_id}/ictal/{edf_artifact_id}/ei-result")
def get_ei_result(subject_id: int, edf_artifact_id: int, db: Session = Depends(get_db)):
    _get_subject_or_404(subject_id, db)
    job = (
        db.query(Job)
        .filter(
            Job.subject_id == subject_id,
            Job.job_type == "ei_compute",
            Job.state == "finished",
        )
        .order_by(Job.created_at.desc())
        .all()
    )
    job = next((j for j in job if (j.params_json or {}).get("edf_artifact_id") == edf_artifact_id), None)
    if not job:
        raise HTTPException(status_code=404, detail="No finished EI computation found for this edf")

    artifact = (
        db.query(Artifact)
        .filter(Artifact.job_id == job.id, Artifact.kind == "ei_npz")
        .first()
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="EI result artifact not found")

    abs_path = os.path.join(settings.DATA_ROOT, artifact.rel_path)
    return ictal_service.load_ei_result(abs_path)
