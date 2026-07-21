import mne
import numpy as np
from sqlalchemy.orm import Session
from app.models import Artifact, Subject
from app.services.edf_common import resolve_edf_path
from app.services.signal_filters import filter_for_display

# Synchronous (not a job) windowed EDF fetch for the web client's EEG canvas --
# closes the gap where EDF files were only ever retrievable whole (raw_edf
# artifact download) and ei-result/hfo-result only expose per-channel scalar
# summaries, never time-resolved samples.
MAX_WINDOW_SECONDS = 60.0


def _get_artifact(db: Session, subject: Subject, edf_artifact_id: int) -> Artifact:
    artifact = (
        db.query(Artifact)
        .filter(Artifact.id == edf_artifact_id, Artifact.subject_id == subject.id)
        .first()
    )
    if not artifact:
        raise FileNotFoundError(f"edf artifact {edf_artifact_id} not found for this subject")
    return artifact


def get_edf_meta(db: Session, subject: Subject, edf_artifact_id: int):
    """GET .../edf/{id}/meta. The amplitude range needs one full decode pass,
    so it's computed once and cached into Artifact.meta_json rather than
    repeated on every call -- this is also what the web client's EEG canvas
    uses for its fixed row pitch (dr = 0.7 * (max-min)), matching the legacy
    disp_press formula in client_ictal.py/client_inter.py."""
    artifact = _get_artifact(db, subject, edf_artifact_id)
    if artifact.meta_json and "amplitude_range" in artifact.meta_json:
        return artifact.meta_json

    edf_path = resolve_edf_path(subject, artifact)
    raw = mne.io.read_raw_edf(edf_path, preload=True, stim_channel=None)
    data, _ = raw[:]
    meta = {
        "fs": raw.info["sfreq"],
        "n_samples": int(data.shape[1]),
        "duration_sec": float(raw.times[-1]),
        "channels": raw.ch_names,
        "amplitude_range": {"min": float(np.min(data)), "max": float(np.max(data))},
    }
    artifact.meta_json = meta
    db.commit()
    return meta


def get_edf_window(
    db: Session,
    subject: Subject,
    edf_artifact_id: int,
    start: float,
    end: float,
    channels=None,
    band_low=None,
    band_high=None,
    pad: float = 2.0,
):
    """GET .../edf/{id}/window. When filtering, the requested range is padded
    by `pad` seconds (clamped to the recording) before running the zero-phase
    filter, then trimmed back to the exact window -- filtering only the exact
    slice would show filtfilt edge-transient artifacts at every window
    boundary, which panning would make constantly visible."""
    if end <= start:
        raise ValueError("end must be greater than start")
    if end - start > MAX_WINDOW_SECONDS:
        raise ValueError(f"window too large -- max {MAX_WINDOW_SECONDS}s per request")

    artifact = _get_artifact(db, subject, edf_artifact_id)
    edf_path = resolve_edf_path(subject, artifact)
    raw = mne.io.read_raw_edf(edf_path, preload=False, stim_channel=None)
    fs = raw.info["sfreq"]
    duration = raw.times[-1]

    start = max(0.0, start)
    end = min(duration, end)

    if channels:
        wanted = set(channels)
        picks = [i for i, name in enumerate(raw.ch_names) if name in wanted]
    else:
        picks = list(range(len(raw.ch_names)))

    filtering = band_low is not None and band_high is not None
    pad_start = max(0.0, start - pad) if filtering else start
    pad_end = min(duration, end + pad) if filtering else end

    i0, i1 = raw.time_as_index([pad_start, pad_end])
    data, _ = raw[picks, i0:i1]

    if filtering:
        data = filter_for_display(data, fs, band_low, band_high)
        trim0 = int(round((start - pad_start) * fs))
        trim1 = trim0 + int(round((end - start) * fs))
        data = data[:, trim0:trim1]

    return {
        "fs": fs,
        "start": start,
        "end": end,
        "channels": [raw.ch_names[i] for i in picks],
        "units": "uV",
        "filtered": filtering,
        "band_low": band_low,
        "band_high": band_high,
        "data": data.tolist(),
    }
