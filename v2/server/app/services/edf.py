import json
import os
import struct

import mne
import numpy as np
from sqlalchemy.orm import Session

from app.models import Artifact, Subject
from app.services.edf_common import find_non_seeg_channels, resolve_edf_path
from app.services.signal_filters import DEFAULT_MAINS_FREQ, filter_for_display

# Synchronous (not a job) windowed EDF fetch for the web client's EEG canvas --
# closes the gap where EDF files were only ever retrievable whole (raw_edf
# artifact download) and ei-result/hfo-result only expose per-channel scalar
# summaries, never time-resolved samples.
MAX_WINDOW_SECONDS = 60.0

# GET .../window is on the hot path for every pan/zoom/filter-toggle of the
# EEG canvas, and JSON floats (both Python's json.dumps and JS's JSON.parse)
# were a measurable chunk of that round trip -- this is a minimal binary
# format instead, same idea as surface.py's mesh cache: 8-byte magic, a fixed
# scalar header, a UTF-8 JSON block for the one variable-length piece
# (channel names), then a raw channel-major float32 sample buffer. See
# v2/web/src/lib/parseEdfWindowBinary.ts for the matching reader.
WINDOW_MAGIC = b"BQEDFW01"


def _get_artifact(db: Session, subject: Subject, edf_artifact_id: int) -> Artifact:
    artifact = (
        db.query(Artifact)
        .filter(Artifact.id == edf_artifact_id, Artifact.subject_id == subject.id)
        .first()
    )
    if not artifact:
        raise FileNotFoundError(f"edf artifact {edf_artifact_id} not found for this subject")
    return artifact


# Seconds of signal decoded at a time when scanning for the amplitude range.
# Bounds peak memory to this window rather than the whole recording.
_META_SCAN_CHUNK_SECONDS = 60.0


def get_edf_meta(db: Session, subject: Subject, edf_artifact_id: int):
    """GET .../edf/{id}/meta. The amplitude range needs one full decode pass,
    so it's computed once and cached into Artifact.meta_json rather than
    repeated on every call -- this is also what the web client's EEG canvas
    uses for its fixed row pitch (dr = 0.7 * (max-min)), matching the legacy
    disp_press formula in client_ictal.py/client_inter.py.

    The cache is keyed on the source file's size and mtime: without that, a
    replaced or re-uploaded recording kept serving the previous file's channel
    list and amplitude range forever.
    """
    artifact = _get_artifact(db, subject, edf_artifact_id)
    edf_path = resolve_edf_path(subject, artifact)
    stat = os.stat(edf_path)
    fingerprint = {"source_size": stat.st_size, "source_mtime": int(stat.st_mtime)}

    cached = artifact.meta_json
    if cached and "amplitude_range" in cached and all(cached.get(k) == v for k, v in fingerprint.items()):
        return _with_aux_channels(cached)

    # preload=False + chunked scan: the only thing needing every sample is the
    # global min/max, and loading a whole multi-GB recording to compute two
    # scalars pushed the API process into swap on long clips.
    raw = mne.io.read_raw_edf(edf_path, preload=False, stim_channel=None)
    fs = raw.info["sfreq"]
    n_samples = int(raw.n_times)
    chunk = max(1, int(fs * _META_SCAN_CHUNK_SECONDS))
    lo, hi = np.inf, -np.inf
    for i0 in range(0, n_samples, chunk):
        block, _ = raw[:, i0:min(n_samples, i0 + chunk)]
        lo = min(lo, float(np.min(block)))
        hi = max(hi, float(np.max(block)))

    meta = {
        "fs": fs,
        "n_samples": n_samples,
        "duration_sec": float(raw.times[-1]),
        "channels": raw.ch_names,
        "amplitude_range": {"min": lo, "max": hi},
        **fingerprint,
    }
    artifact.meta_json = meta
    db.commit()
    return _with_aux_channels(meta)


def _with_aux_channels(meta: dict) -> dict:
    """Annotate a meta payload with the channels that are not SEEG contacts.

    Derived on read rather than stored, so recordings whose meta was cached
    before this field existed get it without a re-scan, and so the naming rule
    has exactly one implementation (edf_common.find_non_seeg_channels) rather
    than a second copy in the web client.

    The client excludes these from the working set on load, so "everything is
    auxiliary" has to mean "this recording does not follow the convention", not
    "discard the whole recording": a file labelled CH1..CH64, or in bipolar
    pairs, matches no contact name at all. In that case say nothing and leave
    the choice to the user, which is the behaviour from before this field
    existed.
    """
    names = meta.get("channels") or []
    aux = find_non_seeg_channels(names)
    if len(aux) == len(names):
        return {**meta, "aux_channels": []}
    return {**meta, "aux_channels": [names[i] for i in aux]}


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
    mains_freq: float = DEFAULT_MAINS_FREQ,
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
        missing = wanted - set(raw.ch_names)
        if missing:
            # Previously these were dropped silently, so a stale channel list in
            # the client returned fewer traces than it asked for with no clue why.
            raise ValueError(f"unknown channel(s) for this recording: {sorted(missing)}")
    else:
        picks = list(range(len(raw.ch_names)))
    if not picks:
        raise ValueError("no channels selected")

    filtering = band_low is not None and band_high is not None
    pad_start = max(0.0, start - pad) if filtering else start
    pad_end = min(duration, end + pad) if filtering else end

    i0, i1 = raw.time_as_index([pad_start, pad_end])
    data, _ = raw[picks, i0:i1]

    if filtering:
        # NOTE: the common-average reference inside filter_for_display is taken
        # over the *picked* channels, so a client requesting a subset sees
        # slightly different traces than one requesting all of them -- and than
        # what the EI job computes over its own channel set.
        data = filter_for_display(data, fs, band_low, band_high, mains_freq=mains_freq)
        trim0 = int(round((start - pad_start) * fs))
        trim1 = trim0 + int(round((end - start) * fs))
        data = data[:, trim0:trim1]

    return {
        "fs": fs,
        "start": start,
        "end": end,
        "channels": [raw.ch_names[i] for i in picks],
        "filtered": filtering,
        "band_low": band_low,
        "band_high": band_high,
        "data": data,
    }


def pack_edf_window(result: dict) -> bytes:
    channels_json = json.dumps(result["channels"]).encode("utf-8")
    data = np.ascontiguousarray(result["data"], dtype="<f4")
    header = WINDOW_MAGIC + struct.pack(
        "<dddBffIII",
        result["fs"],
        result["start"],
        result["end"],
        1 if result["filtered"] else 0,
        result["band_low"] if result["band_low"] is not None else 0.0,
        result["band_high"] if result["band_high"] is not None else 0.0,
        data.shape[0],
        data.shape[1],
        len(channels_json),
    )
    return header + channels_json + data.tobytes()
