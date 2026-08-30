"""Process-driven analysis runs.

One process applied to a set of recordings. EI, HFO and fragility differ only in
their request model, their result artifact and how per-channel scores are read out
of it -- that is the whole of PROCESSES below. Adding a method is one entry.

The per-process POST/result endpoints in ictal.py and interictal.py stay as they
are; this router adds the batch run and the cross-recording aggregate.
"""
import json
import os
from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Any, Literal, NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Artifact, Job, Subject
from app.routers.ictal import EiRequest
from app.routers.interictal import HfoRequest
from app.routers.json_safe import json_safe
from app.schemas import JobResponse
from app.services import fragility as fragility_service
from app.services import ictal as ictal_service
from app.services import interictal as interictal_service
from app.sigproc.montage import parse_contact

router = APIRouter(prefix="/subjects", tags=["analysis"])

DEFAULT_TOP_N = 20  # contacts per run that vote for their shaft; run_frag.R's TOP_N


class FragilityRequest(BaseModel):
    # Seconds from the start of the edf, normally taken from an annotation.
    onset_s: float
    # Crop and scoring windows default to run_frag.R's PRE/POST/ICTAL_END: a run on
    # a different window compares windows, not implementations.
    pre: float = 20.0
    post: float = 10.0
    eval_end: float = 5.0
    win_s: float = 0.25
    step_s: float = 0.125
    method: Literal["extended", "ezfragility"] = "extended"
    # 'auto' is 0.5 Hz for extended and off for ezfragility, which must stay
    # unfiltered to keep reproducing R.
    highpass_hz: float | Literal["auto"] | None = "auto"
    # Excluded contacts leave before the common average, not just the plot.
    remain_chns: list[str] | None = None
    # The annotation's own text, carried through purely so results and the
    # aggregate read as "SZ 2P" rather than "t340.000". Nothing computes on it.
    onset_label: str | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.onset_s < 0:
            raise ValueError(f"onset_s must be >= 0, got {self.onset_s}")
        if self.pre < 0:
            raise ValueError(f"pre must be >= 0, got {self.pre}")
        if self.post <= 0:
            raise ValueError(f"post must be > 0, got {self.post}")
        if self.win_s <= 0 or self.step_s <= 0:
            raise ValueError("win_s and step_s must be > 0")
        if self.eval_end <= 0:
            raise ValueError(f"eval_end must be > 0, got {self.eval_end}")
        return self


class ProcessSpec(NamedTuple):
    job_type: str
    artifact_kind: str
    request_model: type[BaseModel]
    load: Callable[[str], dict]
    scores: Callable[[dict], dict]
    # What distinguishes two runs on the *same* recording. None means one result
    # per recording (EI and HFO overwrite by design). Fragility runs one 30s
    # window per seizure, and a clip can hold several, so its runs are keyed by
    # onset -- without this the second seizure silently replaces the first.
    run_key: Callable[[dict], str] | None = None


PROCESSES: dict[str, ProcessSpec] = {
    "ei": ProcessSpec(
        job_type="ei_compute", artifact_kind="ei_npz", request_model=EiRequest,
        load=ictal_service.load_ei_result,
        scores=lambda r: dict(zip(r["contact_names"], r["ei_by_contact"])),
    ),
    "hfo": ProcessSpec(
        job_type="hfo_compute", artifact_kind="hfo_npz", request_model=HfoRequest,
        load=interictal_service.load_hfo_result,
        scores=lambda r: dict(zip(r["chn_names"], r["event_counts"])),
    ),
    "fragility": ProcessSpec(
        job_type="fragility_compute", artifact_kind="fragility_npz",
        request_model=FragilityRequest,
        load=fragility_service.load_fragility_result,
        scores=lambda r: r["channel_scores"],
        run_key=lambda p: "t%.3f" % float(p["onset_s"]),
    ),
}


class RunItem(BaseModel):
    edf_artifact_id: int
    # Per-recording values for the process's input slots (baseline/target ranges,
    # a seizure onset). Merged over `params` before validation.
    marks: dict[str, Any] = {}


class RunRequest(BaseModel):
    # Shared across every run in the batch.
    params: dict[str, Any] = {}
    runs: list[RunItem]

    @model_validator(mode="after")
    def _check(self):
        if not self.runs:
            raise ValueError("runs must not be empty")
        # Identical (recording, marks) twice is a double-submit; the same
        # recording with different marks is several seizures in one clip.
        seen = {(r.edf_artifact_id, json.dumps(r.marks, sort_keys=True)) for r in self.runs}
        if len(seen) != len(self.runs):
            raise ValueError("runs repeats the same recording and marks")
        return self


def _get_subject_or_404(subject_id: int, db: Session) -> Subject:
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    return subject


def _run_key(spec: ProcessSpec, params: dict) -> str:
    """Identity of a run within a recording; "" when the process allows only one."""
    if not spec.run_key:
        return ""
    try:
        return spec.run_key(params)
    except (KeyError, TypeError, ValueError):
        return ""


def _get_process_or_404(process: str) -> ProcessSpec:
    spec = PROCESSES.get(process)
    if not spec:
        raise HTTPException(
            status_code=404,
            detail=f"unknown process {process!r}; known are {sorted(PROCESSES)}",
        )
    return spec


