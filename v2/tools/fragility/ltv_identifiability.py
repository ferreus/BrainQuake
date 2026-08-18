#!/usr/bin/env python3
"""Is the LTV fit's instability a dimensionality problem, or a regularization one?

At 184 channels and a 250 ms window each row of A is fit from 249 sample pairs --
1.35 observations per parameter -- so the fit nearly interpolates and its
eigenvalues scatter across the unit circle. app.sigproc.fragility works around
that with l2_reg=0.3 plus an escalation search, which buys stability at the cost
of dynamic range (shaft-mean CV 2.49% vs EZFragility's 8.25%).

This sweeps window length (raises T) and per-shaft fits (cuts N) at FIXED lambda,
measuring rho(A) directly. Measurement only: fragility.py is not modified.

    python ltv_identifiability.py --stage rho
    python ltv_identifiability.py --stage frag --configs 1.0:joint:1e-2 --highpass 0.5
"""

import os

# A 32-thread OpenBLAS pool makes the 184x184 LAPACK Schur ~6x slower than serial
# here; parallelism belongs at the process level. Must precede the numpy import.
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse
import csv
import sys
import time
import warnings
from collections import Counter, defaultdict
from multiprocessing import Pool

import numpy as np
import scipy.linalg
from scipy.linalg import LinAlgWarning
from scipy.stats import spearmanr

# The N ~= T regime under test is ill-conditioned by construction; that is the finding.
warnings.filterwarnings("ignore", category=LinAlgWarning)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "server"))
sys.path.insert(0, os.path.join(REPO, "tools"))

from app.sigproc.fragility import compute_min_perturbations  # noqa: E402
from app.sigproc.filters import bandpass  # noqa: E402
from compare_fragility_r import (  # noqa: E402
    FS, PRE, ICTAL, get_shaft, read_labelled_matrix, score_window,
)

# Held constant across every arm: identical step means the window STARTS inside
# ICTAL are the same set regardless of win_s, so contact scores stay comparable.
STEP_S = 0.125

WIN_GRID = [0.25, 0.5, 1.0]
L2_GRID = [0.0, 1e-6, 1e-4, 1e-3, 1e-2, 0.3]
MODES = ["joint", "shaft"]
LABELS = [f"SZ{i}P" for i in range(1, 9)]
NUM_FREQS = 16
REPORT_SHAFTS = ["A", "I", "D"]

FIELDS = [
    "win_s", "step_s", "mode", "l2_reg", "l2_effective", "seizure", "n_windows",
    "n_blocks", "obs_per_param", "rho_median", "rho_p90", "rho_frac_unstable",
    "median_r2", "r_lambda_median", "frag_computed", "spearman", "top10", "top20",
    "shaft_cv_pct", "rank_A", "rank_I", "rank_D", "size_delta_rho", "secs",
]


def channel_groups(contacts, mode, min_shaft=4):
    """Index blocks to fit independently. joint -> one block of all channels."""
    if mode == "joint":
        return [("all", np.arange(len(contacts)))]
    by = defaultdict(list)
    for i, c in enumerate(contacts):
        by[get_shaft(c)].append(i)
    groups, misc = [], []
    for s, idx in sorted(by.items()):
        if len(idx) >= min_shaft:
            groups.append((s, np.array(idx)))
        else:
            misc.extend(idx)
    if misc:
        # Pooled, not dropped: dropping would shorten the vector compared to R.
        groups.append(("misc", np.array(sorted(misc))))
    return groups


def window_grams(data, starts, win_samples, groups):
    """(cov, cross, sum(X2^2), ss_tot) per window per block; raw windows not retained."""
    out = []
    for s0 in starts:
        W = data[:, s0 : s0 + win_samples]
        per = []
        for _, idx in groups:
            X1, X2 = W[idx, :-1], W[idx, 1:]
            per.append((
                X1 @ X1.T,
                X1 @ X2.T,
                float(np.sum(X2 * X2)),
                float(np.sum((X2 - X2.mean(axis=1, keepdims=True)) ** 2)),
            ))
        out.append(per)
    return out


def fit_fixed(cov, cross, l2):
    """Ridge at a FIXED lambda. fit_ltv_model always escalates to stability, and
    escalation is the thing being measured away, so it cannot be used here."""
    n = cov.shape[0]
    scale = float(np.trace(cov)) / n
    c = cov + (l2 * scale) * np.eye(n) if (l2 > 0 and scale > 0) else cov
    try:
        return scipy.linalg.solve(c, cross, assume_a="pos").T, l2 * scale
    except (scipy.linalg.LinAlgError, ValueError):
        return (np.linalg.pinv(c) @ cross).T, l2 * scale


