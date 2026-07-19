import os
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_db
from app.config import settings
from app.models import Subject, Job, Artifact
from app.schemas import JobResponse
from app.services import interictal as interictal_service

router = APIRouter(prefix="/subjects", tags=["interictal"])


def _get_subject_or_404(subject_id: int, db: Session) -> Subject:
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    return subject


class HfoRequest(BaseModel):
    band_low: float = 80.0  # Hz, HFO/ripple band
    band_high: float = 250.0
    rel_thresh: float = 2.0  # envelope must exceed rel_thresh * its own channel median
    abs_thresh: float = 2.0  # and abs_thresh * the whole-recording median
    min_gap: float = 20.0  # ms, merge high-envelope segments closer together than this
    min_last: float = 50.0  # ms, minimum event duration to count
    remain_chns: Optional[List[str]] = None  # channel names to include; default: all


@router.post("/{subject_id}/interictal/{edf_artifact_id}/hfo", response_model=JobResponse)
def compute_hfo(subject_id: int, edf_artifact_id: int, request: HfoRequest = HfoRequest(), db: Session = Depends(get_db)):
    subject = _get_subject_or_404(subject_id, db)
    artifact = db.query(Artifact).filter(Artifact.id == edf_artifact_id, Artifact.subject_id == subject_id).first()
    if not artifact:
        raise HTTPException(status_code=404, detail="edf artifact not found for this subject")

    active_job = db.query(Job).filter(
        Job.subject_id == subject_id,
        Job.job_type == "hfo_compute",
        Job.state.in_(["queued", "running"])
    ).first()
    if active_job:
        raise HTTPException(status_code=400, detail="An HFO computation job is already in progress for this subject")

    job = Job(
        subject_id=subject.id,
        job_type="hfo_compute",
        state="queued",
        params_json={"edf_artifact_id": edf_artifact_id, **request.model_dump()},
        progress_pct=0.0,
        progress_message="Job queued"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("/{subject_id}/interictal/{edf_artifact_id}/hfo-result")
def get_hfo_result(subject_id: int, edf_artifact_id: int, db: Session = Depends(get_db)):
    _get_subject_or_404(subject_id, db)
    jobs = (
        db.query(Job)
        .filter(
            Job.subject_id == subject_id,
            Job.job_type == "hfo_compute",
            Job.state == "finished",
        )
        .order_by(Job.created_at.desc())
        .all()
    )
    job = next((j for j in jobs if (j.params_json or {}).get("edf_artifact_id") == edf_artifact_id), None)
    if not job:
        raise HTTPException(status_code=404, detail="No finished HFO computation found for this edf")

    artifact = (
        db.query(Artifact)
        .filter(Artifact.job_id == job.id, Artifact.kind == "hfo_npz")
        .first()
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="HFO result artifact not found")

    abs_path = os.path.join(settings.DATA_ROOT, artifact.rel_path)
    return interictal_service.load_hfo_result(abs_path)
