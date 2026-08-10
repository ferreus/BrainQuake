"""EI job orchestration. The numerics live in app/sigproc/ei.py."""
import logging

from sqlalchemy.orm import Session

from app.models import Artifact, Job, Subject
from app.services.edf_common import resolve_edf_path
from app.services.job_control import check_cancelled
from app.services.recon import register_artifact
from app.sigproc.channels import load_seeg
from app.sigproc.ei import (
    compute_ei_index,
    compute_hfer,
    find_saturated_channels,
    load_ei_result,
    save_ei_result,
)
from app.sigproc.filters import DEFAULT_MAINS_FREQ, filter_for_display

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

    job.progress_pct = 10.0
    job.progress_message = "Loading edf and applying notch + bandpass filter"
    db.commit()

    edf_path = resolve_edf_path(subject, artifact)
    # Auxiliary traces are gone before anything reads a sample, so they are in
    # neither the common-average reference nor the EI ranking.
    edf_data = load_seeg(edf_path)
    fs = edf_data.info['sfreq']
    chn_names = edf_data.ch_names
    duration = float(edf_data.times[-1])

    # Typed-in windows can land outside the recording or invert; catch it before
    # the expensive load, with a message naming the recording length rather than
    # failing later on an empty slice inside compute_hfer.
    for label, t0, t1 in (("baseline", baseline_start, baseline_end),
                          ("target", target_start, target_end)):
        if t0 < 0 or t1 > duration or t0 >= t1:
            raise ValueError(
                f"{label} window {t0:.3f}-{t1:.3f}s is invalid for a {duration:.3f}s recording"
            )

    # Channels the caller kept in the trace viewer. Applied before anything
    # else, so a dropped channel is out of the common-average reference too --
    # not merely absent from the ranking.
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

    # Only the two windows are used, but filtfilt needs runway on either side or
    # its edge transient lands inside them. 10 s comfortably covers the impulse
    # response of a 1 Hz-cornered 5th-order Butterworth.
    pad = 10.0
    span_start = max(0.0, min(baseline_start, target_start) - pad)
    span_end = min(duration, max(baseline_end, target_end) + pad)
    edf_data.crop(tmin=span_start, tmax=span_end).load_data()
    raw_data, _ = edf_data[:]

    saturated = find_saturated_channels(raw_data)
    if saturated:
        logger.warning(
            "%d/%d channel(s) are clipped at the amplifier rail for >1%% of the analysed "
            "window; their energy is flat-topped and their EI is not meaningful: %s",
            len(saturated), len(chn_names), [chn_names[i] for i in saturated],
        )

    filtered = filter_for_display(raw_data, fs, band_low, band_high, mains_freq=mains_freq)

    # Re-base the window indices onto the cropped span.
    def _idx(t):
        return int(round((t - span_start) * fs))

    base_start_i, base_end_i = _idx(baseline_start), _idx(baseline_end)
    target_start_i, target_end_i = _idx(target_start), _idx(target_end)

    job.progress_pct = 60.0
    job.progress_message = "Computing HFER + EI index"
    db.commit()

    check_cancelled(db, job)

    baseline_data = filtered[:, base_start_i:base_end_i]
    target_data = filtered[:, target_start_i:target_end_i]
    norm_target, norm_base = compute_hfer(target_data, baseline_data, fs)
    ei, ei_raw, hfer, time_coef = compute_ei_index(norm_target, norm_base, fs)

    ei_result_path = save_ei_result(edf_path, chn_names, ei, ei_raw, hfer, time_coef)
    register_artifact(db, subject.id, job.id, "ei_npz", ei_result_path)

    job.progress_pct = 95.0
    job.progress_message = "EI computation complete"
    db.commit()


