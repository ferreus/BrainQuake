from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import Subject, Job
from app.schemas import JobResponse
from app.services import electrodes as electrodes_service

router = APIRouter(prefix="/subjects", tags=["electrodes"])


@router.post("/{subject_id}/electrodes/register-ct", response_model=JobResponse)
def register_ct(subject_id: int, db: Session = Depends(get_db)):
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")

    # Check if there is already an active job (queued or running) of this type
    active_job = db.query(Job).filter(
        Job.subject_id == subject_id,
        Job.job_type == "ct_register",
        Job.state.in_(["queued", "running"])
    ).first()

    if active_job:
        raise HTTPException(status_code=400, detail="A CT registration job is already in progress for this subject")

    # Create the job
    job = Job(
        subject_id=subject.id,
        job_type="ct_register",
        state="queued",
        params_json={},
        progress_pct=0.0,
        progress_message="Job queued"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _get_subject_or_404(subject_id: int, db: Session) -> Subject:
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    return subject


def _reject_if_active_job(subject_id: int, job_type: str, db: Session):
    active_job = db.query(Job).filter(
        Job.subject_id == subject_id,
        Job.job_type == job_type,
        Job.state.in_(["queued", "running"])
    ).first()
    if active_job:
        raise HTTPException(status_code=400, detail=f"A {job_type} job is already in progress for this subject")


class DetectRequest(BaseModel):
    K: int  # target number of implanted electrodes
    threshold_pct: float  # intensity threshold, percent of max CT value in the eroded mask
    erosion_iterations: int  # brain-mask erosion iterations before thresholding


@router.post("/{subject_id}/electrodes/detect", response_model=JobResponse)
def detect_electrodes(subject_id: int, request: DetectRequest, db: Session = Depends(get_db)):
    subject = _get_subject_or_404(subject_id, db)
    _reject_if_active_job(subject_id, "elec_detect", db)

    job = Job(
        subject_id=subject.id,
        job_type="elec_detect",
        state="queued",
        params_json={
            "K": request.K,
            "threshold_pct": request.threshold_pct,
            "erosion_iterations": request.erosion_iterations,
        },
        progress_pct=0.0,
        progress_message="Job queued"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("/{subject_id}/electrodes/labels-summary")
def get_labels_summary(subject_id: int, db: Session = Depends(get_db)):
    subject = _get_subject_or_404(subject_id, db)
    try:
        return electrodes_service.summarize_labels(subject)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


class LabelsUpdateRequest(BaseModel):
    exclude_labels: Optional[List[int]] = None  # cluster values (1..K) to drop as noise


class LabelsUpdateResponse(BaseModel):
    K: int  # number of electrode clusters remaining after exclusion/renumbering


@router.put("/{subject_id}/electrodes/labels", response_model=LabelsUpdateResponse)
def update_labels(subject_id: int, request: LabelsUpdateRequest, db: Session = Depends(get_db)):
    subject = _get_subject_or_404(subject_id, db)
    try:
        K = electrodes_service.commit_labels(subject, request.exclude_labels)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return LabelsUpdateResponse(K=K)


class SegmentRequest(BaseModel):
    numMax: int = 20  # max contacts per electrode shaft
    diameterSize: float = 2.5  # contact diameter, in voxels
    spacing: float = 2.5  # inter-contact spacing, in voxels
    gap: float = 0.0


@router.post("/{subject_id}/electrodes/segment", response_model=JobResponse)
def segment_electrodes(subject_id: int, request: SegmentRequest = SegmentRequest(), db: Session = Depends(get_db)):
    subject = _get_subject_or_404(subject_id, db)
    _reject_if_active_job(subject_id, "elec_segment", db)

    job = Job(
        subject_id=subject.id,
        job_type="elec_segment",
        state="queued",
        params_json=request.model_dump(),
        progress_pct=0.0,
        progress_message="Job queued"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("/{subject_id}/electrodes/chn-xyz")
def get_chn_xyz(subject_id: int, db: Session = Depends(get_db)):
    subject = _get_subject_or_404(subject_id, db)
    try:
        return electrodes_service.load_chn_xyz(subject)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{subject_id}/electrodes/contacts/{label}")
def get_contacts(subject_id: int, label: str, db: Session = Depends(get_db)):
    subject = _get_subject_or_404(subject_id, db)
    try:
        return electrodes_service.load_contact(subject, label)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
