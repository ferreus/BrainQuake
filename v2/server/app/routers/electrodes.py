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


class ContactImportItem(BaseModel):
    electrode: str  # e.g. "G'" -- Slicer's own electrode naming, not the legacy A-Z (minus I) convention
    contact_index: int  # 1-based; must be contiguous 1..N within an electrode
    x: float  # FreeSurfer surface (tkreg) RAS -- same space ElectrodeSeg.resulting() writes
    y: float
    z: float


class ImportContactsRequest(BaseModel):
    # Exactly one of these two must be given. contacts is the pre-parsed path
    # (e.g. a script that already did Steps 3-4); csv_text is the raw
    # electrode/contact_index/surfR/surfA/surfS CSV from those same steps,
    # parsed server-side inside run_elec_import_job -- deliberately NOT
    # validated here, so a malformed CSV still produces a real (failed) job
    # with the parse error as its progress_message, rather than a client-side
    # error with no job ever created (the web UI takes this path).
    contacts: Optional[List[ContactImportItem]] = None
    csv_text: Optional[str] = None


@router.post("/{subject_id}/electrodes/import", response_model=JobResponse)
def import_electrode_contacts(subject_id: int, request: ImportContactsRequest, db: Session = Depends(get_db)):
    """Bridges externally-resolved SEEG contacts (e.g. from a 3D Slicer .mrb --
    see docs/seeg_slicer_contact_import_plan.md) into the same chnXyzDict/contact_txt
    artifacts detect()+segment() produce, bypassing hough3dlines/GMM/ElectrodeSeg
    entirely. Coordinates must already be resolved to FreeSurfer surface RAS by the
    caller (Steps 1-4 of the plan doc)."""
    subject = _get_subject_or_404(subject_id, db)
    _reject_if_active_job(subject_id, "elec_import", db)

    if not request.contacts and not request.csv_text:
        raise HTTPException(status_code=400, detail="Provide either contacts or csv_text")

    params_json = {}
    if request.contacts:
        params_json["contacts"] = [c.model_dump() for c in request.contacts]
    if request.csv_text:
        params_json["csv_text"] = request.csv_text

    job = Job(
        subject_id=subject.id,
        job_type="elec_import",
        state="queued",
        params_json=params_json,
        progress_pct=0.0,
        progress_message="Job queued"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


class SlicerPreviewRequest(BaseModel):
    mrb_artifact_id: int  # a 'raw_mrb' artifact from POST /subjects/{id}/upload?file_type=mrb


@router.post("/{subject_id}/electrodes/import/preview", response_model=JobResponse)
def start_slicer_preview(subject_id: int, request: SlicerPreviewRequest, db: Session = Depends(get_db)):
    """Parses a previously-uploaded 3D Slicer .mrb into a *preview* contacts
    list (never written to the real chnXyzDict/contact_txt directly) -- see
    services/electrodes.parse_mrb for the auto-selection heuristics involved
    (which markup node, which transform direction) and why this is a
    review-then-approve step rather than a one-shot import."""
    subject = _get_subject_or_404(subject_id, db)
    for job_type in ("elec_detect", "elec_segment", "elec_import", "slicer_mrb_parse"):
        _reject_if_active_job(subject_id, job_type, db)

    job = Job(
        subject_id=subject.id,
        job_type="slicer_mrb_parse",
        state="queued",
        params_json={"mrb_artifact_id": request.mrb_artifact_id},
        progress_pct=0.0,
        progress_message="Job queued"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("/{subject_id}/electrodes/import/preview")
def get_slicer_preview(subject_id: int, db: Session = Depends(get_db)):
    subject = _get_subject_or_404(subject_id, db)
    try:
        return electrodes_service.load_slicer_preview(db, subject)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{subject_id}/electrodes/import/preview/approve")
def approve_slicer_preview(subject_id: int, db: Session = Depends(get_db)):
    """Writes the pending preview's contacts into the real chnXyzDict/contact_txt
    artifacts (via the same import_contacts() the CSV/JSON import path uses) and
    discards the preview. Synchronous, not a job -- the heavy work (parsing,
    transform-direction disambiguation) already happened in the preview job;
    this step is just copying already-computed numbers into place, the same
    reasoning as why PUT .../labels (committing reviewed clusters) isn't a job."""
    subject = _get_subject_or_404(subject_id, db)
    for job_type in ("elec_detect", "elec_segment", "elec_import"):
        _reject_if_active_job(subject_id, job_type, db)
    try:
        result = electrodes_service.approve_slicer_preview(db, subject)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.post("/{subject_id}/electrodes/import/preview/reject")
def reject_slicer_preview(subject_id: int, db: Session = Depends(get_db)):
    subject = _get_subject_or_404(subject_id, db)
    try:
        electrodes_service.reject_slicer_preview(db, subject)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "Preview rejected"}


@router.delete("/{subject_id}/electrodes/contacts")
def delete_electrode_contacts(subject_id: int, db: Session = Depends(get_db)):
    """Clears clusters (detect()'s labels_npy) and contacts (segment()'s or
    import()'s chnXyzDict/contact_txt) so the tab can be redone from scratch --
    e.g. to throw out a bad hough3dlines/GMM run or a Slicer import."""
    subject = _get_subject_or_404(subject_id, db)
    for job_type in ("elec_detect", "elec_segment", "elec_import", "slicer_mrb_parse"):
        _reject_if_active_job(subject_id, job_type, db)
    electrodes_service.clear_contacts(db, subject)
    return {"message": "Contacts and cluster data cleared"}


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
