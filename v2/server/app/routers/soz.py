import os
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_db
from app.config import settings
from app.models import Subject, Job, Artifact
from app.schemas import JobResponse
from app.services import soz as soz_service

router = APIRouter(prefix="/subjects", tags=["soz"])


def _get_subject_or_404(subject_id: int, db: Session) -> Subject:
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    return subject


class SozFuseRequest(BaseModel):
    ei_artifact_id: Optional[int] = None  # defaults to the subject's most recent ei_npz artifact
    hi_artifact_id: Optional[int] = None  # defaults to the subject's most recent hfo_npz artifact


@router.post("/{subject_id}/soz/fuse", response_model=JobResponse)
def fuse_soz(subject_id: int, request: SozFuseRequest = SozFuseRequest(), db: Session = Depends(get_db)):
    subject = _get_subject_or_404(subject_id, db)

    active_job = db.query(Job).filter(
        Job.subject_id == subject_id,
        Job.job_type == "soz_fuse",
        Job.state.in_(["queued", "running"])
    ).first()
    if active_job:
        raise HTTPException(status_code=400, detail="A SOZ fusion job is already in progress for this subject")

    job = Job(
        subject_id=subject.id,
        job_type="soz_fuse",
        state="queued",
        params_json=request.model_dump(),
        progress_pct=0.0,
        progress_message="Job queued"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("/{subject_id}/soz/result")
def get_soz_result(subject_id: int, db: Session = Depends(get_db)):
    _get_subject_or_404(subject_id, db)
    job = (
        db.query(Job)
        .filter(Job.subject_id == subject_id, Job.job_type == "soz_fuse", Job.state == "finished")
        .order_by(Job.created_at.desc())
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="No finished SOZ fusion found for this subject")

    artifact = (
        db.query(Artifact)
        .filter(Artifact.job_id == job.id, Artifact.kind == "soz_csv")
        .first()
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="SOZ result artifact not found")

    abs_path = os.path.join(settings.DATA_ROOT, artifact.rel_path)
    return soz_service.load_result_rows(abs_path)
