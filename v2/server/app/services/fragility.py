"""Neural fragility job orchestration. The numerics live in app/sigproc/fragility.py.

Defaults mirror v2/tools/fragility/run_frag.R and verify_fragility_bella.py: a run
on a different window compares windows, not implementations.
"""
import logging

import numpy as np
from sqlalchemy.orm import Session

from app.models import Artifact, Job, Subject
from app.services.edf_common import resolve_edf_path
from app.services.job_control import check_cancelled
from app.services.recon import register_artifact
from app.sigproc.channels import load_seeg
from app.sigproc.fragility import (
    compute_fragility_pipeline,
    load_fragility_result,
    save_fragility_result,
)

logger = logging.getLogger(__name__)

__all__ = ["run_fragility_compute_job", "load_fragility_result", "DEFAULTS"]

DEFAULTS = {
    "pre": 20.0,
    "post": 10.0,
    "eval_end": 5.0,
    "win_s": 0.25,
    "step_s": 0.125,
    "method": "extended",
    "highpass_hz": "auto",
}


def run_fragility_compute_job(db: Session, job: Job, log_file):
    subject = db.query(Subject).filter(Subject.id == job.subject_id).first()
    if not subject:
        raise ValueError("Subject not found")

    params = job.params_json or {}
    artifact = db.query(Artifact).filter(
        Artifact.id == params["edf_artifact_id"], Artifact.subject_id == subject.id
    ).first()
    if not artifact:
        raise FileNotFoundError(
            f"edf artifact {params.get('edf_artifact_id')} not found for this subject"
        )

    onset_s = float(params["onset_s"])
    pre = float(params.get("pre", DEFAULTS["pre"]))
    post = float(params.get("post", DEFAULTS["post"]))
    eval_end = float(params.get("eval_end", DEFAULTS["eval_end"]))
    win_s = float(params.get("win_s", DEFAULTS["win_s"]))
    step_s = float(params.get("step_s", DEFAULTS["step_s"]))
    method = params.get("method", DEFAULTS["method"])
    highpass_hz = params.get("highpass_hz", DEFAULTS["highpass_hz"])
    if method not in ("extended", "ezfragility"):
        raise ValueError(f"unknown method {method!r}; expected 'extended' or 'ezfragility'")
    if pre < 0 or post <= 0:
        raise ValueError(f"invalid crop window -{pre}/+{post}")

    job.progress_pct = 10.0
    job.progress_message = "Loading edf"
    db.commit()

    edf_path = resolve_edf_path(subject, artifact)
    edf_data = load_seeg(edf_path)
    fs = float(edf_data.info["sfreq"])
    chn_names = edf_data.ch_names
    duration = float(edf_data.times[-1])

    if not 0.0 <= onset_s <= duration:
        raise ValueError(f"onset {onset_s:.3f}s is outside a {duration:.3f}s recording")

    # Excluded contacts must leave before the common average, not just the plot --
    # otherwise they leak into every remaining channel through the reference.
    remain_chns = params.get("remain_chns")
    if remain_chns:
        wanted = set(remain_chns)
        picks = [i for i, n in enumerate(chn_names) if n in wanted]
        missing = wanted - set(chn_names)
        if missing:
            raise ValueError(
                f"remain_chns names {len(missing)} channel(s) not in this recording: "
                f"{sorted(missing)}"
            )
        if not picks:
            raise ValueError("remain_chns excluded every channel")
        edf_data.pick(picks)
        chn_names = edf_data.ch_names

    # Clamp the crop to the recording and carry the onset's real offset into it, so a
    # seizure marked near either end still scores its ictal window at t = 0.
    t_start = max(0.0, onset_s - pre)
    t_end = min(duration, onset_s + post)
    if t_end <= t_start:
        raise ValueError(f"crop [{t_start:.3f}, {t_end:.3f}]s is empty")
    onset_in_crop = onset_s - t_start

    i0 = int(round(t_start * fs))
    i1 = int(round(t_end * fs)) + 1
    edf_data.load_data()
    data = edf_data.get_data(start=i0, stop=i1) * 1e6  # Volts -> microvolts
    # CAR, matching Li et al. / EZFragility -- parity requires it.
    data = data - data.mean(axis=0, keepdims=True)

    job.progress_pct = 40.0
    job.progress_message = f"Computing fragility over {data.shape[1]} samples x {len(chn_names)} channels"
    db.commit()
    check_cancelled(db, job)

    result = compute_fragility_pipeline(
        data=data,
        fs=fs,
        ch_names=chn_names,
        win_s=win_s,
        step_s=step_s,
        radius=1.0,
        method=method,
        highpass_hz=highpass_hz,
        eval_window_s=(0.0, eval_end),
        onset_s=onset_in_crop,
    )

    check_cancelled(db, job)

    out_path = save_fragility_result(edf_path, result, suffix="_t%.3f" % onset_s)
    register_artifact(db, subject.id, job.id, "fragility_npz", out_path)

    median_r2 = float(result["median_r2"])
    n_windows = int(np.shape(result["fragility_matrix"])[1])
    message = (
        f"Fragility complete: {len(chn_names)} channels x {n_windows} windows, "
        f"median R2 {median_r2:.3f}"
    )
    # The linear models are the whole method; a poor fit means the ranking is
    # describing noise rather than dynamics (v2/tools/fragility/README.md).
    if median_r2 < 0.8:
        message += " -- low R2, treat this ranking as unreliable"
    if log_file:
        log_file.write(message + "\n")
        log_file.flush()

    job.progress_pct = 95.0
    job.progress_message = message
    db.commit()