def r2_from_gram(A, cov, cross, s22, ss_tot):
    ss_res = s22 - 2.0 * float(np.sum(A * cross.T)) + float(np.trace(A @ cov @ A.T))
    return max(0.0, 1.0 - ss_res / ss_tot) if ss_tot > 1e-12 else 0.0


def spectral_radius(A):
    return float(np.max(np.abs(scipy.linalg.eigvals(A))))


def window_deltas(mats, groups, n_ch):
    """Raw min-perturbation norms in channel order -- deliberately NOT normalized.
    Per-shaft blocks must be pooled and normalized once, or every shaft gets a 1.0."""
    d = np.empty(n_ch)
    for (_, idx), A in zip(groups, mats, strict=True):
        d[idx] = compute_min_perturbations(A, radius=1.0, num_freqs=NUM_FREQS)
    return d


def load_clip(data_dir, label, highpass=0.0):
    data = np.loadtxt(os.path.join(data_dir, f"{label}.csv"), delimiter=",", dtype=np.float64)
    if highpass > 0:
        # Li et al.'s 4th-order Butterworth. Filtering commutes with the export's
        # CAR, so applying it here matches their preprocessing order.
        data = bandpass(data, FS, highpass, FS / 2.0, order=4, context="ltv sweep")
    contacts = [c.strip() for c in open(os.path.join(data_dir, f"{label}_ch.txt")) if c.strip()]
    if data.shape[0] != len(contacts):
        sys.exit(f"{label}: {data.shape[0]} rows but {len(contacts)} channel names")
    return data, contacts


def run_unit(job):
    """All (mode, l2) for one (label, win_s). Grams are built once and reused."""
    label, win_s, data_dir, l2_grid, modes, want_frag, highpass = job
    t0 = time.time()
    data, contacts = load_clip(data_dir, label, highpass)
    n_ch, total = data.shape
    win_samples = int(round(win_s * FS))
    step_samples = int(round(STEP_S * FS))
    starts = np.arange(0, total - win_samples + 1, step_samples)
    start_times = starts / FS - PRE

    rows = []
    for mode in modes:
        groups = channel_groups(contacts, mode)
        grams = window_grams(data, starts, win_samples, groups)
        n_par = max(len(idx) for _, idx in groups)
        for l2 in l2_grid:
            t1 = time.time()
            rhos, r2s, l2_eff = [], [], []
            mats_all = []
            for per in grams:
                mats = []
                for cov, cross, s22, ss_tot in per:
                    A, eff = fit_fixed(cov, cross, l2)
                    if want_frag:
                        mats.append(A)
                    rhos.append(spectral_radius(A))
                    r2s.append(r2_from_gram(A, cov, cross, s22, ss_tot))
                    l2_eff.append(eff)
                mats_all.append(mats)

            rec = {
                "win_s": win_s, "step_s": STEP_S, "mode": mode, "l2_reg": l2,
                "l2_effective": float(np.median(l2_eff)), "seizure": label,
                "n_windows": len(starts), "n_blocks": len(groups),
                "obs_per_param": (win_samples - 1) / n_par,
                "rho_median": float(np.median(rhos)), "rho_p90": float(np.percentile(rhos, 90)),
                "rho_frac_unstable": float(np.mean(np.array(rhos) >= 1.0)),
                "median_r2": float(np.median(r2s)), "frag_computed": int(want_frag),
            }

            if want_frag:
                raw = np.array([window_deltas(m, groups, n_ch) for m in mats_all]).T
                mx = raw.max(axis=0, keepdims=True)  # one global max per window
                frag = np.where(mx > 1e-12, 1.0 - raw / np.maximum(mx, 1e-300), 0.0)
                rec["_scores"] = dict(zip(contacts, score_window(frag, start_times), strict=True))
                sel = np.where((start_times >= ICTAL[0]) & (start_times <= ICTAL[1]))[0]
                rec["_raw_delta"] = dict(zip(contacts, raw[:, sel].mean(axis=1), strict=True))
            rec["secs"] = time.time() - t1
            rows.append(rec)
        del grams
    sys.stderr.write(f"  {label} win={win_s} done in {time.time()-t0:.0f}s\n")
    return rows


