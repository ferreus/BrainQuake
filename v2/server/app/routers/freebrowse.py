from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Subject
from app.services import freebrowse as freebrowse_service

router = APIRouter(prefix="/subjects", tags=["freebrowse"])


def _get_subject_or_404(subject_id: int, db: Session) -> Subject:
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    return subject


@router.get("/{subject_id}/freebrowse.nvd")
def get_freebrowse_document(subject_id: int, db: Session = Depends(get_db)):
    """Builds a NiiVue .nvd document on the fly listing whichever of this
    subject's volumes/surfaces exist on disk (see services/freebrowse.py).
    The FreeBrowse tab's iframe (v2/web) fetches this via its own, unmodified
    `?nvd=<url>` query-param support -- no upload/file-picker step."""
    subject = _get_subject_or_404(subject_id, db)
    return freebrowse_service.build_nvd_document(subject)


@router.get("/{subject_id}/freebrowse/files/{key}")
def get_freebrowse_file(subject_id: int, key: str, db: Session = Depends(get_db)):
    """Serves one whitelisted subject file by a fixed key -- never a raw
    filesystem path -- see services/freebrowse.FREEBROWSE_VOLUMES/MESHES."""
    subject = _get_subject_or_404(subject_id, db)
    try:
        abs_path, name = freebrowse_service.resolve_file_path(subject, key)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown freebrowse file key: {key!r}")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No {key!r} file exists for this subject")
    return FileResponse(abs_path, filename=name, media_type="application/octet-stream")
