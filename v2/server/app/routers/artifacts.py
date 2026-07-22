import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from app.db import get_db
from app.config import settings
from app.models import Artifact

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("/{artifact_id}/download")
def download_artifact(artifact_id: int, db: Session = Depends(get_db)):
    artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    abs_path = os.path.join(settings.DATA_ROOT, artifact.rel_path)
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="Artifact file not found on disk")
    return FileResponse(abs_path, filename=os.path.basename(abs_path))


@router.delete("/{artifact_id}")
def delete_artifact(artifact_id: int, db: Session = Depends(get_db)):
    """Lets the web client clear out a broken artifact record -- e.g. one
    whose backing file was lost from disk (upload/window endpoints then 404
    forever) -- without a DB shell. Best-effort file removal: a record whose
    file is already gone is exactly the case this exists for."""
    artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    abs_path = os.path.join(settings.DATA_ROOT, artifact.rel_path)
    if os.path.exists(abs_path):
        os.remove(abs_path)

    db.delete(artifact)
    db.commit()
    return {"message": "Artifact deleted successfully"}