@router.post("/{subject_id}/analysis/{process}/run", response_model=list[JobResponse])
def run_analysis(subject_id: int, process: str, request: RunRequest, db: Session = Depends(get_db)):
    """Queue one job per recording. The worker serialises them per subject."""
    subject = _get_subject_or_404(subject_id, db)
    spec = _get_process_or_404(process)

    jobs = []
    for item in request.runs:
        artifact = db.query(Artifact).filter(
            Artifact.id == item.edf_artifact_id, Artifact.subject_id == subject_id
        ).first()
        if not artifact:
            raise HTTPException(
                status_code=404,
                detail=f"edf artifact {item.edf_artifact_id} not found for this subject",
            )

        # Guarding per (process, recording) rather than per process lets a batch
        # queue while still rejecting an accidental double-submit of one run.
        active = db.query(Job).filter(
            Job.subject_id == subject_id,
            Job.job_type == spec.job_type,
            Job.state.in_(["queued", "running"]),
        ).all()
        merged = {**request.params, **item.marks}
        try:
            validated = spec.request_model(**merged)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"invalid params for run on edf artifact {item.edf_artifact_id}: {exc}",
            ) from exc

        key = _run_key(spec, validated.model_dump())
        if any(
            (j.params_json or {}).get("edf_artifact_id") == item.edf_artifact_id
            and _run_key(spec, j.params_json or {}) == key
            for j in active
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"a {process} job is already in progress for edf artifact "
                    f"{item.edf_artifact_id}" + (f" at {key}" if key else "")
                ),
            )

        job = Job(
            subject_id=subject.id,
            job_type=spec.job_type,
            state="queued",
            params_json={"edf_artifact_id": item.edf_artifact_id, **validated.model_dump()},
            progress_pct=0.0,
            progress_message="Job queued",
        )
        db.add(job)
        jobs.append(job)

    db.commit()
    for job in jobs:
        db.refresh(job)
    return jobs


def _latest_finished_runs(db: Session, subject_id: int, spec: ProcessSpec):
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
        key = (edf_id, _run_key(spec, params))
        if key in out:
            continue
        artifact = db.query(Artifact).filter(
            Artifact.job_id == job.id, Artifact.kind == spec.artifact_kind
        ).first()
        if artifact:
            out[key] = (job, artifact)
    return out


@router.get("/{subject_id}/analysis/{process}/{edf_artifact_id}/result")
def get_analysis_result(subject_id: int, process: str, edf_artifact_id: int,
                        run_key: str | None = Query(None),
                        db: Session = Depends(get_db)):
    """One run's result. Without `run_key`, the newest run on that recording --
    a clip with several seizures has one result per seizure."""
    _get_subject_or_404(subject_id, db)
    spec = _get_process_or_404(process)
    runs = _latest_finished_runs(db, subject_id, spec)
    matches = [(k, v) for k, v in runs.items() if k[0] == edf_artifact_id
               and (run_key is None or k[1] == run_key)]
    if not matches:
        raise HTTPException(status_code=404, detail=f"no finished {process} result for this recording")
    # Newest first: _latest_finished_runs walked the jobs in descending id order.
    _, (job, artifact) = max(matches, key=lambda kv: kv[1][0].id)
    abs_path = os.path.join(settings.DATA_ROOT, artifact.rel_path)
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="result file is missing from disk")
    result = json_safe(spec.load(abs_path))
    result["params"] = job.params_json
    return result


@router.get("/{subject_id}/analysis/{process}/aggregate")
def get_analysis_aggregate(subject_id: int, process: str,
                           top_n: int = Query(DEFAULT_TOP_N, ge=1),
                           db: Session = Depends(get_db)):
    """Shaft ranking over every finished run, computed on read.

    Top-`top_n` contacts in each run cast one vote for their shaft; votes are then
    divided by the shaft's contact count, so a 12-contact shaft does not outrank a
    6-contact one on size alone. Ports verify_fragility_bella.py's aggregation.
    """
    _get_subject_or_404(subject_id, db)
    spec = _get_process_or_404(process)
    found = _latest_finished_runs(db, subject_id, spec)

    runs = []
    votes = Counter()
    contacts_seen: dict[str, set] = defaultdict(set)

    for (edf_id, key), (job, artifact) in sorted(found.items()):
        abs_path = os.path.join(settings.DATA_ROOT, artifact.rel_path)
        if not os.path.exists(abs_path):
            continue
        loaded = spec.load(abs_path)
        scores = {k: v for k, v in spec.scores(loaded).items() if v is not None}

        for name in scores:
            parsed = parse_contact(name)
            if parsed:
                contacts_seen[parsed[0]].add(name)

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        for name, _ in ranked[:top_n]:
            parsed = parse_contact(name)
            if parsed:
                votes[parsed[0]] += 1

        params = job.params_json or {}
        runs.append({
            "edf_artifact_id": edf_id,
            "run_key": key,
            "job_id": job.id,
            "recording": os.path.basename(artifact.rel_path),
            # What the operator picked as t=0, so a clip with several seizures is
            # readable as "SZ 2P" rather than "t340.000".
            "label": params.get("onset_label"),
            "onset_s": params.get("onset_s"),
            "n_channels": len(scores),
            # The linear fit quality; only fragility reports it.
            "median_r2": loaded.get("median_r2"),
        })

    shafts = [
        {
            "shaft": shaft,
            "n_contacts": len(names),
            "votes": votes.get(shaft, 0),
            "votes_per_channel": votes.get(shaft, 0) / len(names),
        }
        for shaft, names in contacts_seen.items()
        if names
    ]
    shafts.sort(key=lambda s: s["votes_per_channel"], reverse=True)

    return json_safe({
        "process": process,
        "n_runs": len(runs),
        "top_n": top_n,
        "runs": runs,
        "shafts": shafts,
    })
