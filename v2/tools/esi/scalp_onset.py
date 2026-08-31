#!/usr/bin/env python3
"""Step 0 -- raw scalp lateralisation. No head model, no source imaging.

Per pre-surgical seizure: which channels light up first and hardest, and does
the left or the right temporal chain carry more of it. Cheap signal processing
that validates the export, the channel names, the reference and the onset marks
before any modelling investment -- and that may answer the clinical question
outright.

    python scalp_onset.py "datasets/ScalpEEG/1. BellaVeegIchilov"
    python scalp_onset.py datasets/ScalpEEG --census
    python scalp_onset.py <study-dir> -o out.csv
"""

import argparse
import csv
import glob
import json
import os
import re
import sys

import numpy as np

SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../server"))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

import mne  # noqa: E402
from app.sigproc import scalp_montage  # noqa: E402
from app.sigproc.ei import determine_threshold_onset  # noqa: E402
from app.sigproc.filters import filter_for_display  # noqa: E402

# "offset" does not contain "onset"; "SZ 1P" is Cleveland's clinical mark.
ONSET_RE = r"(?i)\bonset\b|^SZ\s*\d+P$"
IED_RE = re.compile(r"(?i)^(SW|IS|SPK)\b")
BAND = (3.0, 30.0)          # ictal rhythm range for a child; f0 is reported, not assumed
BASELINE = (-60.0, -10.0)   # relative to the mark
TARGET = (0.0, 5.0)
PAD = 10.0                  # filtfilt runway


def load_sidecar(edf_path):
    p = edf_path[:-4] + ".json"
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def clip_events(sidecar):
    """Events belonging to *this* clip. `onset_s` is null for a sibling's events."""
    if not sidecar:
        return []
    return [e for e in sidecar.get("events", []) if e.get("onset_s") is not None]


def find_onset(sidecar, pattern):
    """(onset_s, label) of the earliest matching mark, or (None, None)."""
    rx = re.compile(pattern)
    hits = [(e["onset_s"], e["label"]) for e in clip_events(sidecar)
            if rx.search(str(e.get("label", "")))]
    return min(hits) if hits else (None, None)


def detect_mains(data, fs):
    """50 or 60 Hz, by peak prominence above the local spectral background.

    Not raw band power: the spectrum falls toward Nyquist, so at 200 Hz any
    broadband rise (drowsy EMG, movement) puts more power at 50 Hz than at 60 Hz
    and a power comparison reports 50 Hz for a 60 Hz recording. Prominence is
    flat under a broadband change; only a real line peak moves it.
    """
    f = np.fft.rfftfreq(data.shape[1], 1 / fs)
    p = (np.abs(np.fft.rfft(data, axis=1)) ** 2).mean(axis=0)

    def prominence(c):
        if c > 0.45 * fs:                      # no headroom for a sideband estimate
            return -np.inf
        peak = p[(f > c - 1.0) & (f < c + 1.0)]
        side = p[((f > c - 8.0) & (f < c - 2.0)) | ((f > c + 2.0) & (f < c + 8.0))]
        if not peak.size or not side.size or not np.median(side):
            return -np.inf
        return float(peak.max() / np.median(side))

    return 50.0 if prominence(50.0) >= prominence(60.0) else 60.0


def envelope(x, fs):
    """Analytic-signal envelope, smoothed to 0.25 s."""
    from scipy.signal import hilbert
    env = np.abs(hilbert(x, axis=1))
    n = max(1, int(0.25 * fs))
    k = np.ones(n) / n
    return np.apply_along_axis(lambda r: np.convolve(r, k, mode="same"), 1, env)


def analyse(edf_path, pattern=ONSET_RE):
    sidecar = load_sidecar(edf_path)
    onset, mark = find_onset(sidecar, pattern)
    if onset is None:
        return None

    raw = mne.io.read_raw_edf(edf_path, preload=False, stim_channel=None, verbose="error")
    eeg, aux, unknown = scalp_montage.classify_channels(raw.ch_names, sidecar)
    mapping = scalp_montage.normalize_labels(raw.ch_names, sidecar)
    fs = float(raw.info["sfreq"])
    dur = float(raw.times[-1])

    if onset + BASELINE[0] < 0:
        return {"error": "only %.0fs of pre-ictal signal, need %.0fs"
                         % (onset, -BASELINE[0]), "onset": onset, "mark": mark}

    t0 = max(0.0, onset + BASELINE[0] - PAD)
    t1 = min(dur, onset + TARGET[1] + PAD)
    raw.crop(tmin=t0, tmax=t1).load_data(verbose="error")
    raw.pick(eeg).rename_channels({n: mapping[n] for n in eeg})
    names = list(raw.ch_names)
    data, _ = raw[:]

    def idx(t):
        return int(round((onset + t - t0) * fs))

    # Estimate mains on the baseline only: ictal clipping puts broadband harmonics
    # into the window and flipped one Cleveland clip to 50 Hz in a 60 Hz hospital.
    mains = detect_mains(data[:, idx(BASELINE[0]):idx(BASELINE[1])], fs)
    filt = filter_for_display(data, fs, BAND[0], BAND[1], mains_freq=mains, reference="car")

    env = envelope(filt, fs)
    base = env[:, idx(BASELINE[0]):idx(BASELINE[1])]
    targ = env[:, idx(TARGET[0]):idx(TARGET[1])]
    ratio_db = 20 * np.log10(targ.mean(axis=1) / np.maximum(base.mean(axis=1), 1e-20))

    # Latency: first target sample crossing the channel's own baseline threshold.
    onset_idx, crossed = determine_threshold_onset(targ, base, threshold_k=5.0)
    latency = np.where(crossed, onset_idx / fs, np.nan)

    # Dominant ictal rhythm, for the record -- a child's onset is usually theta/alpha.
    seg = filt[:, idx(TARGET[0]):idx(TARGET[1])]
    f = np.fft.rfftfreq(seg.shape[1], 1 / fs)
    ps = (np.abs(np.fft.rfft(seg, axis=1)) ** 2).mean(axis=0)
    inband = (f >= BAND[0]) & (f <= BAND[1])
    f0 = float(f[inband][np.argmax(ps[inband])])

    tier, left, right = scalp_montage.laterality_chains(names)

    def take(chs):
        return float(np.mean([ratio_db[names.index(c)] for c in chs]))

    regions = [(reg, take(l), take(r))
               for reg, l, r in scalp_montage.region_contrasts(names)]

    return {"file": os.path.basename(edf_path), "mark": mark, "onset": onset,
            "fs": fs, "mains": mains, "f0": f0, "n_eeg": len(names),
            "unknown": unknown, "names": names, "ratio_db": ratio_db,
            "latency": latency, "tier": tier, "left": left, "right": right,
            "regions": regions, "left_sum": take(left), "right_sum": take(right)}


