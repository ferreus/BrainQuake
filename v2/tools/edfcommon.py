"""EDF+ header/annotation helpers and the sidecar schema shared by the converters.

nk2edf, nicolet2edf and vwr2edf each held their own copy of these; they now
import this module by adding v2/tools to sys.path. build_header and signal_spec
stay per-tool -- those legitimately differ per format. See SIDECAR.md.
"""
import json
import os
from collections import OrderedDict

REC_SECS = 1  # EDF data record duration
MIN_ANNOT_BYTES = 120  # floor for the EDF Annotations signal, per record

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


# --- EDF header fields -----------------------------------------------------

def _fld(text, width):
    """Left-justified fixed-width ASCII EDF header field."""
    b = str(text).encode("ascii", "replace")[:width]
    return b + b" " * (width - len(b))


def _num(value, width):
    """EDF numeric field: as many significant digits as fit, no exponent.

    Every character counts: the gain a reader recovers is only as good as what
    fits in these 8 bytes, and montage ranges and Nicolet resolutions are not
    round numbers.
    """
    s = f"{value:.8f}".rstrip("0").rstrip(".")
    for dec in range(width, -1, -1):
        if len(s) <= width:
            return _fld(s, width)
        s = f"{value:.{dec}f}"
    return _fld(s[:width], width)


def recording_field(start, admin, equipment):
    """EDF+ recording identification, which is a strictly ordered five fields."""
    return (f"Startdate {start.day:02d}-{MONTHS[start.month - 1]}-{start.year} "
            f"{admin} X {equipment}")


# --- EDF+ annotations ------------------------------------------------------

def _tal(onset, text=None, duration=None):
    """One EDF+ Time-stamped Annotation List entry."""
    head = f"+{onset:.6f}".rstrip("0").rstrip(".")
    if text is None:  # the timekeeping TAL every record must start with
        return (head + "\x14\x14\x00").encode("utf-8")
    if duration is not None:
        head += "\x15" + f"{duration:.6f}".rstrip("0").rstrip(".")
    clean = text.replace("\x14", " ").replace("\x15", " ").replace("\x00", " ").strip()
    return (head + "\x14" + clean + "\x14\x00").encode("utf-8")


def plan_annotations(events, n_records):
    """Group events into the record containing their onset, and size the
    annotation signal so the fullest record fits. Returns (per_record, nbytes).

    An event is (onset, text) or (onset, text, duration).
    """
    per_record = {}
    for ev in events:
        onset, text = ev[0], ev[1]
        duration = ev[2] if len(ev) > 2 else None
        r = min(int(onset // REC_SECS), n_records - 1)
        per_record.setdefault(r, []).append(_tal(onset, text, duration))
    widest = 0
    for r in range(n_records):
        used = len(_tal(r * REC_SECS)) + sum(len(t) for t in per_record.get(r, ()))
        widest = max(widest, used)
    nbytes = max(MIN_ANNOT_BYTES, widest + (widest % 2))
    return per_record, nbytes


def record_annotations(record, per_record, nbytes):
    """The annotation signal for one record, padded to its allocated width."""
    tals = _tal(record * REC_SECS) + b"".join(per_record.get(record, ()))
    if len(tals) > nbytes:  # plan_annotations sized this
        raise AssertionError(f"record {record}: {len(tals)} > {nbytes} annotation bytes")
    return tals + b"\x00" * (nbytes - len(tals))


# --- CLI -------------------------------------------------------------------

def parse_sel(text, n, what="clip"):
    """`0,2-3` -> [0, 2, 3]; None means everything."""
    if text is None:
        return list(range(n))
    out = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    bad = [i for i in out if not 0 <= i < n]
    if bad:
        raise SystemExit(f"{what} index out of range: {bad}")
    return out


# --- sidecar schema --------------------------------------------------------

SCHEMA = "eeg2edf-sidecar/1"

CLIP = {"index": None, "start": None, "offset_s": 0.0, "duration_s": None, "sfreq_hz": None}
PATIENT = {"sex": None, "dob": None, "age_at_recording": None}
CHANNEL = {"label": None, "edf_label": None, "source_index": None, "unit": None,
           "sfreq_hz": None, "reference": None, "resolution_uv_per_lsb": None,
           "low_cut": None, "high_cut": None, "notch": None, "derived": False}
TRACE = {"label": None, "active": None, "reference": None,
         "active_index": None, "reference_index": None}
EVENT = {"onset_s": None, "duration_s": None, "label": None, "type": None,
         "source": None, "channel": None}
SEGMENT = {"index": None, "start": None, "offset_s": None, "duration_s": None}


def _fill(template, value):
    """Template keys first, in schema order; format-specific extras kept after."""
    value = value or {}
    out = OrderedDict((k, value.get(k, d)) for k, d in template.items())
    out.update((k, v) for k, v in value.items() if k not in template)
    return out


def build_sidecar(*, source_file, source_format, clip, channels, montages=(),
                  montage_applied=None, events=(), segments=(), device=None,
                  reference=None, patient=None, **extra):
    """The sidecar information EDF itself has nowhere to put, one schema for
    every converter. Keys are always present; absent concepts are null or []."""
    meta = OrderedDict()
    meta["schema"] = SCHEMA
    meta["source"] = {"file": source_file, "format": source_format}
    meta["clip"] = _fill(CLIP, clip)
    meta["device"] = device
    meta["reference"] = reference
    meta["patient"] = _fill(PATIENT, patient)
    meta["channels"] = [_fill(CHANNEL, c) for c in channels]
    meta["montages"] = [
        {"name": m["name"], "channels": [_fill(TRACE, t) for t in m["channels"]]}
        for m in montages
    ]
    meta["montage_applied"] = montage_applied
    meta["segments"] = [_fill(SEGMENT, s) for s in segments]
    meta["events"] = [_fill(EVENT, e) for e in events]
    meta.update(extra)
    return meta


def write_sidecar(edf_path, meta):
    with open(os.path.splitext(edf_path)[0] + ".json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
