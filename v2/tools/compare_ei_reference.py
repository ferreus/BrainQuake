#!/usr/bin/env python3
"""Paired comparison of two EI verification runs (e.g. CAR vs bipolar) on ds004100.

Compares only runs that succeeded in BOTH arms, so neither is credited for a
different denominator. Subject-level means are the headline: runs from one
patient share an implantation and a focus, so treating 213 runs as 213
independent samples overstates confidence.
"""
import argparse
import csv
from collections import defaultdict

import numpy as np
from scipy.stats import wilcoxon

METRICS = ["soz_recall", "resect_concordance"]
HIT_METRICS = ["soz_hit_count", "resect_hit_count"]


def read_rows(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def index_successful(rows):
    return {r["run_id"]: r for r in rows if r["status"] == "SUCCESS"}


def as_float(row, key):
    try:
        return float(row[key])
    except (TypeError, ValueError):
        return 0.0


def summarise(rows_a, rows_b, label_a, label_b, name):
    """One comparison block over a matched list of (row_a, row_b)."""
    n = len(rows_a)
    if n == 0:
        print(f"\n{name}: no paired runs")
        return

    print(f"\n{'=' * 78}\n{name}  (n = {n} paired runs)\n{'=' * 78}")
    subjects = sorted({r["subject"] for r in rows_a})
    print(f"{'metric':<28} {label_a:>12} {label_b:>12} {'delta':>10}  {'wins':>12}")
    print("-" * 78)

    for metric in METRICS:
        a = np.array([as_float(r, metric) for r in rows_a])
        b = np.array([as_float(r, metric) for r in rows_b])
        wins_b = int((b > a).sum())
        wins_a = int((a > b).sum())
        print(f"{metric:<28} {a.mean():>11.2%} {b.mean():>11.2%} "
              f"{b.mean() - a.mean():>+9.2%}  {wins_b:>5}/{wins_a:<5} b/a")

    for metric in HIT_METRICS:
        a = np.array([as_float(r, metric) > 0 for r in rows_a], dtype=float)
        b = np.array([as_float(r, metric) > 0 for r in rows_b], dtype=float)
        name_ = metric.replace("_count", "_rate (>=1)")
        print(f"{name_:<28} {a.mean():>11.2%} {b.mean():>11.2%} {b.mean() - a.mean():>+9.2%}")

    # Subject-level: mean per subject first, so a patient with 9 runs does not
    # outvote one with 1.
    print(f"\n  per-subject means (n = {len(subjects)} subjects)")
    for metric in METRICS:
        by_sub_a, by_sub_b = defaultdict(list), defaultdict(list)
        for ra, rb in zip(rows_a, rows_b):
            by_sub_a[ra["subject"]].append(as_float(ra, metric))
            by_sub_b[rb["subject"]].append(as_float(rb, metric))
        a = np.array([np.mean(by_sub_a[s]) for s in subjects])
        b = np.array([np.mean(by_sub_b[s]) for s in subjects])
        delta = b - a
        try:
            stat, p = wilcoxon(a, b)
            p_str = f"p = {p:.4f}"
        except ValueError as e:  # all-zero differences
            p_str = f"p = n/a ({e})"
        print(f"  {metric:<26} {a.mean():>11.2%} {b.mean():>11.2%} {delta.mean():>+9.2%}  "
              f"{p_str}  (better on {int((delta > 0).sum())}/{len(subjects)} subjects)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv_a")
    ap.add_argument("csv_b")
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    args = ap.parse_args()

    a_all, b_all = read_rows(args.csv_a), read_rows(args.csv_b)
    a_ok, b_ok = index_successful(a_all), index_successful(b_all)
    shared = sorted(set(a_ok) & set(b_ok))

    print(f"{args.label_a}: {len(a_ok)}/{len(a_all)} runs succeeded")
    print(f"{args.label_b}: {len(b_ok)}/{len(b_all)} runs succeeded")
    print(f"paired (succeeded in both): {len(shared)}")
    only_a, only_b = sorted(set(a_ok) - set(b_ok)), sorted(set(b_ok) - set(a_ok))
    for label, only in ((args.label_a, only_a), (args.label_b, only_b)):
        if only:
            print(f"  dropped, {label}-only ({len(only)}): {only[:4]}")

    rows_a = [a_ok[r] for r in shared]
    rows_b = [b_ok[r] for r in shared]
    summarise(rows_a, rows_b, args.label_a, args.label_b, "ALL ACQUISITIONS")

    for acq in sorted({r.get("acq", "unknown") for r in rows_a}):
        idx = [i for i, r in enumerate(rows_a) if r.get("acq") == acq]
        summarise([rows_a[i] for i in idx], [rows_b[i] for i in idx],
                  args.label_a, args.label_b, f"acq-{acq}")


if __name__ == "__main__":
    main()
