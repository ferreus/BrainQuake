"""Registry of analysis processes: how to find a process's finished runs and
read per-channel scores out of one.

Lives in services/ rather than in routers/analysis.py so the SOZ fusion reads
runs the same way the analysis endpoints do. The per-process request models stay
in the router layer (REQUEST_MODELS there), which keeps this import-cycle free.
"""
from collections.abc import Callable
from typing import NamedTuple

from sqlalchemy.orm import Session

from app.models import Artifact, Job
from app.services import fragility as fragility_service
from app.services import ictal as ictal_service
from app.services import interictal as interictal_service


class ProcessSpec(NamedTuple):
    job_type: str
    artifact_kind: str
    load: Callable[[str], dict]
    scores: Callable[[dict], dict]
    # What distinguishes two runs on the *same* recording. None means one result
    # per recording (EI and HFO overwrite by design). Fragility runs one 30s
    # window per seizure, and a clip can hold several, so its runs are keyed by
    # onset -- without this the second seizure silently replaces the first.
    run_key: Callable[[dict], str] | None = None


PROCESSES: dict[str, ProcessSpec] = {
    "ei": ProcessSpec(
        job_type="ei_compute", artifact_kind="ei_npz",
        load=ictal_service.load_ei_result,
        scores=lambda r: dict(zip(r["contact_names"], r["ei_by_contact"])),
    ),
    "hfo": ProcessSpec(
        job_type="hfo_compute", artifact_kind="hfo_npz",
        load=interictal_service.load_hfo_result,
        scores=lambda r: dict(zip(r["chn_names"], r["event_counts"])),
    ),
    "fragility": ProcessSpec(
        job_type="fragility_compute", artifact_kind="fragility_npz",
        load=fragility_service.load_fragility_result,
        scores=lambda r: r["channel_scores"],
        run_key=lambda p: "t%.3f" % float(p["onset_s"]),
    ),
}

# Which process wrote a given result file, for callers holding only artifact ids.
PROCESS_BY_ARTIFACT_KIND = {spec.artifact_kind: name for name, spec in PROCESSES.items()}


def run_key(spec: ProcessSpec, params: dict) -> str:
    """Identity of a run within a recording; "" when the process allows only one."""
    if not spec.run_key:
        return ""
    try:
        return spec.run_key(params)
    except (KeyError, TypeError, ValueError):
        return ""


def latest_finished_runs(db: Session, subject_id: int, spec: ProcessSpec):
    """Newest finished job per (recording, run key), with its result artifact.

    Keying on the recording alone made a second seizure in the same clip replace
    the first in the aggregate, with no error.
    """
    jobs = db.query(Job).filter(
        Job.subject_id == subject_id,
        Job.job_type == spec.job_type,
        Job.state == "finished",
    ).order_by(Job.created_at.desc(), Job.id.desc()).all()

    out = {}
    for job in jobs:
        params = job.params_json or {}
        edf_id = params.get("edf_artifact_id")
        if edf_id is None:
            continue
        key = (edf_id, run_key(spec, params))
        if key in out:
            continue
        artifact = db.query(Artifact).filter(
            Artifact.job_id == job.id, Artifact.kind == spec.artifact_kind
        ).first()
        if artifact:
            out[key] = (job, artifact)
    return out
