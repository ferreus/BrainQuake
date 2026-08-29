#!/usr/bin/env python3
"""Epileptogenicity Index across Bella's 8 'SZ nP' seizures.

Promoted from EI_all_seizures.ipynb, which globbed a directory and hardcoded a
path that no longer exists. Same numerics, explicit seizure list, and a
per-contact CSV so downstream joins have a stable target.

  python ei_bella.py                      # datasets/BellaNew, stdout tables
  python ei_bella.py -o data/ei_bella.csv
"""

import argparse
import csv
import os
import re
import sys
from collections import defaultdict

import numpy as np

SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../server"))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from app.sigproc.channels import load_seeg
from app.sigproc.ei import (compute_ei_index, compute_hfer, determine_threshold_onset,
                            ei_diagnostics, find_saturated_channels)
from app.sigproc.filters import filter_for_display

DEFAULT_EDF_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../datasets/BellaNew"))

SEIZURE_FILES = [
    ("SZ 1P", "DA6465AU_17_20240319072231.edf"),
    ("SZ 2P", "DA6465AU_20_20240319173404.edf"),
    ("SZ 3P", "DA6465AU_21_20240319221721.edf"),
    ("SZ 4P", "DA6465AU_22_20240320060204.edf"),
    ("SZ 5P", "DA6465AU_26_20240321033634.edf"),
    ("SZ 6P", "DA6465AU_27_20240321070053.edf"),
    ("SZ 7P", "DA6465AU_29_20240321125835.edf"),
    ("SZ 8P", "DA6465AU_44_20240322221053.edf"),
]

# Every clip marks its seizure at t≈120 s, so one window convention serves all 8.
# Clip-17 parameters from docs/bella_ictal_ei_vs_annotation_discrepancy.md.
BASELINE = (20.0, 75.0)
TARGET = (100.0, 170.0)
BAND = (1.0, 300.0)
MAINS = 60.0
PAD = 10.0  # filtfilt runway outside the analysed windows

GT_ONSET_SHAFTS = {"A", "I"}
GT_SPREAD_SHAFTS = {"N", "P", "G", "L", "K", "Q", "S"}


def shaft_of(contact):
    """'X\\'12' -> "X'", 'A7' -> 'A'. The prime marks the contralateral shaft."""
    return re.match(r"^([A-Za-z]'?)", contact).group(1)


