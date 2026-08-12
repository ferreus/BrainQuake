import logging
from sqlalchemy.orm import Session

from app.models import Artifact, Job, Subject
from app.services.edf_common import resolve_edf_path
from app.services.job_control import check_cancelled
from app.services.recon import register_artifact
from app.sigproc.channels import load_seeg, seeg_contacts
from app.sigproc.filters import DEFAULT_MAINS_FREQ
from app.sigproc.hfo import (
    HI_count_highEvents_chns,
    HI_preprocess_file,
    band_filt,
    cat_chns_times,
    compute_hfo_pipeline,
    find_high_enveTimes,
    find_high_enveTimes_dir,
    hilbert3,
    load_hfo_result,
    merge_timeRanges,
    notch_filt,
    return_hil_enve,
    return_hil_enve_norm,
    return_timeRanges,
)

logger = logging.getLogger(__name__)

__all__ = [
    "run_hfo_compute_job",
    "load_hfo_result",
    "HI_preprocess_file",
    "HI_count_highEvents_chns",
    "compute_hfo_pipeline",
]


def run_hfo_compute_job(db: Session, job: Job, log_file):
    subject = db.query(Subject).filter(Subject.id == job.subject_id).first()
    if not subject:
        raise ValueError("Subject not found")

    params = job.params_json or {}
    artifact = db.query(Artifact).filter(
        Artifact.id == params["edf_artifact_id"], Artifact.subject_id == subject.id
    ).first()
    if not artifact:
        raise FileNotFoundError(f"edf artifact {params.get('edf_artifact_id')} not found for this subject")

    band_low = float(params.get("band_low", 80.0))
    band_high = float(params.get("band_high", 250.0))
    rel_thresh = float(params.get("rel_thresh", 2.0))
    abs_thresh = float(params.get("abs_thresh", 2.0))
    min_gap = float(params.get("min_gap", 20))
    min_last = float(params.get("min_last", 50))
    mains_freq = float(params.get("mains_freq", DEFAULT_MAINS_FREQ))
    start_time = params.get("start_time")
    end_time = params.get("end_time")

    job.progress_pct = 5.0
    job.progress_message = "Loading edf"
    db.commit()

    edf_path = resolve_edf_path(subject, artifact)
    remain_chns = params.get("remain_chns")
    remain_chns = seeg_contacts(remain_chns) if remain_chns else load_seeg(edf_path).ch_names

    def progress_cb(pct):
        check_cancelled(db, job)
        job.progress_pct = min(90.0, float(pct))
        job.progress_message = f"Computing envelope ({job.progress_pct:.0f}%)"
        db.commit()

    HI_preprocess_file(edf_path, remain_chns, [band_low, band_high], progress_cb,
                       mains_freq=mains_freq, start_time=start_time, end_time=end_time)

    check_cancelled(db, job)
    job.progress_pct = 92.0
    job.progress_message = "Detecting high-envelope events"
    db.commit()

    events_path, _ = HI_count_highEvents_chns(edf_path, rel_thresh, abs_thresh, min_gap, min_last)
    register_artifact(db, subject.id, job.id, "hfo_npz", events_path)

    job.progress_pct = 98.0
    job.progress_message = "HFO computation complete"
    db.commit()

