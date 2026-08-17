#!/usr/bin/env python3
"""Run EI under both references over every seizure of one subject and aggregate.

Each clip is expected to carry exactly one `SZ nP` annotation marking the
seizure. Rankings from a single seizure swing too much to be read on their own,
so the output is aggregated across seizures and reported per shaft as well as
per contact.

    python compare_ei_reference_subject.py data/Bella/BellaEDF \
        --soz A,I --spared-of-interest D -o out/
"""
import argparse
import csv
import glob
import os
import re
import sys

import numpy as np

SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../server"))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from app.sigproc.channels import load_seeg  # noqa: E402
from app.sigproc.ei import compute_ei_pipeline  # noqa: E402
from app.sigproc.montage import parse_contact, project_pairs_to_contacts  # noqa: E402

SZ_RE = re.compile(r"^SZ \d+P$")
# Anything that means "a seizure was already happening" -- the baseline must
# start after the last one, or it carries ictal activity into the reference.
PRIOR_ACTIVITY_RE = re.compile(r"^(SZ \d+P|end|EEG onset|clinical onset)$", re.I)


def seizure_clips(edf_dir):
    """(path, sz_mark_seconds) for each clip holding exactly one SZ nP mark."""
    out = []
    for path in sorted(glob.glob(os.path.join(edf_dir, "*.edf"))):
        import mne
        raw = mne.io.read_raw_edf(path, preload=False, verbose="ERROR")
        ann = list(zip(raw.annotations.onset, raw.annotations.description))
        sz = [(float(o), d.strip()) for o, d in ann if SZ_RE.match(str(d).strip())]
        if len(sz) == 1:
            out.append((path, sz[0][0], sz[0][1], ann))
    out.sort(key=lambda r: int(re.search(r"\d+", r[2]).group()))
    return out


def pick_windows(sz_t, ann, duration, pre_pad, baseline_len, lead, post):
    """Baseline/target windows, with the baseline clamped after any earlier
    ictal annotation so a clustered seizure does not poison its own reference."""
    target_start = max(0.0, sz_t - lead)
    target_end = min(duration, sz_t + post)
    baseline_end = target_start - pre_pad
    baseline_start = baseline_end - baseline_len

    prior = [float(o) for o, d in ann
             if PRIOR_ACTIVITY_RE.match(str(d).strip()) and float(o) < baseline_end - 1.0]
    floor = max(prior) + 5.0 if prior else 0.0
    baseline_start = max(baseline_start, floor, 0.0)
    return baseline_start, baseline_end, target_start, target_end


def run_one(path, sz_t, ann, reference, opts):
    raw = load_seeg(path)
    fs = float(raw.info["sfreq"])
    duration = float(raw.times[-1])
    chn_names = list(raw.ch_names)

    b0, b1, t0, t1 = pick_windows(sz_t, ann, duration, opts.pre_pad,
                                  opts.baseline_len, opts.lead, opts.post)
    if b1 - b0 < 5.0 or t1 - t0 < 2.0:
        raw.close()
        return None, f"unusable windows baseline={b0:.1f}-{b1:.1f} target={t0:.1f}-{t1:.1f}"

    pad = 10.0
    span_start = max(0.0, b0 - pad)
    span_end = min(duration, t1 + pad)
    raw.crop(tmin=span_start, tmax=span_end).load_data()
    data, _ = raw[:]
    raw.close()

    res = compute_ei_pipeline(
        raw_data=data, fs=fs, chn_names=chn_names,
        baseline_start=b0, baseline_end=b1, target_start=t0, target_end=t1,
        ei_method="band_ratio", band_low=opts.band_low, band_high=opts.band_high,
        mains_freq=opts.mains_freq, span_start=span_start, reference=reference,
    )
    scores = (project_pairs_to_contacts(res["ei_scores"], res["pairs"])
              if reference == "bipolar" else res["ei_scores"])
    return {"scores": scores, "diag": res["diagnostics"],
            "windows": (b0, b1, t0, t1)}, None


def rank_pct(scores):
    """Contact -> percentile rank in [0,1], 1.0 = highest EI this seizure."""
    names = list(scores)
    vals = np.array([scores[n] for n in names], dtype=float)
    order = vals.argsort()
    pct = np.empty(len(vals), dtype=float)
    pct[order] = np.arange(len(vals)) / max(len(vals) - 1, 1)
    return dict(zip(names, pct))


def aggregate(per_seizure):
    """Mean EI and mean rank-percentile per contact across seizures."""
    contacts = sorted({c for s in per_seizure for c in s["scores"]})
    ei = {c: [] for c in contacts}
    pr = {c: [] for c in contacts}
    for s in per_seizure:
        pct = rank_pct(s["scores"])
        for c in contacts:
            if c in s["scores"]:
                ei[c].append(s["scores"][c])
                pr[c].append(pct[c])
    return (
        {c: float(np.mean(v)) for c, v in ei.items() if v},
        {c: float(np.mean(v)) for c, v in pr.items() if v},
    )


