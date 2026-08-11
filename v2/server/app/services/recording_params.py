import logging

import mne
from sqlalchemy.orm import Session

from app.models import Artifact, RecordingParams, Subject
from app.schemas import RecordingParamsResponse
from app.services.edf_common import resolve_edf_path

logger = logging.getLogger(__name__)


def extract_annotations(edf_path: str) -> list[dict]:
    """Onset/duration/description from the file's EDF+ TAL annotations, if
    any. Onset is seconds from the recording start, same convention as every
    other timing value in this codebase (edf.py's window/meta endpoints)."""
    raw = mne.io.read_raw_edf(edf_path, preload=False, stim_channel=None, verbose=False)
    ann = raw.annotations
    items = [
        {"onset": float(onset), "duration": float(duration), "description": str(description)}
        for onset, duration, description in zip(ann.onset, ann.duration, ann.description)
    ]
    items.sort(key=lambda a: a["onset"])
    return items


def _get_or_create(db: Session, edf_artifact_id: int) -> RecordingParams:
    row = db.query(RecordingParams).filter(RecordingParams.edf_artifact_id == edf_artifact_id).first()
    if not row:
        row = RecordingParams(edf_artifact_id=edf_artifact_id)
        db.add(row)
    return row


def populate_annotations_on_upload(db: Session, subject: Subject, artifact: Artifact) -> None:
    edf_path = resolve_edf_path(subject, artifact)
    try:
        annotations = extract_annotations(edf_path)
    except Exception:
        # A malformed/unreadable annotations channel must not block the
        # upload -- the recording itself is still usable.
        logger.warning("failed to extract annotations from %s", edf_path, exc_info=True)
        annotations = []
    row = _get_or_create(db, artifact.id)
    row.annotations_json = annotations
    db.commit()


def save_ictal_params(db: Session, edf_artifact_id: int, params: dict) -> None:
    row = _get_or_create(db, edf_artifact_id)
    row.ictal_params_json = params
    db.commit()


def save_interictal_params(db: Session, edf_artifact_id: int, params: dict) -> None:
    row = _get_or_create(db, edf_artifact_id)
    row.interictal_params_json = params
    db.commit()


def get_params_response(db: Session, edf_artifact_id: int) -> RecordingParamsResponse:
    row = db.query(RecordingParams).filter(RecordingParams.edf_artifact_id == edf_artifact_id).first()
    if not row:
        return RecordingParamsResponse(edf_artifact_id=edf_artifact_id)
    return RecordingParamsResponse(
        edf_artifact_id=edf_artifact_id,
        ictal_params=row.ictal_params_json,
        interictal_params=row.interictal_params_json,
        annotations=row.annotations_json or [],
        updated_at=row.updated_at,
    )


def delete_params(db: Session, edf_artifact_id: int) -> None:
    # No commit here -- delete_edf_recording (the only caller) batches this
    # with its other cleanup and commits once at the end.
    db.query(RecordingParams).filter(RecordingParams.edf_artifact_id == edf_artifact_id).delete()