def shaft_means(scores):
    by = defaultdict(list)
    for ch, v in scores.items():
        by[get_shaft(ch)].append(v)
    return {s: float(np.mean(v)) for s, v in by.items()}


def shaft_cv(means):
    """Dynamic range across shafts. R's reference is 8.25%, production's 2.49%.
    ddof=1 to match how those two reference numbers were computed."""
    v = np.array(list(means.values()))
    return 100.0 * float(np.std(v, ddof=1)) / float(np.mean(v)) if v.mean() > 1e-12 else float("nan")


def rank_of(means, shaft):
    order = sorted(means, key=means.get, reverse=True)
    return order.index(shaft) + 1 if shaft in order else float("nan")


def load_r_reference(frag_dir, labels):
    """Contact scores and the lambdas EZFragility actually used, per seizure."""
    ref, lam = {}, {}
    for label in labels:
        fp = os.path.join(frag_dir, f"{label}_frag.csv")
        tp = os.path.join(frag_dir, f"{label}_times.csv")
        if not (os.path.exists(fp) and os.path.exists(tp)):
            continue
        names, frag = read_labelled_matrix(fp)
        ref[label] = dict(zip(names, score_window(frag, np.loadtxt(tp, skiprows=1)), strict=True))
        lp = os.path.join(frag_dir, f"{label}_lambdas.csv")
        if os.path.exists(lp):
            lam[label] = float(np.median(read_labelled_matrix(lp)[1]))
    return ref, lam


def aggregate(rows, ref, r_lam, sizes):
    """Collapse per-seizure records into one ALL row per config, adding parity metrics."""
    by = defaultdict(list)
    for r in rows:
        by[(r["win_s"], r["mode"], r["l2_reg"])].append(r)

    out = []
    for _, recs in sorted(by.items()):
        agg = {k: recs[0][k] for k in ("win_s", "step_s", "mode", "l2_reg", "n_blocks",
                                       "obs_per_param", "frag_computed")}
        agg["seizure"] = "ALL"
        for k in ("l2_effective", "rho_median", "rho_p90", "rho_frac_unstable", "median_r2"):
            agg[k] = float(np.mean([r[k] for r in recs]))
        agg["n_windows"] = int(np.sum([r["n_windows"] for r in recs]))
        agg["secs"] = float(np.sum([r["secs"] for r in recs]))
        if r_lam:
            agg["r_lambda_median"] = float(np.median(list(r_lam.values())))

        if recs[0].get("_scores"):
            # Mean over seizures of each seizure's per-shaft mean -- the same
            # quantity compare_fragility_r.py's py_mean table reports.
            acc, dacc, sp, t10, t20 = defaultdict(list), defaultdict(list), [], [], []
            for r in recs:
                for s, v in shaft_means(r["_scores"]).items():
                    acc[s].append(v)
                for s, v in shaft_means(r["_raw_delta"]).items():
                    dacc[s].append(v)
                if r["seizure"] in ref:
                    shared = [c for c in r["_scores"] if c in ref[r["seizure"]]]
                    a = [r["_scores"][c] for c in shared]
                    b = [ref[r["seizure"]][c] for c in shared]
                    sp.append(spearmanr(a, b).statistic)
                    pt = sorted(shared, key=lambda c: r["_scores"][c], reverse=True)
                    rt = sorted(shared, key=lambda c: ref[r["seizure"]][c], reverse=True)
                    t10.append(len(set(pt[:10]) & set(rt[:10])))
                    t20.append(len(set(pt[:20]) & set(rt[:20])))
            means = {s: float(np.mean(v)) for s, v in acc.items()}
            agg["shaft_cv_pct"] = shaft_cv(means)
            for s in REPORT_SHAFTS:
                agg[f"rank_{s}"] = rank_of(means, s)
            if sp:
                agg["spearman"] = float(np.mean(sp))
                agg["top10"] = float(np.mean(t10))
                agg["top20"] = float(np.mean(t20))
            # Delta norm shrinks with block size, so per-shaft pooling can carry a
            # size artifact. Strongly negative here means the ranking is confounded.
            dmean = {s: float(np.mean(v)) for s, v in dacc.items()}
            common = [s for s in dmean if s in sizes]
            if len(common) > 3:
                agg["size_delta_rho"] = float(
                    spearmanr([sizes[s] for s in common], [dmean[s] for s in common]).statistic)
        out.append(agg)
    return out


