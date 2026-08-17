#!/usr/bin/env python3
"""Compare app.sigproc.fragility against the stored EZFragility results on Bella's 8 seizures.

Runs the Python pipeline over the same exported clips EZFragility consumed
(data/fragility/export/SZ*.csv) using EZFragility's own parameters, so the only
difference left is the implementation. R's side is read from
frag_full/{label}_frag.csv, written by v2/tools/fragility/frag_to_csv.R -- R is
never re-run here.
"""

import argparse
import csv
import os
import re
import sys
import time
from collections import Counter

import numpy as np
from scipy.stats import spearmanr

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "server"))

from app.sigproc.fragility import compute_fragility_pipeline  # noqa: E402

# EZFragility's settings, from v2/tools/fragility/frag_compute.R + run_frag.R
FS = 1000.0
PRE = 20.0            # s of pre-roll in the export; puts onset at t = 0
WIN_S, STEP_S = 0.250, 0.125
ICTAL = (0.0, 5.0)    # window the per-contact score averages over
TOP_N = 20            # contacts per seizure that vote for their shaft

SHAFT_RE = re.compile(r"^([A-Za-z]+'?)\d+$")
GT_ONSET_SHAFTS = {"A", "I"}


def get_shaft(contact: str) -> str:
    m = SHAFT_RE.match(contact.strip())
    return m.group(1) if m else contact


def read_labelled_matrix(path: str) -> tuple[list[str], np.ndarray]:
    """Read an R write.csv matrix: header row of column names, row names in column 0."""
    with open(path, newline="") as fh:
        rows = list(csv.reader(fh))
    names = [r[0].strip().strip('"') for r in rows[1:]]
    values = np.array([[float(v) for v in r[1:]] for r in rows[1:]], dtype=np.float64)
    return names, values


def score_window(frag: np.ndarray, times: np.ndarray) -> np.ndarray:
    sel = np.where((times >= ICTAL[0]) & (times <= ICTAL[1]))[0]
    if not len(sel):
        raise ValueError(f"no windows in {ICTAL}")
    return frag[:, sel].mean(axis=1)


def shaft_table(scores: dict[str, float], sizes: Counter) -> tuple[dict, dict]:
    """Size-normalised mean fragility per shaft, and top-N votes/ch."""
    by_shaft = {}
    for ch, v in scores.items():
        by_shaft.setdefault(get_shaft(ch), []).append(v)
    means = {s: float(np.mean(v)) for s, v in by_shaft.items()}

    top = sorted(scores, key=scores.get, reverse=True)[:TOP_N]
    votes = Counter(get_shaft(c) for c in top)
    per_ch = {s: votes[s] / sizes[s] for s in sizes if sizes[s]}
    return means, per_ch


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=os.path.join(REPO, "..", "data", "fragility", "export"))
    ap.add_argument("--labels", nargs="*", default=[f"SZ{i}P" for i in range(1, 9)])
    args = ap.parse_args()

    data_dir = os.path.abspath(args.data_dir)
    frag_dir = os.path.join(data_dir, "frag_full")
    if not os.path.isdir(frag_dir):
        sys.exit(f"no frag_full/ under {data_dir}; run frag_to_csv.R first")

    py_scores, r_scores, sizes = {}, {}, None
    print(f"{'seizure':>8} {'contacts':>9} {'py win':>7} {'R win':>6} {'spearman':>9} "
          f"{'top10':>6} {'top20':>6} {'top1 py':>8} {'top1 R':>8} {'secs':>6}", flush=True)

    for label in args.labels:
        csv_path = os.path.join(data_dir, f"{label}.csv")
        ch_path = os.path.join(data_dir, f"{label}_ch.txt")
        r_path = os.path.join(frag_dir, f"{label}_frag.csv")
        if not all(os.path.exists(p) for p in (csv_path, ch_path, r_path)):
            print(f"{label}: missing inputs, skipped", flush=True)
            continue

        data = np.loadtxt(csv_path, delimiter=",", dtype=np.float64)
        contacts = [c.strip() for c in open(ch_path) if c.strip()]
        if data.shape[0] != len(contacts):
            sys.exit(f"{label}: {data.shape[0]} rows but {len(contacts)} channel names")

        t0 = time.time()
        res = compute_fragility_pipeline(
            data=data, fs=FS, ch_names=contacts,
            win_s=WIN_S, step_s=STEP_S, eval_window_s=ICTAL, onset_s=PRE,
        )
        elapsed = time.time() - t0
        py = res["channel_scores"]

        r_contacts, r_frag = read_labelled_matrix(r_path)
        r_times = np.loadtxt(os.path.join(frag_dir, f"{label}_times.csv"), skiprows=1)
        r = dict(zip(r_contacts, score_window(r_frag, r_times)))

        shared = [c for c in contacts if c in r]
        a = np.array([py[c] for c in shared])
        b = np.array([r[c] for c in shared])
        rho = spearmanr(a, b).statistic
        top_py = sorted(shared, key=lambda c: py[c], reverse=True)
        top_r = sorted(shared, key=lambda c: r[c], reverse=True)
        ov10 = len(set(top_py[:10]) & set(top_r[:10]))
        ov20 = len(set(top_py[:20]) & set(top_r[:20]))

        print(f"{label:>8} {len(shared):>9} {res['fragility_matrix'].shape[1]:>7} "
              f"{r_frag.shape[1]:>6} {rho:>9.4f} {ov10:>4}/10 {ov20:>4}/20 "
              f"{top_py[0]:>8} {top_r[0]:>8} {elapsed:>6.1f}", flush=True)

        py_scores[label], r_scores[label] = py, r
        if sizes is None:
            sizes = Counter(get_shaft(c) for c in shared)

    if not py_scores:
        sys.exit("nothing compared")

    print("\n=== shaft ranking, python vs EZFragility "
          "(mean fragility over shaft, and top-20 votes/ch) ===", flush=True)
    py_mean = Counter()
    r_mean = Counter()
    py_votes = Counter()
    r_votes = Counter()
    for label in py_scores:
        pm, pv = shaft_table(py_scores[label], sizes)
        rm, rv = shaft_table(r_scores[label], sizes)
        for s in pm:
            py_mean[s] += pm[s] / len(py_scores)
            r_mean[s] += rm[s] / len(py_scores)
        for s in sizes:
            py_votes[s] += pv.get(s, 0.0)
            r_votes[s] += rv.get(s, 0.0)

    order = sorted(r_mean, key=r_mean.get, reverse=True)
    print(f"{'shaft':>6} {'n':>4} {'py mean':>9} {'R mean':>9} {'py v/ch':>9} {'R v/ch':>8}   clinical",
          flush=True)
    for s in order:
        tag = "<<< clinical SOZ" if s in GT_ONSET_SHAFTS else ""
        print(f"{s:>6} {sizes[s]:>4} {py_mean[s]:>9.4f} {r_mean[s]:>9.4f} "
              f"{py_votes[s]:>9.2f} {r_votes[s]:>8.2f}   {tag}", flush=True)

    rho_shaft = spearmanr([py_mean[s] for s in order], [r_mean[s] for s in order]).statistic
    print(f"\nshaft-level Spearman (mean fragility): {rho_shaft:.4f}", flush=True)
    print(f"top shaft: python={max(py_mean, key=py_mean.get)}  R={max(r_mean, key=r_mean.get)}",
          flush=True)


if __name__ == "__main__":
    main()