def shaft_of(contact):
    parsed = parse_contact(contact)
    return parsed[0] if parsed else "?"


def report(label, mean_ei, mean_pct, soz, spared, top_k, out_rows):
    ranked = sorted(mean_pct, key=lambda c: mean_pct[c], reverse=True)
    top = ranked[:top_k]
    soz_contacts = [c for c in mean_pct if shaft_of(c) in soz]
    hits = [c for c in top if shaft_of(c) in soz]

    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
    print(f"top {top_k} contacts by mean rank-percentile:")
    print("  " + ", ".join(f"{c}({mean_pct[c]:.2f})" for c in top))
    print(f"\nclinical SOZ shafts {sorted(soz)}: {len(hits)}/{top_k} of the top "
          f"{top_k} ({len(soz_contacts)} SOZ contacts implanted)")
    if hits:
        print(f"  hits: {', '.join(hits)}")

    by_shaft = {}
    for c, v in mean_pct.items():
        by_shaft.setdefault(shaft_of(c), []).append(v)
    shaft_rank = sorted(by_shaft, key=lambda s: np.mean(by_shaft[s]), reverse=True)
    print("\nshafts by mean contact rank-percentile:")
    for s in shaft_rank:
        tag = ""
        if s in soz:
            tag = "  <- clinical SOZ"
        elif s in spared:
            tag = "  <- most fragile / spared"
        print(f"  {s:<4} {np.mean(by_shaft[s]):.3f}  (n={len(by_shaft[s])}){tag}")

    for c in sorted(mean_pct, key=lambda c: mean_pct[c], reverse=True):
        out_rows.append({"reference": label, "contact": c, "shaft": shaft_of(c),
                         "mean_ei": round(mean_ei[c], 4),
                         "mean_rank_pct": round(mean_pct[c], 4),
                         "is_soz_shaft": shaft_of(c) in soz})
    return shaft_rank


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("edf_dir")
    ap.add_argument("--soz", default="", help="comma-separated clinically annotated onset shafts")
    ap.add_argument("--spared-of-interest", default="",
                    help="comma-separated shafts to flag in the shaft table")
    ap.add_argument("--lead", type=float, default=5.0,
                    help="start the target window this many seconds before the SZ mark")
    ap.add_argument("--post", type=float, default=15.0, help="target window length after the mark")
    ap.add_argument("--pre-pad", type=float, default=10.0, help="gap between baseline end and target start")
    ap.add_argument("--baseline-len", type=float, default=50.0)
    ap.add_argument("--band-low", type=float, default=1.0)
    ap.add_argument("--band-high", type=float, default=300.0)
    ap.add_argument("--mains-freq", type=float, default=60.0)
    ap.add_argument("--top-k", type=int, default=16)
    ap.add_argument("-o", "--out-csv")
    opts = ap.parse_args()

    soz = {s.strip() for s in opts.soz.split(",") if s.strip()}
    spared = {s.strip() for s in opts.spared_of_interest.split(",") if s.strip()}

    clips = seizure_clips(opts.edf_dir)
    if not clips:
        raise SystemExit(f"no clip in {opts.edf_dir} carries a single 'SZ nP' annotation")
    print(f"{len(clips)} seizure clip(s) found")

    results = {"car": [], "bipolar": []}
    for path, sz_t, label, ann in clips:
        for reference in ("car", "bipolar"):
            res, err = run_one(path, sz_t, ann, reference, opts)
            if err:
                print(f"  {label:7} {reference:8} SKIPPED ({err})")
                continue
            b0, b1, t0, t1 = res["windows"]
            d = res["diag"]
            flags = []
            if d.get("degenerate_window"):
                flags.append("DEGENERATE")
            if d.get("saturated_channels"):
                flags.append(f"{len(d['saturated_channels'])} saturated")
            print(f"  {label:7} {reference:8} base {b0:6.1f}-{b1:6.1f}  target {t0:6.1f}-{t1:6.1f}  "
                  f"crossed {d['n_crossed']}/{d['n_channels']}  {' '.join(flags)}")
            results[reference].append(res)

    out_rows = []
    for reference in ("car", "bipolar"):
        if not results[reference]:
            print(f"\n{reference}: no usable seizures")
            continue
        mean_ei, mean_pct = aggregate(results[reference])
        report(f"{reference.upper()}  (n={len(results[reference])} seizures)",
               mean_ei, mean_pct, soz, spared, opts.top_k, out_rows)

    if opts.out_csv and out_rows:
        with open(opts.out_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out_rows[0]))
            w.writeheader()
            w.writerows(out_rows)
        print(f"\nwrote {opts.out_csv}")


if __name__ == "__main__":
    main()