HDR = (f"{'win':>5} {'mode':>6} {'l2':>8} {'ob/pr':>6} {'rho_med':>8} {'rho_p90':>8} "
       f"{'unstb%':>7} {'R2':>6} {'shCV%':>7} {'spear':>6} {'t10':>4} {'t20':>4} "
       f"{'A':>3} {'I':>3} {'D':>3} {'szD':>6}")


def fmt(a):
    def g(k, spec, scale=1.0):
        v = a.get(k)
        return format(v * scale, spec) if isinstance(v, (int, float)) and np.isfinite(v) else "--"
    return (f"{a['win_s']:>5.2f} {a['mode']:>6} {a['l2_reg']:>8.0e} "
            f"{a['obs_per_param']:>6.2f} {g('rho_median','>8.4f')} {g('rho_p90','>8.4f')} "
            f"{g('rho_frac_unstable','>7.1f',100):>7} {g('median_r2','>6.3f')} "
            f"{g('shaft_cv_pct','>7.2f')} {g('spearman','>6.3f')} {g('top10','>4.1f')} "
            f"{g('top20','>4.1f')} {g('rank_A','>3.0f')} {g('rank_I','>3.0f')} "
            f"{g('rank_D','>3.0f')} {g('size_delta_rho','>6.2f')}")


def parse_configs(spec):
    out = []
    for part in spec.split(","):
        w, m, lam = part.split(":")
        out.append((float(w), m, float(lam)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=os.path.join(REPO, "..", "data", "fragility", "export"))
    ap.add_argument("--r-frag-dir", default=None, help="default: {data-dir}/frag_full")
    ap.add_argument("--labels", nargs="*", default=LABELS)
    ap.add_argument("--stage", choices=["rho", "frag"], default="rho",
                    help="rho: conditioning only, whole grid. frag: add perturbations")
    ap.add_argument("--configs", help="win:mode:l2 list for --stage frag, e.g. 0.5:joint:1e-4")
    ap.add_argument("--win", nargs="*", type=float, default=WIN_GRID)
    ap.add_argument("--l2", nargs="*", type=float, default=L2_GRID)
    ap.add_argument("--modes", nargs="*", default=MODES)
    ap.add_argument("--highpass", type=float, default=0.0,
                    help="4th-order Butterworth high-pass in Hz before fitting (Li et al. use 0.5)")
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--out", default=os.path.join(REPO, "verification_results",
                                                  "ltv_identifiability.csv"))
    args = ap.parse_args()

    data_dir = os.path.abspath(args.data_dir)
    frag_dir = args.r_frag_dir or os.path.join(data_dir, "frag_full")
    ref, r_lam = load_r_reference(frag_dir, args.labels)
    print(f"R reference: {len(ref)}/{len(args.labels)} seizures from {frag_dir}", flush=True)

    _, contacts0 = load_clip(data_dir, args.labels[0])
    sizes = Counter(get_shaft(c) for c in contacts0)

    want_frag = args.stage == "frag"
    if want_frag:
        if not args.configs:
            sys.exit("--stage frag needs --configs win:mode:l2[,...]")
        units = {}
        for w, m, lam in parse_configs(args.configs):
            units.setdefault((w, m), []).append(lam)
        jobs = [(lab, w, data_dir, ls, [m], True, args.highpass)
                for (w, m), ls in units.items() for lab in args.labels]
    else:
        jobs = [(lab, w, data_dir, args.l2, args.modes, False, args.highpass)
                for w in args.win for lab in args.labels]
    print(f"{len(jobs)} units, {args.jobs} workers, stage={args.stage}", flush=True)
    t0 = time.time()
    with Pool(args.jobs) as pool:
        rows = [r for batch in pool.imap_unordered(run_unit, jobs) for r in batch]
    print(f"swept in {time.time()-t0:.0f}s", flush=True)

    aggs = aggregate(rows, ref, r_lam, sizes)
    print("\n" + HDR, flush=True)
    for a in sorted(aggs, key=lambda a: (a["mode"], a["win_s"], a["l2_reg"])):
        print(fmt(a), flush=True)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    new = not os.path.exists(args.out)
    with open(args.out, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        for r in rows + aggs:
            w.writerow(r)
    print(f"\n-> {args.out}", flush=True)


if __name__ == "__main__":
    main()
