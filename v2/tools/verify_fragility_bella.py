#!/usr/bin/env python3
"""Validate pure Python Neural Fragility (app.sigproc.fragility) on Bella's 8 seizures.

Cross-validates the size-normalised shaft ranking against the reference EZFragility (R)
output. That file is expected to hold one row per shaft, shaft label as the first token
and votes/ch as the last numeric token -- the format this script itself prints. Exits
nonzero if the rankings disagree; without a reference file it only prints the ranking.
"""

import argparse
import csv
import os
import re
import sys
import time
from collections import Counter
import mne
import numpy as np
from scipy.stats import spearmanr

# Add server directory to path
SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../server"))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from app.sigproc.fragility import compute_fragility_pipeline

SEEG_CONTACT_RE = re.compile(r"^[A-Za-z]'?\d+$")
SHAFT_RE = re.compile(r"^([A-Za-z]+'?)\d+$")

DEFAULT_EDF_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../datasets/BellaNew"))

SEIZURE_FILES = [
    ("SZ1P", "DA6465AU_17_20240319072231.edf", r"^SZ 1P$"),
    ("SZ2P", "DA6465AU_20_20240319173404.edf", r"^SZ 2P$"),
    ("SZ3P", "DA6465AU_21_20240319221721.edf", r"^SZ 3P$"),
    ("SZ4P", "DA6465AU_22_20240320060204.edf", r"^SZ 4P$"),
    ("SZ5P", "DA6465AU_26_20240321033634.edf", r"^SZ 5P$"),
    ("SZ6P", "DA6465AU_27_20240321070053.edf", r"^SZ 6P$"),
    ("SZ7P", "DA6465AU_29_20240321125835.edf", r"^SZ 7P$"),
    ("SZ8P", "DA6465AU_44_20240322221053.edf", r"^SZ 8P$"),
]

GT_ONSET_SHAFTS = {"A", "I"}
GT_SPREAD_SHAFTS = {"N", "P", "G", "L", "K", "Q", "S"}

DEFAULT_REF = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/ezfragility_result.txt"))
# Defaults mirror run_frag.R (PRE/POST/ICTAL_END/TOP_N). They have to: a parity
# check run on a different window compares windows, not implementations.
EVAL_WINDOW_S = (0.0, 5.0)  # score the ictal period only; pre-onset baseline is context
CROP_PRE_S = 20.0
CROP_POST_S = 10.0
TOP_N_CONTACTS = 20
SPEARMAN_MIN = 0.8


def get_shaft(contact_name: str) -> str:
    m = SHAFT_RE.match(contact_name.strip())
    return m.group(1) if m else contact_name


def load_reference(path: str) -> dict[str, float]:
    """Parse shaft -> votes/ch from an EZFragility shaft-ranking table."""
    ref = {}
    with open(path) as fh:
        for line in fh:
            tokens = line.split()
            if not tokens or not re.fullmatch(r"[A-Za-z]+'?", tokens[0]):
                continue
            floats = [float(t) for t in tokens[1:] if re.fullmatch(r"-?\d+(\.\d+)?", t)]
            if floats:
                ref[tokens[0]] = floats[-1]
    return ref


