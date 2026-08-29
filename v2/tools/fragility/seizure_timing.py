#!/usr/bin/env python3
"""Per-seizure timing from the EDF+ annotations, in export coordinates.

The fragility export puts t=0 at the `SZ nP` mark, but Li et al. define their
analysis window off the *electrographic* onset (`EEG onset`) and the seizure
offset (`end`). This prints both, relative to the export's zero.

    python seizure_timing.py ../../../data/Bella/BellaEDF -o timing.csv
"""

import argparse
import csv
import glob
import os
import re

import mne

SZ_RE = re.compile(r"^SZ \d+P$")


def timings(path):
    raw = mne.io.read_raw_edf(path, preload=False, verbose="ERROR")
    ann = [(float(o), d.strip()) for o, d in
           zip(raw.annotations.onset, raw.annotations.description)]

    sz = [(o, d) for o, d in ann if SZ_RE.match(d)]
    if len(sz) != 1:
        return None  # not one of the seizure clips
    zero, label = sz[0]

    def first(pattern, after=None):
        for o, d in ann:
            if re.match(pattern, d, re.I) and (after is None or o >= after):
                return o, d
        return None, None

    # Not "^EEG onset$": reviewers append initials or a localising note
    # ("EEG onset JB", "EEG onset - IA fast"), and an anchored match drops them.
    eeg_onset, eeg_onset_label = first(r"^EEG onset\b")
    clin_onset, _ = first(r"^clinical onset$")
    end, _ = first(r"^end$", after=zero)

    return {
        "label": label,
        "file": os.path.basename(path),
        "sz_mark_abs": zero,
        "eeg_onset_label": eeg_onset_label,
        # everything below is relative to the export's t=0 (the SZ nP mark)
        "eeg_onset": None if eeg_onset is None else eeg_onset - zero,
        "clinical_onset": None if clin_onset is None else clin_onset - zero,
        "end": None if end is None else end - zero,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("edf_dir")
    p.add_argument("-o", "--out", help="write a CSV here as well")
    a = p.parse_args()

    rows = [t for f in sorted(glob.glob(os.path.join(a.edf_dir, "*.edf")))
            if (t := timings(f)) is not None]
    if not rows:
        raise SystemExit(f"no clip in {a.edf_dir} carries a single 'SZ nP' annotation")
    rows.sort(key=lambda r: int(re.search(r"\d+", r["label"]).group()))

    hdr = f"{'label':8} {'eeg_onset':>10} {'clin_onset':>11} {'end':>8} {'dur_s':>7} {'5%':>6}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        eo, en = r["eeg_onset"], r["end"]
        dur = pct5 = None
        if eo is not None and en is not None:
            dur = en - eo
            pct5 = 0.05 * dur
        r["duration"], r["pct5"] = dur, pct5
        fmt = lambda v, w, p=1: (f"{v:>{w}.{p}f}" if v is not None else f"{'-':>{w}}")
        print(f"{r['label']:8} {fmt(eo,10)} {fmt(r['clinical_onset'],11)} "
              f"{fmt(en,8)} {fmt(dur,7)} {fmt(pct5,6,2)}")

    if a.out:
        with open(a.out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
