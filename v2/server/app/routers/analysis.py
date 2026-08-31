"""Process-driven analysis runs.

One process applied to a set of recordings. EI, HFO and fragility differ only in
their request model, their result artifact and how per-channel scores are read out
of it -- REQUEST_MODELS here plus PROCESSES in services/processes.py, which the
SOZ fusion reads through too. Adding a method is one entry in each.

ei.py and hfo.py keep their own POST /run and EI's bipolar-preview; every
process's result and the cross-recording aggregate are served here.
"""
import math
import os
from collections import Counter, defaultdict
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Artifact, Job, Subject
from app.routers.ei import EiRequest
from app.routers.hfo import HfoRequest
from app.routers.json_safe import json_safe
from app.schemas import JobResponse
from app.services.processes import (
    PROCESSES,
    ProcessSpec,
    latest_finished_runs,
    run_key as process_run_key,
)
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


# The request models stay here, in the router layer; services/processes.py owns
# how a run's results are found and read.
REQUEST_MODELS: dict[str, type[BaseModel]] = {
    "ei": EiRequest,
    "hfo": HfoRequest,
    "fragility": FragilityRequest,
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
        # Duplicates are checked in the endpoint, on the process's run_key rather
        # than on `marks`: two marks can differ (a different onset_label) and
        # still name the same run, which would collide on one result file.
        return self


def _get_subject_or_404(subject_id: int, db: Session) -> Subject:
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    return subject


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
    seen_runs: set[tuple[int, str]] = set()
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
            validated = REQUEST_MODELS[process](**merged)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"invalid params for run on edf artifact {item.edf_artifact_id}: {exc}",
            ) from exc

        key = process_run_key(spec, validated.model_dump())
        # Within this batch: db.add'ed rows are invisible to the query below
        # (SessionLocal is autoflush=False), so track the keys ourselves.
        if (item.edf_artifact_id, key) in seen_runs:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"runs names edf artifact {item.edf_artifact_id} twice"
                    + (f" at {key}" if key else "")
                ),
            )
        seen_runs.add((item.edf_artifact_id, key))

        if any(
            (j.params_json or {}).get("edf_artifact_id") == item.edf_artifact_id
            and process_run_key(spec, j.params_json or {}) == key
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


@router.get("/{subject_id}/analysis/{process}/{edf_artifact_id}/result")
def get_analysis_result(subject_id: int, process: str, edf_artifact_id: int,
                        run_key: str | None = Query(None),
                        db: Session = Depends(get_db)):
    """One run's result. Without `run_key`, the newest run on that recording --
    a clip with several seizures has one result per seizure."""
    _get_subject_or_404(subject_id, db)
    spec = _get_process_or_404(process)
    runs = latest_finished_runs(db, subject_id, spec)
    matches = [(k, v) for k, v in runs.items() if k[0] == edf_artifact_id
               and (run_key is None or k[1] == run_key)]
    if not matches:
        raise HTTPException(status_code=404, detail=f"no finished {process} result for this recording")
    # Newest first: latest_finished_runs walked the jobs in descending id order.
    _, (job, artifact) = max(matches, key=lambda kv: kv[1][0].id)
    abs_path = os.path.join(settings.DATA_ROOT, artifact.rel_path)
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="result file is missing from disk")
    result = json_safe(spec.load(abs_path))
    result["params"] = job.params_json or {}
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
    found = latest_finished_runs(db, subject_id, spec)

    runs = []
    votes = Counter()
    contacts_seen: dict[str, set] = defaultdict(set)

    for (edf_id, key), (job, artifact) in sorted(found.items()):
        abs_path = os.path.join(settings.DATA_ROOT, artifact.rel_path)
        if not os.path.exists(abs_path):
            continue
        loaded = spec.load(abs_path)
        # isfinite, not "is not None": a channel with no usable baseline scores
        # NaN, which survives a None check and then sorts unpredictably -- it both
        # steals a top-N vote and displaces a real contact from one.
        scores = {
            k: float(v) for k, v in spec.scores(loaded).items()
            if v is not None and math.isfinite(v)
        }

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
            # The result file itself -- what the SOZ run picker selects and deletes.
            "artifact_id": artifact.id,
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
