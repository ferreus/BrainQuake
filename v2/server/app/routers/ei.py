from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Artifact, Job, Subject
from app.schemas import JobResponse
from app.services import recording_params as recording_params_service
from app.services.edf_common import resolve_edf_path
from app.sigproc.channels import load_seeg
from app.sigproc.filters import DEFAULT_MAINS_FREQ
from app.sigproc.montage import bipolar_plan

router = APIRouter(prefix="/subjects", tags=["ei"])


def _get_subject_or_404(subject_id: int, db: Session) -> Subject:
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    return subject


class EiRequest(BaseModel):
    baseline_start: float  # seconds from the start of the edf
    baseline_end: float
    target_start: float
    target_end: float
    band_low: float = 1.0  # Hz, bandpass filter applied before EI computation
    # None means "up to this recording's Nyquist"; a fixed number here would be a
    # different band on every sampling rate in a mixed cohort.
    band_high: float | None = None
    # Grid frequency the data was recorded on: 50 for Europe/Asia, 60 for North
    # America. Wrong value notches clean signal and leaves the interference.
    mains_freq: float = DEFAULT_MAINS_FREQ
    # Channel names to keep; default (None/empty) is every channel in the file.
    # Same contract as the interictal HFO endpoint. Channels deleted in the
    # trace viewer must be sent here -- otherwise they stay in the ranking AND
    # in the common-average reference, which mixes them into every channel.
    remain_chns: list[str] | None = None
    # 'band_ratio' is Bartolomei's published E(beta+gamma)/E(theta+alpha);
    # 'broadband' is the older energy-vs-baseline variant, kept so earlier
    # results stay reproducible.
    ei_method: Literal["band_ratio", "broadband"] = "band_ratio"
    er_low_band: tuple[float, float] | None = None   # defaults to BARTOLOMEI_LOW_BAND
    er_high_band: tuple[float, float] | None = None  # defaults to BARTOLOMEI_HIGH_BAND
    # Bipolar by default: it beats CAR on SEEG SOZ localization
    # (docs/ei_reference_montage_ds004100.md). Jobs saved before this field
    # existed replay as CAR -- see services/ictal.py.
    reference: Literal["car", "bipolar"] = "bipolar"

    @model_validator(mode="after")
    def _check_windows(self):
        for label in ("baseline", "target"):
            s = getattr(self, f"{label}_start")
            e = getattr(self, f"{label}_end")
            if s < 0:
                raise ValueError(f"{label}_start must be >= 0, got {s}")
            if e <= s:
                raise ValueError(f"{label}_end ({e}) must be greater than {label}_start ({s})")
        if self.band_low <= 0:
            raise ValueError(f"band_low must be > 0, got {self.band_low}")
        if self.band_high is not None and self.band_high <= self.band_low:
            raise ValueError(
                f"band_high ({self.band_high}) must be greater than band_low ({self.band_low})"
            )
        if self.mains_freq < 0:
            raise ValueError(f"mains_freq must be >= 0, got {self.mains_freq}")
        # The baseline is what the target is measured against; overlapping them
        # puts seizure activity into the reference and flattens the contrast.
        if self.baseline_end > self.target_start:
            raise ValueError(
                f"baseline must end before the target starts (baseline_end="
                f"{self.baseline_end}, target_start={self.target_start})"
            )
        for label in ("er_low_band", "er_high_band"):
            band = getattr(self, label)
            if band is not None and band[0] >= band[1]:
                raise ValueError(f"{label} must be (low, high) with low < high, got {band}")
        return self


@router.post("/{subject_id}/analysis/ei/{edf_artifact_id}/run", response_model=JobResponse)
def compute_ei(subject_id: int, edf_artifact_id: int, request: EiRequest, db: Session = Depends(get_db)):
    subject = _get_subject_or_404(subject_id, db)
    artifact = db.query(Artifact).filter(Artifact.id == edf_artifact_id, Artifact.subject_id == subject_id).first()
    if not artifact:
        raise HTTPException(status_code=404, detail="edf artifact not found for this subject")

    # Per (job type, recording) rather than per subject: a batch over several
    # seizures must be able to queue, while an accidental double-submit of one
    # recording is still rejected. The worker serialises execution per subject.
    active_jobs = db.query(Job).filter(
        Job.subject_id == subject_id,
        Job.job_type == "ei_compute",
        Job.state.in_(["queued", "running"])
    ).all()
    if any((j.params_json or {}).get("edf_artifact_id") == edf_artifact_id for j in active_jobs):
        raise HTTPException(
            status_code=400,
            detail=f"An EI computation job is already in progress for edf artifact {edf_artifact_id}",
        )

    job = Job(
        subject_id=subject.id,
        job_type="ei_compute",
        state="queued",
        params_json={"edf_artifact_id": edf_artifact_id, **request.model_dump()},
        progress_pct=0.0,
        progress_message="Job queued"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    recording_params_service.save_ictal_params(db, edf_artifact_id, request.model_dump())
    return job


@router.get("/{subject_id}/analysis/ei/{edf_artifact_id}/bipolar-preview")
def bipolar_preview(
    subject_id: int,
    edf_artifact_id: int,
    remain_chns: list[str] | None = Query(None),
    db: Session = Depends(get_db),
):
    """The derivations a bipolar montage would build for this recording.

    Lets the client show what it is about to compute on -- an unpairable naming
    scheme is otherwise only discovered after the job fails.
    """
    subject = _get_subject_or_404(subject_id, db)
    artifact = db.query(Artifact).filter(
        Artifact.id == edf_artifact_id, Artifact.subject_id == subject_id
    ).first()
    if not artifact:
        raise HTTPException(status_code=404, detail="edf artifact not found for this subject")

    raw = load_seeg(resolve_edf_path(subject, artifact))
    chn_names = list(raw.ch_names)
    raw.close()
    # Exclusions are applied first: dropping a contact legitimately opens a
    # numbering gap, and the preview must show the montage that would result.
    unknown = []
    if remain_chns:
        wanted = set(remain_chns)
        unknown = sorted(wanted - set(chn_names))  # the EI job rejects these outright
        chn_names = [n for n in chn_names if n in wanted]

    plan = bipolar_plan(chn_names)
    return {
        "n_contacts": len(chn_names),
        "n_pairs": len(plan["pairs"]),
        "pairs": [p.name for p in plan["pairs"]],
        "unpairable": plan["unpairable"],
        "skipped_gaps": plan["skipped_gaps"],
        "unknown_channels": unknown,
    }

# The result endpoint lives in analysis.py, served generically for every process
# via its PROCESSES registry. A literal copy here shadowed it (or was shadowed by
# it, depending on include_router order) and drifted: the generic one also checks
# the npz still exists on disk instead of letting np.load raise a 500.