def ei_for_file(path, target=TARGET, excluded=()):
    """(contacts ranked best-first, EI in that order, saturated names)."""
    raw = load_seeg(path)  # drops REF/DC/EKG/UNUSED/MARK
    if excluded:
        raw.drop_channels([c for c in excluded if c in raw.ch_names])
    fs = raw.info["sfreq"]
    chn = raw.ch_names
    duration = float(raw.times[-1])

    span0 = max(0.0, min(BASELINE[0], target[0]) - PAD)
    span1 = min(duration, max(BASELINE[1], target[1]) + PAD)
    raw.crop(tmin=span0, tmax=span1).load_data()
    data, _ = raw[:]
    filtered = filter_for_display(data, fs, *BAND, mains_freq=MAINS)

    def idx(t):
        return int(round((t - span0) * fs))

    # Scoped to the target window, and reported with the time clipping starts:
    # compute_ei_index integrates only EI_ENERGY_WINDOW_SEC from each channel's
    # own onset, so clipping late in the window reaches no computation. Without
    # the timestamp this warning fires on data nothing reads.
    tgt = data[:, idx(target[0]):idx(target[1])]
    sat_idx = find_saturated_channels(tgt)
    saturated = [chn[i] for i in sat_idx]
    sat_from = None
    if sat_idx:
        lo = tgt[sat_idx].min(axis=1, keepdims=True)
        hi = tgt[sat_idx].max(axis=1, keepdims=True)
        tol = (hi - lo) * 1e-4
        rail = (np.abs(tgt[sat_idx] - hi) <= tol) | (np.abs(tgt[sat_idx] - lo) <= tol)
        sat_from = target[0] + int(np.argmax(rail.any(axis=0))) / fs
    norm_t, norm_b = compute_hfer(filtered[:, idx(target[0]):idx(target[1])],
                                  filtered[:, idx(BASELINE[0]):idx(BASELINE[1])], fs)
    # Onsets bunched at the window edge mean the window opened inside activity
    # already under way: the time term goes flat and EI degrades to energy-only.
    onset, crossed = determine_threshold_onset(norm_t, norm_b)
    diag = ei_diagnostics(onset, crossed, norm_t.shape[1] / (target[1] - target[0]),
                          norm_t.shape[1])

    ei, _raw_ei, _hfer, _tc = compute_ei_index(norm_t, norm_b, fs)
    order = np.argsort(ei)[::-1]
    return [chn[c] for c in order], ei[order], saturated, sat_from, diag


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edf-dir", default=DEFAULT_EDF_DIR,
                    help="directory holding the 8 seizure EDFs")
    ap.add_argument("-o", "--out", help="write per-contact EI to this CSV")
    ap.add_argument("--target-end", type=float, default=TARGET[1],
                    help=f"end of the ictal target window (default {TARGET[1]:g} s)")
    ap.add_argument("--exclude", default="",
                    help="comma-separated contacts to drop before the CAR, "
                         "e.g. \"L'9,K'12,G'4\"")
    args = ap.parse_args()

    target = (TARGET[0], args.target_end)
    excluded = {c.strip() for c in args.exclude.split(",") if c.strip()}
    print(f"baseline {BASELINE}  target {target}", flush=True)
    if excluded:
        print(f"excluding {len(excluded)} contacts: {', '.join(sorted(excluded))}",
              flush=True)

    edf_dir = os.path.abspath(args.edf_dir)
    print(f"Loading Bella EDFs from: {edf_dir}", flush=True)
    if not os.path.isdir(edf_dir):
        print(f"Error: {edf_dir} not found.", flush=True)
        return 1

    ranking, values, shaft_size = {}, {}, {}
    for label, filename in SEIZURE_FILES:
        path = os.path.join(edf_dir, filename)
        if not os.path.exists(path):
            print(f"Warning: {path} not found, skipping {label}.", flush=True)
            continue
        order, ei, saturated, sat_from, diag = ei_for_file(path, target, excluded)
        ranking[label], values[label] = order, ei
        if not shaft_size:
            for c in order:
                shaft_size[shaft_of(c)] = shaft_size.get(shaft_of(c), 0) + 1
        note = ""
        if saturated:
            note = (f"  clipped from t={sat_from:.0f}s: {len(saturated)} ch "
                    f"({', '.join(saturated[:6])}{'...' if len(saturated) > 6 else ''})")
        print(f"{label}: {len(order)} contacts ranked{note}", flush=True)
        print(f"    onsets at window start: {diag['frac_onset_at_window_start']:.0%}"
              f"   DEGENERATE={diag['degenerate_window']}", flush=True)

    if not ranking:
        print("No seizures evaluated.", flush=True)
        return 1

    n_contacts = sum(shaft_size.values())
    print(f"\n{n_contacts} contacts across {len(shaft_size)} shafts\n", flush=True)

    print(f"{'seizure':8}  EI top 10", flush=True)
    for label in sorted(ranking):
        print(f"{label:8}  {', '.join(ranking[label][:10])}", flush=True)

    # Cutoff-free: which shaft wins each seizure outright.
    wins = defaultdict(int)
    for label in ranking:
        wins[shaft_of(ranking[label][0])] += 1
    print("\n#1 contact per seizure: " +
          ", ".join(ranking[L][0] for L in sorted(ranking)), flush=True)
    print("  shaft wins: " + ", ".join(
        f"{s}:{n}/{len(ranking)}" for s, n in sorted(wins.items(), key=lambda kv: -kv[1])),
        flush=True)

    def shaft_votes(top_n):
        votes = defaultdict(int)
        for order in ranking.values():
            for c in order[:top_n]:
                votes[shaft_of(c)] += 1
        return {s: v / shaft_size[s] for s, v in votes.items()}

    # Any single N is an argument, not a result -- read the sweep.
    print("\n=== cutoff sweep (votes/ch; `chance` is a random top-N) ===", flush=True)
    for top_n in (5, 10, 20, 30):
        norm = shaft_votes(top_n)
        chance = len(ranking) * top_n / n_contacts
        top = sorted(norm, key=lambda s: -norm[s])[:8]
        print(f"top {top_n:2d} (chance {chance:.2f}):  " +
              "  ".join(f"{s}:{norm[s]:.2f}" for s in top), flush=True)

    n10, n20 = shaft_votes(10), shaft_votes(20)
    print(f"\n{'shaft':>6} {'n':>4} {'EI@10':>7} {'EI@20':>7}  clinical", flush=True)
    for sh in sorted(shaft_size, key=lambda s: -n20.get(s, 0)):
        tag = ("EEG ONSET" if sh in GT_ONSET_SHAFTS
               else "early spread" if sh in GT_SPREAD_SHAFTS else "")
        print(f"{sh:>6} {shaft_size[sh]:4d} {n10.get(sh, 0):7.2f} "
              f"{n20.get(sh, 0):7.2f}  {tag}", flush=True)

    # Primed shafts are the contralateral side; equal shares mean no lateralisation.
    n_primed = sum(n for s, n in shaft_size.items() if s.endswith("'"))
    print(f"\nimplant: primed {n_primed}/{n_contacts} = {n_primed / n_contacts:.0%}",
          flush=True)
    for top_n in (10, 20):
        votes = defaultdict(int)
        for order in ranking.values():
            for c in order[:top_n]:
                votes[shaft_of(c)] += 1
        primed = sum(v for s, v in votes.items() if s.endswith("'"))
        total = sum(votes.values())
        print(f"top {top_n}: primed {primed}/{total} = {primed / total:.0%}", flush=True)

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["seizure", "contact", "shaft", "ei", "rank"])
            for label in sorted(ranking):
                for i, (c, v) in enumerate(zip(ranking[label], values[label]), 1):
                    w.writerow([label, c, shaft_of(c), f"{v:.6f}", i])
        print(f"\nwrote {sum(len(v) for v in ranking.values())} rows -> {args.out}",
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
