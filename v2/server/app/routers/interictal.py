import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Artifact, Job, Subject
from app.schemas import JobResponse
from app.services import interictal as interictal_service
from app.services import recording_params as recording_params_service
from app.sigproc.filters import DEFAULT_MAINS_FREQ

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
    remain_chns: list[str] | None = None  # channel names to include; default: all
    # Grid frequency the data was recorded on. This matters more here than for EI:
    # on 60 Hz mains the 180 and 240 Hz harmonics land inside the 80-250 Hz ripple
    # band, and an un-notched harmonic is detected as an HFO.
    mains_freq: float = DEFAULT_MAINS_FREQ
    # Seconds from the start of the edf; None means the whole recording, which is
    # what the legacy code always did.
    start_time: float | None = None
    end_time: float | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.band_low <= 0:
            raise ValueError(f"band_low must be > 0, got {self.band_low}")
        if self.band_high <= self.band_low:
            raise ValueError(
                f"band_high ({self.band_high}) must be greater than band_low ({self.band_low})"
            )
        if self.mains_freq < 0:
            raise ValueError(f"mains_freq must be >= 0, got {self.mains_freq}")
        if self.start_time is not None and self.start_time < 0:
            raise ValueError(f"start_time must be >= 0, got {self.start_time}")
        if (self.start_time is not None and self.end_time is not None
                and self.end_time <= self.start_time):
            raise ValueError(
                f"end_time ({self.end_time}) must be greater than start_time ({self.start_time})"
            )
        return self


@router.post("/{subject_id}/interictal/{edf_artifact_id}/hfo", response_model=JobResponse)
def compute_hfo(subject_id: int, edf_artifact_id: int, request: HfoRequest = HfoRequest(), db: Session = Depends(get_db)):
    subject = _get_subject_or_404(subject_id, db)
    artifact = db.query(Artifact).filter(Artifact.id == edf_artifact_id, Artifact.subject_id == subject_id).first()
    if not artifact:
        raise HTTPException(status_code=404, detail="edf artifact not found for this subject")

    # Per (job type, recording) rather than per subject: a batch over several
    # seizures must be able to queue, while an accidental double-submit of one
    # recording is still rejected. The worker serialises execution per subject.
    active_jobs = db.query(Job).filter(
        Job.subject_id == subject_id,
        Job.job_type == "hfo_compute",
        Job.state.in_(["queued", "running"])
    ).all()
    if any((j.params_json or {}).get("edf_artifact_id") == edf_artifact_id for j in active_jobs):
        raise HTTPException(
            status_code=400,
            detail=f"An HFO computation job is already in progress for edf artifact {edf_artifact_id}",
        )

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
    recording_params_service.save_interictal_params(db, edf_artifact_id, request.model_dump())
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
