"""EI job orchestration. The numerics live in app/sigproc/ei.py."""
import logging

import numpy as np
from sqlalchemy.orm import Session

from app.models import Artifact, Job, Subject
from app.services.edf_common import resolve_edf_path
from app.services.job_control import check_cancelled
from app.services.recon import register_artifact
from app.sigproc.channels import load_seeg
from app.sigproc.ei import (
    BARTOLOMEI_HIGH_BAND,
    BARTOLOMEI_LOW_BAND,
    compute_ei_pipeline,
    load_ei_result,
    save_ei_result,
)
from app.sigproc.filters import DEFAULT_MAINS_FREQ

logger = logging.getLogger(__name__)

__all__ = ["run_ei_compute_job", "load_ei_result"]


def run_ei_compute_job(db: Session, job: Job, log_file):
    subject = db.query(Subject).filter(Subject.id == job.subject_id).first()
    if not subject:
        raise ValueError("Subject not found")

    params = job.params_json or {}
    artifact = db.query(Artifact).filter(
        Artifact.id == params["edf_artifact_id"], Artifact.subject_id == subject.id
    ).first()
    if not artifact:
        raise FileNotFoundError(f"edf artifact {params.get('edf_artifact_id')} not found for this subject")

    band_low = float(params.get("band_low", 1.0))
    band_high = float(params.get("band_high", 500.0))
    baseline_start = float(params["baseline_start"])
    baseline_end = float(params["baseline_end"])
    target_start = float(params["target_start"])
    target_end = float(params["target_end"])
    mains_freq = float(params.get("mains_freq", DEFAULT_MAINS_FREQ))
    ei_method = params.get("ei_method", "band_ratio")
    low_band = tuple(params.get("er_low_band") or BARTOLOMEI_LOW_BAND)
    high_band = tuple(params.get("er_high_band") or BARTOLOMEI_HIGH_BAND)
    # Legacy params carry no reference and were computed under CAR; retrying such a
    # job must reproduce its original result. New jobs always send one explicitly.
    reference = params.get("reference", "car")
    if ei_method not in ("band_ratio", "broadband"):
        raise ValueError(f"unknown ei_method {ei_method!r}; expected 'band_ratio' or 'broadband'")
    if reference not in ("car", "bipolar"):
        raise ValueError(f"unknown reference {reference!r}; expected 'car' or 'bipolar'")

    job.progress_pct = 10.0
    job.progress_message = "Loading edf and applying notch + bandpass filter"
    db.commit()

    edf_path = resolve_edf_path(subject, artifact)
    edf_data = load_seeg(edf_path)
    fs = edf_data.info['sfreq']
    chn_names = edf_data.ch_names
    duration = float(edf_data.times[-1])

    for label, t0, t1 in (("baseline", baseline_start, baseline_end),
                          ("target", target_start, target_end)):
        if t0 < 0 or t1 > duration or t0 >= t1:
            raise ValueError(
                f"{label} window {t0:.3f}-{t1:.3f}s is invalid for a {duration:.3f}s recording"
            )

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
        dropped = [n for i, n in enumerate(chn_names) if i not in set(picks)]
        if dropped:
            logger.info("excluding %d channel(s) at the caller's request: %s", len(dropped), dropped)
        edf_data.pick(picks)
        chn_names = edf_data.ch_names

    pad = 10.0
    span_start = max(0.0, min(baseline_start, target_start) - pad)
    span_end = min(duration, max(baseline_end, target_end) + pad)
    edf_data.crop(tmin=span_start, tmax=span_end).load_data()
    raw_data, _ = edf_data[:]

    job.progress_pct = 60.0
    job.progress_message = "Computing HFER + EI index"
    db.commit()

    check_cancelled(db, job)

    pipeline_res = compute_ei_pipeline(
        raw_data=raw_data,
        fs=fs,
        chn_names=chn_names,
        baseline_start=baseline_start,
        baseline_end=baseline_end,
        target_start=target_start,
        target_end=target_end,
        ei_method=ei_method,
        band_low=band_low,
        band_high=band_high,
        mains_freq=mains_freq,
        low_band=low_band,
        high_band=high_band,
        span_start=span_start,
        reference=reference,
    )

    diagnostics = pipeline_res["diagnostics"]
    final_message = "EI computation complete"
    if diagnostics["degenerate_window"]:
        final_message = (
            "EI complete, but the target window starts inside activity already under way "
            "-- the ranking is effectively energy-only. Consider starting it at the onset."
        )

    ei_result_path = save_ei_result(
        edf_path,
        # the pipeline's own names, not the ones passed in: under bipolar these
        # are pairs (A1-A2) and there is one fewer per shaft
        pipeline_res["chn_names"],
        pipeline_res["ei"],
        pipeline_res["ei_raw"],
        pipeline_res["hfer"],
        pipeline_res["time_coef"],
        diagnostics=diagnostics,
        ei_by_contact=pipeline_res["ei_by_contact"],
    )
    register_artifact(db, subject.id, job.id, "ei_npz", ei_result_path)

    job.progress_pct = 95.0
    job.progress_message = final_message
    db.commit()



