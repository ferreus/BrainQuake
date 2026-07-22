import os
import uuid
import shutil
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_db
from app.config import settings
from app.models import Subject, Job, Artifact
from app.schemas import JobResponse, SubjectResponse
from app.services.patient_io import read_import_manifest

router = APIRouter(prefix="/subjects", tags=["patient-io"])


class ImportResponse(BaseModel):
    subject: SubjectResponse
    job: JobResponse


@router.post("/{subject_id}/export", response_model=JobResponse)
def export_patient(subject_id: int, db: Session = Depends(get_db)):
    """Queue a job that zips the subject's entire on-disk footprint (FreeSurfer
    dir + recv tree + a manifest) into a single downloadable archive."""
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")

    active = db.query(Job).filter(
        Job.subject_id == subject_id,
        Job.job_type == "export_patient",
        Job.state.in_(["queued", "running"]),
    ).first()
    if active:
        raise HTTPException(status_code=400, detail="An export job is already in progress for this subject")

    job = Job(
        subject_id=subject.id,
        job_type="export_patient",
        state="queued",
        params_json={},
        progress_pct=0.0,
        progress_message="Job queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("/{subject_id}/export/download")
def download_patient_export(subject_id: int, db: Session = Depends(get_db)):
    """Stream the most recent completed patient-export archive."""
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")

    artifact = (
        db.query(Artifact)
        .filter(Artifact.subject_id == subject_id, Artifact.kind == "patient_export")
        .order_by(Artifact.created_at.desc())
        .first()
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="No patient export found. Run 'Download Patient' first.")
    abs_path = os.path.join(settings.DATA_ROOT, artifact.rel_path)
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="Export file not found on disk")
    return FileResponse(abs_path, filename=f"{subject.name}.zip", media_type="application/zip")


@router.post("/import", response_model=ImportResponse)
def import_patient(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Accept a previously exported patient zip, create the subject record from
    its manifest, and queue a job that unpacks the payload and re-registers the
    subject's artifacts. The subject name is taken from the archive and must not
    already exist (delete the existing patient first)."""
    imports_dir = os.path.join(settings.DATA_ROOT, "imports")
    os.makedirs(imports_dir, exist_ok=True)
    tmp_path = os.path.join(imports_dir, f"import_{uuid.uuid4().hex}.zip")
    with open(tmp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        manifest = read_import_manifest(tmp_path)
    except ValueError as e:
        os.remove(tmp_path)
        raise HTTPException(status_code=400, detail=str(e))

    name = manifest["name"]
    existing = db.query(Subject).filter(Subject.name == name).first()
    if existing:
        os.remove(tmp_path)
        raise HTTPException(
            status_code=409,
            detail=f"A patient named '{name}' already exists. Delete it first to re-import.",
        )

    subject = Subject(
        name=name,
        recon_type=manifest.get("recon_type"),
        subject_dir=None,
    )
    db.add(subject)
    db.commit()
    db.refresh(subject)

    job = Job(
        subject_id=subject.id,
        job_type="import_patient",
        state="queued",
        params_json={"zip_path": tmp_path},
        progress_pct=0.0,
        progress_message="Job queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    return ImportResponse(
        subject=SubjectResponse.model_validate(subject),
        job=JobResponse.model_validate(job),
    )
