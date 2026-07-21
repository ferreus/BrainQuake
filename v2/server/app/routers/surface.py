from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import Job, Subject
from app.schemas import JobResponse
from app.services import surface as surface_service

router = APIRouter(prefix="/subjects", tags=["surface"])


def _get_subject_or_404(subject_id: int, db: Session) -> Subject:
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    return subject


@router.get("/{subject_id}/surface/{hemi}")
def get_surface(subject_id: int, hemi: str, db: Session = Depends(get_db)):
    subject = _get_subject_or_404(subject_id, db)
    if hemi not in surface_service.HEMISPHERES:
        raise HTTPException(status_code=400, detail="hemi must be 'lh' or 'rh'")

    path = surface_service.latest_mesh_artifact_path(db, subject, hemi)
    if not path:
        raise HTTPException(
            status_code=404,
            detail=f"No cached {hemi} mesh for this subject yet -- "
            f"POST /subjects/{subject_id}/surface/rebuild to (re)generate it.",
        )
    return FileResponse(path, media_type="application/octet-stream")


@router.post("/{subject_id}/surface/rebuild", response_model=JobResponse)
def rebuild_surface(subject_id: int, db: Session = Depends(get_db)):
    subject = _get_subject_or_404(subject_id, db)

    active_job = (
        db.query(Job)
        .filter(Job.subject_id == subject_id, Job.job_type == "surface_export", Job.state.in_(["queued", "running"]))
        .first()
    )
    if active_job:
        raise HTTPException(status_code=400, detail="A surface export job is already in progress for this subject")

    job = Job(
        subject_id=subject.id,
        job_type="surface_export",
        state="queued",
        params_json={},
        progress_pct=0.0,
        progress_message="Job queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job
