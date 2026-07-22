import os
import shutil
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from app.db import get_db
from app.config import settings
from app.models import Subject, Artifact
from app.schemas import SubjectCreate, SubjectResponse, ArtifactResponse

router = APIRouter(prefix="/subjects", tags=["subjects"])


@router.get("", response_model=List[SubjectResponse])
def list_subjects(db: Session = Depends(get_db)):
    return db.query(Subject).all()


@router.post("", response_model=SubjectResponse)
def create_subject(subject_in: SubjectCreate, db: Session = Depends(get_db)):
    existing = db.query(Subject).filter(Subject.name == subject_in.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Subject with this name already exists")

    subject_dir = os.path.join(settings.SUBJECTS_DIR, subject_in.name)
    subject = Subject(
        name=subject_in.name,
        recon_type=subject_in.recon_type,
        subject_dir=subject_dir
    )
    db.add(subject)
    db.commit()
    db.refresh(subject)

    # NOTE: subject_dir (SUBJECTS_DIR/<name>) is deliberately NOT created here.
    # recon-all/fast-surfer/infant_recon_all all treat that directory merely
    # *existing* (regardless of contents) as "this subject already has a prior run"
    # when given -i, and refuse with "You are trying to re-run an existing subject".
    # Pre-creating it here caused exactly that failure on a genuinely first-ever run
    # (see services/recon.py's run_recon_job, which now owns creating/clearing this
    # directory immediately before invoking the recon tool).
    os.makedirs(os.path.join(settings.DATA_ROOT, "recv", subject.name), exist_ok=True)

    return subject


@router.get("/{subject_id}", response_model=SubjectResponse)
def get_subject(subject_id: int, db: Session = Depends(get_db)):
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    return subject


@router.delete("/{subject_id}")
def delete_subject(subject_id: int, db: Session = Depends(get_db)):
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")

    recv_dir = os.path.join(settings.DATA_ROOT, "recv", subject.name)
    if os.path.exists(recv_dir):
        shutil.rmtree(recv_dir)
    if subject.subject_dir and os.path.exists(subject.subject_dir):
        shutil.rmtree(subject.subject_dir)

    # Patient-export archives live outside the two dirs above (under
    # DATA_ROOT/exports); remove their files so they don't orphan on disk after
    # the Artifact rows cascade-delete with the subject.
    for artifact in db.query(Artifact).filter(
        Artifact.subject_id == subject.id, Artifact.kind == "patient_export"
    ).all():
        export_path = os.path.join(settings.DATA_ROOT, artifact.rel_path)
        if os.path.exists(export_path):
            try:
                os.remove(export_path)
            except OSError:
                pass

    db.delete(subject)
    db.commit()
    return {"message": "Subject deleted successfully"}


@router.post("/{subject_id}/upload", response_model=ArtifactResponse)
def upload_file(
    subject_id: int,
    file_type: str = Query(..., description="Type of file: 't1', 'ct', 'edf', 'zip'"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")

    if file_type == "t1":
        filename = f"{subject.name}T1.nii.gz"
        kind = "raw_t1"
    elif file_type == "ct":
        filename = f"{subject.name}CT.nii.gz"
        kind = "raw_ct"
    elif file_type == "zip":
        filename = f"{subject.name}.zip"
        kind = "archive"
    elif file_type == "edf":
        filename = file.filename
        kind = "raw_edf"
    else:
        raise HTTPException(status_code=400, detail="Invalid file_type")

    recv_dir = os.path.join(settings.DATA_ROOT, "recv", subject.name)
    os.makedirs(recv_dir, exist_ok=True)
    dest_path = os.path.join(recv_dir, filename)

    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    rel_path = os.path.relpath(dest_path, settings.DATA_ROOT)

    artifact = Artifact(
        subject_id=subject.id,
        kind=kind,
        rel_path=rel_path,
        meta_json={"original_filename": file.filename}
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    return artifact


@router.get("/{subject_id}/artifacts", response_model=List[ArtifactResponse])
def list_subject_artifacts(
    subject_id: int,
    kind: Optional[str] = None,
    db: Session = Depends(get_db)
):
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    query = db.query(Artifact).filter(Artifact.subject_id == subject_id)
    if kind is not None:
        query = query.filter(Artifact.kind == kind)
    return query.all()


@router.get("/{subject_id}/download.zip")
def download_subject_zip(subject_id: int, db: Session = Depends(get_db)):
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    artifact = (
        db.query(Artifact)
        .filter(Artifact.subject_id == subject_id, Artifact.kind == "recon_zip")
        .order_by(Artifact.created_at.desc())
        .first()
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="No reconstruction zip found for this subject")
    abs_path = os.path.join(settings.DATA_ROOT, artifact.rel_path)
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="Zip file not found on disk")
    return FileResponse(abs_path, filename=f"{subject.name}.zip", media_type="application/zip")