def census(root):
    """Clips, recorded hours, wall-clock span, coverage and mark counts per study."""
    studies = sorted(d for d in glob.glob(os.path.join(root, "*")) if os.path.isdir(d))
    if not studies:
        studies = [root]
    print("%-38s %5s %7s %7s %6s %4s %4s"
          % ("study", "clips", "rec_h", "span_h", "cov%", "sz", "IED"))
    for d in studies:
        rec, starts, nsz, nied = 0.0, [], 0, 0
        sidecars = sorted(glob.glob(os.path.join(d, "*.json")))
        for p in sidecars:
            with open(p, encoding="utf-8") as fh:
                s = json.load(fh)
            rec += float(s.get("clip", {}).get("duration_s") or 0)
            st = s.get("clip", {}).get("start")
            if st:
                starts.append(st)
            for e in clip_events(s):
                lab = str(e.get("label", ""))
                if re.search(ONSET_RE, lab):
                    nsz += 1
                if IED_RE.match(lab):
                    nied += 1
        span = 0.0
        if len(starts) > 1:
            import datetime as dt
            ts = sorted(dt.datetime.fromisoformat(x) for x in starts)
            span = (ts[-1] - ts[0]).total_seconds()
        cov = 100 * rec / span if span else float("nan")
        print("%-38s %5d %7.2f %7.2f %6.1f %4d %4d"
              % (os.path.basename(d)[:38], len(sidecars), rec / 3600,
                 span / 3600, cov, nsz, nied))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="study directory, or a single .edf")
    ap.add_argument("--census", action="store_true",
                    help="per-study clip/coverage/mark counts, then exit")
    ap.add_argument("--onset-pattern", default=ONSET_RE)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("-o", "--out", help="write per-channel results to this CSV")
    a = ap.parse_args()

    if a.census:
        census(a.path)
        return 0

    edfs = ([a.path] if a.path.endswith(".edf")
            else sorted(glob.glob(os.path.join(a.path, "*.edf"))))
    rows, done = [], 0
    for p in edfs:
        r = analyse(p, a.onset_pattern)
        if r is None:
            continue
        if "error" in r:
            print("\n=== %s === SKIPPED: %s (mark '%s' at %.1fs)"
                  % (os.path.basename(p), r["error"], r["mark"], r["onset"]))
            continue
        done += 1
        order = np.argsort(-r["ratio_db"])
        print("\n=== %s ===" % r["file"])
        print("  mark '%s' at %.1fs | %d EEG ch | %g Hz | %g Hz mains | peak %.1f Hz"
              % (r["mark"], r["onset"], r["n_eeg"], r["fs"], r["mains"], r["f0"]))
        if r["unknown"]:
            print("  unmapped (excluded): %s" % r["unknown"])
        parts = []
        for i in order[:a.top]:
            lat = r["latency"][i]
            parts.append("%s %+.1fdB%s" % (r["names"][i], r["ratio_db"][i],
                                           " @%.2fs" % lat if np.isfinite(lat) else ""))
        print("  top channels: " + ", ".join(parts))
        print("  region contrasts (mean dB per side; + = that side greater)")
        print("    %-18s %8s %8s %9s" % ("region", "left", "right", "L-R"))
        for reg, lv, rv in r["regions"]:
            d = lv - rv
            side = "LEFT" if d > 0 else "RIGHT"
            flag = "  <<< %s" % side if abs(d) >= 1.0 else ""
            print("    %-18s %+8.1f %+8.1f %+9.1f%s" % (reg, lv, rv, d, flag))
        for i, n in enumerate(r["names"]):
            rows.append({"file": r["file"], "mark": r["mark"], "channel": n,
                         "ratio_db": round(float(r["ratio_db"][i]), 3),
                         "latency_s": (round(float(r["latency"][i]), 3)
                                       if np.isfinite(r["latency"][i]) else "")})

    print("\n%d seizure(s) analysed." % done)
    if a.out and rows:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
        with open(a.out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print("wrote %d rows -> %s" % (len(rows), a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
