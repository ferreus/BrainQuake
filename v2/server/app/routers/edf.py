
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Subject
from app.services import edf as edf_service
from app.services.signal_filters import DEFAULT_MAINS_FREQ

router = APIRouter(prefix="/subjects", tags=["edf"])


def _get_subject_or_404(subject_id: int, db: Session) -> Subject:
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    return subject


@router.get("/{subject_id}/edf/{edf_artifact_id}/meta")
def get_edf_meta(subject_id: int, edf_artifact_id: int, db: Session = Depends(get_db)):
    subject = _get_subject_or_404(subject_id, db)
    try:
        return edf_service.get_edf_meta(db, subject, edf_artifact_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/{subject_id}/edf/{edf_artifact_id}/window")
def get_edf_window(
    subject_id: int,
    edf_artifact_id: int,
    start: float = Query(..., ge=0),
    end: float = Query(...),
    channels: str | None = Query(None, description="comma-separated channel names; omit for all"),
    band_low: float | None = Query(None),
    band_high: float | None = Query(None),
    mains_freq: float = Query(
        DEFAULT_MAINS_FREQ,
        description="power-line frequency to notch: 50 Europe/Asia, 60 North America",
    ),
    db: Session = Depends(get_db),
):
    subject = _get_subject_or_404(subject_id, db)
    channel_list = [c for c in channels.split(",") if c] if channels else None
    try:
        result = edf_service.get_edf_window(
            db,
            subject,
            edf_artifact_id,
            start,
            end,
            channels=channel_list,
            band_low=band_low,
            band_high=band_high,
            mains_freq=mains_freq,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return Response(content=edf_service.pack_edf_window(result), media_type="application/octet-stream")