def compare_to_reference(ranked_shafts: list[tuple[str, float]], ref_path: str) -> int:
    """Print the Python-vs-R comparison; return a process exit code."""
    if not os.path.exists(ref_path):
        print(f"\nNo reference at {ref_path} -- skipping cross-validation.", flush=True)
        print("Parity against EZFragility is UNVERIFIED.", flush=True)
        return 0

    ref = load_reference(ref_path)
    if not ref:
        print(f"\nERROR: parsed no shaft rows from {ref_path}.", flush=True)
        return 1

    ours = dict(ranked_shafts)
    shared = [s for s in ours if s in ref]
    if len(shared) < 3:
        print(f"\nERROR: only {len(shared)} shafts in common with the reference.", flush=True)
        return 1

    rho = spearmanr([ours[s] for s in shared], [ref[s] for s in shared]).statistic
    our_top = max(shared, key=lambda s: ours[s])
    ref_top = max(shared, key=lambda s: ref[s])

    print(f"\n=== CROSS-VALIDATION vs EZFragility ({os.path.basename(ref_path)}) ===", flush=True)
    print(f"{'shaft':>6} {'python':>9} {'R':>9}", flush=True)
    for s in sorted(shared, key=lambda s: ref[s], reverse=True):
        print(f"{s:>6} {ours[s]:>9.2f} {ref[s]:>9.2f}", flush=True)
    print(f"\nSpearman over {len(shared)} shafts: {rho:.4f} (threshold {SPEARMAN_MIN})", flush=True)
    print(f"top shaft: python={our_top}  R={ref_top}", flush=True)

    failures = []
    if not (rho >= SPEARMAN_MIN):
        failures.append(f"Spearman {rho:.4f} < {SPEARMAN_MIN}")
    if our_top != ref_top:
        failures.append(f"top shaft {our_top} != {ref_top}")

    if failures:
        print("PARITY FAILED: " + "; ".join(failures), flush=True)
        return 1
    print("PARITY OK", flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default=DEFAULT_REF, help="EZFragility shaft-ranking table")
    parser.add_argument("--method", choices=["ezfragility", "extended"], default="extended")
    parser.add_argument("--edf-dir", default=DEFAULT_EDF_DIR,
                        help="directory holding the 8 seizure EDFs")
    parser.add_argument("-o", "--out", help="write per-contact scores to this CSV")
    parser.add_argument("--top-n", type=int, default=TOP_N_CONTACTS,
                        help="top contacts per seizure that vote for their shaft")
    parser.add_argument("--pre", type=float, default=CROP_PRE_S, help="seconds before onset")
    parser.add_argument("--post", type=float, default=CROP_POST_S, help="seconds after onset")
    parser.add_argument("--eval-end", type=float, default=EVAL_WINDOW_S[1],
                        help="score windows starting in [0, eval-end] s")
    parser.add_argument("--exclude", default="",
                        help="comma-separated contacts to drop before the CAR, "
                             "e.g. \"L'9,K'12,G'4\"")
    args = parser.parse_args()

    edf_dir = os.path.abspath(args.edf_dir)
    print(f"Loading Bella EDFs from: {edf_dir}", flush=True)
    if not os.path.exists(edf_dir):
        print(f"Error: {edf_dir} not found.", flush=True)
        sys.exit(1)

    crop_pre = args.pre
    crop_post = args.post
    eval_window = (EVAL_WINDOW_S[0], args.eval_end)
    top_n_contacts = args.top_n
    excluded = {c.strip() for c in args.exclude.split(",") if c.strip()}
    print(f"window [-{crop_pre:g}, +{crop_post:g}] s, scored over "
          f"[{eval_window[0]:g}, {eval_window[1]:g}] s, top-{top_n_contacts} vote",
          flush=True)
    if excluded:
        print(f"excluding {len(excluded)} contacts: {', '.join(sorted(excluded))}",
              flush=True)

    all_results = {}
    shaft_sizes = None

    t_total_start = time.time()
    for label, filename, pattern in SEIZURE_FILES:
        edf_path = os.path.join(edf_dir, filename)
        if not os.path.exists(edf_path):
            print(f"Warning: {edf_path} not found, skipping {label}.", flush=True)
            continue

        raw = mne.io.read_raw_edf(edf_path, preload=False, stim_channel=None, verbose="error")
        fs = float(raw.info["sfreq"])
        all_ch = list(raw.ch_names)
        contacts = [c for c in all_ch
                    if SEEG_CONTACT_RE.match(c.strip()) and c.strip() not in excluded]

        if shaft_sizes is None:
            shaft_sizes = Counter(get_shaft(c) for c in contacts)

        # Find onset from annotations
        rx = re.compile(pattern)
        onset_sec = None
        for onset, desc in zip(raw.annotations.onset, raw.annotations.description):
            if rx.search(str(desc)):
                onset_sec = float(onset)
                break
        if onset_sec is None:
            onset_sec = 120.0  # Fallback to standard 120s mark

        t_start = onset_sec - crop_pre
        t_end = onset_sec + crop_post
        picks = [all_ch.index(c) for c in contacts]

        i0 = int(round(t_start * fs))
        i1 = int(round(t_end * fs)) + 1
        data = raw.get_data(picks=picks, start=i0, stop=i1) * 1e6  # Volts -> uV
        # CAR
        data = data - data.mean(axis=0, keepdims=True)

        t0 = time.time()
        res = compute_fragility_pipeline(
            data=data,
            fs=fs,
            ch_names=contacts,
            win_s=0.25,
            step_s=0.125,
            radius=1.0,
            method=args.method,
            eval_window_s=eval_window,
            onset_s=crop_pre,  # data starts at -crop_pre relative to onset
        )
        elapsed = time.time() - t0

        all_results[label] = res["channel_scores"]
        top_10 = [ch for ch, score in res["ranked_channels"][:10]]
        print(
            f"{label}: {len(contacts)} electrodes x {res['fragility_matrix'].shape[1]} windows | "
            f"median R2 {res['median_r2']:.3f} | {elapsed:.2f}s",
            flush=True,
        )
        print(f"    top 10: {', '.join(top_10)}", flush=True)

    total_elapsed = time.time() - t_total_start
    print(f"\nAll 8 seizures processed in {total_elapsed:.2f}s total!", flush=True)

    if not all_results:
        print("No seizures evaluated.", flush=True)
        return 1

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["seizure", "contact", "shaft", "fragility"])
            for label, ch_scores in all_results.items():
                for ch, score in ch_scores.items():
                    w.writerow([label, ch, get_shaft(ch), f"{score:.6f}"])
        n = sum(len(s) for s in all_results.values())
        print(f"\nwrote {n} rows -> {args.out}", flush=True)

    # Aggregate votes to shafts
    shaft_votes = Counter()
    for label, ch_scores in all_results.items():
        sorted_ch = sorted(ch_scores.items(), key=lambda item: item[1], reverse=True)
        top_contacts = [ch for ch, score in sorted_ch[:top_n_contacts]]
        for ch in top_contacts:
            shaft = get_shaft(ch)
            shaft_votes[shaft] += 1

    # Normalize by shaft size
    shaft_normalized = {
        s: shaft_votes[s] / shaft_sizes[s]
        for s in shaft_sizes if shaft_sizes[s] > 0
    }
    ranked_shafts = sorted(shaft_normalized.items(), key=lambda item: item[1], reverse=True)

    print("\n=== PYTHON NEURAL FRAGILITY: shaft ranking (size-normalised) ===", flush=True)
    print(f"{'shaft':>6} {'n':>7} {'votes':>7} {'votes/ch':>11}   {'clinical'}", flush=True)
    for shaft, norm_vote in ranked_shafts:
        if shaft_votes[shaft] == 0:
            continue
        tag = ""
        if shaft in GT_ONSET_SHAFTS:
            tag = "<<< EEG ONSET"
        elif shaft in GT_SPREAD_SHAFTS:
            tag = "early spread"
        print(
            f"{shaft:>6} {shaft_sizes[shaft]:>7} {shaft_votes[shaft]:>7} "
            f"{norm_vote:>11.2f}   {tag}",
            flush=True,
        )

    top_shaft = ranked_shafts[0][0]
    print(f"\nTop identified shaft: {top_shaft} (votes/ch: {ranked_shafts[0][1]:.2f})", flush=True)

    return compare_to_reference(ranked_shafts, args.ref)


if __name__ == "__main__":
    sys.exit(main())
