#!/usr/bin/env python3
"""Neural fragility on OpenNeuro ds004100 (213 ictal runs), against Li et al. 2021.

Reproduces the paper's interpretability contrast: in successful resections the SOZ's
fragility sits far above the rest of the implant; in failures the two are
indistinguishable. The statistic is EZFragility's `fragStat` ratio,
I = mean_t F_SOZ(90th) / mean_t F_REF(90th), scored over the first 5% of each seizure.

Runs both estimators so they can be compared on the paper's own dataset:
  --method extended      ours (default): full upper-half contour, global ridge, 0.5 Hz high-pass
  --method ezfragility   the verified port of EZFragility/Li et al.

    python verify_ds004100_fragility.py --method extended ezfragility --jobs 12
"""

import os
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")   # process-level parallelism beats threaded BLAS here

import argparse
import csv
import glob
import sys
import time
import warnings
from collections import defaultdict
from multiprocessing import Pool

import mne
import numpy as np
from scipy.stats import mannwhitneyu

SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../server"))
sys.path.insert(0, SERVER_DIR)
from app.sigproc.fragility import compute_fragility_pipeline  # noqa: E402

DEFAULT_DATASET_DIR = "/media/data/eeg/ds004100"
WIN_S, STEP_S = 0.25, 0.125       # Li et al.
PRE_S = 10.0                      # context before onset
MIN_SCORE_S = 2.0                 # floor for very short seizures
MAX_SPAN_S = 90.0                 # cap on how much signal is loaded per run
FIELDS = [
    "sub", "run", "acq", "outcome", "engel", "method", "n_ch", "n_soz", "fs",
    "sz_dur_s", "score_s", "n_windows", "median_r2", "ratio_soz90_ref90",
    "auc_soz", "soz_recall_at_k", "resect_auc", "secs", "error",
]


def read_tsv(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def parse_entity(base, key):
    for part in os.path.basename(base).split("_"):
        if part.startswith(key + "-"):
            return part[len(key) + 1:]
    return "unknown"


def load_participants(root):
    out = {}
    for row in read_tsv(os.path.join(root, "participants.tsv")):
        out[row["participant_id"]] = (row.get("outcome", ""), row.get("engel", ""))
    return out


def ratio_90(frag, soz_idx):
    """Li et al.'s interpretability ratio, via EZFragility's fragStat definition:
    the 90th percentile of each set per window, averaged over windows."""
    ref_idx = np.setdiff1d(np.arange(frag.shape[0]), soz_idx)
    if not len(soz_idx) or not len(ref_idx):
        return float("nan")
    soz90 = np.mean(np.percentile(frag[soz_idx], 90, axis=0))
    ref90 = np.mean(np.percentile(frag[ref_idx], 90, axis=0))
    return float(soz90 / ref90) if ref90 > 1e-12 else float("nan")


def auc(scores, positive_mask):
    """Threshold-free separation, as Mann-Whitney U / (n1*n2)."""
    pos, neg = scores[positive_mask], scores[~positive_mask]
    if not len(pos) or not len(neg):
        return float("nan")
    return float(mannwhitneyu(pos, neg, alternative="two-sided").statistic / (len(pos) * len(neg)))


def process(job):
    edf, methods, root = job
    base = edf[: -len("_ieeg.edf")]
    sub = parse_entity(base, "sub")
    rec = {"sub": sub, "run": parse_entity(base, "run"), "acq": parse_entity(base, "acq")}
    try:
        ev = read_tsv(base + "_events.tsv")
        onset = next(float(r["onset"]) for r in ev if "onset" in str(r.get("trial_type", "")).lower())
        offs = [float(r["onset"]) for r in ev if "offset" in str(r.get("trial_type", "")).lower()]
        sz_dur = (offs[0] - onset) if offs else 30.0

        rows = read_tsv(base + "_channels.tsv")
        usable = [r["name"] for r in rows if str(r.get("status", "good")).lower() != "bad"]
        soz = {r["name"] for r in rows if "soz" in str(r.get("status_description", "")).lower()}
        resect = {r["name"] for r in rows if "resect" in str(r.get("status_description", "")).lower()}
        if not soz:
            return {**rec, "error": "no SOZ annotation"}

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            raw = mne.io.read_raw_edf(edf, preload=False, verbose="error")
        fs = float(raw.info["sfreq"])
        names = list(raw.ch_names)
        picks = [i for i, n in enumerate(names) if n in usable]
        ch = [names[i] for i in picks]
        dur = float(raw.times[-1])

        score_s = max(MIN_SCORE_S, 0.05 * sz_dur)          # the paper's first 5%
        span0 = max(0.0, onset - PRE_S)
        span1 = min(dur, onset + min(score_s + 5.0, MAX_SPAN_S))
        if span1 - span0 < WIN_S * 4:
            return {**rec, "error": f"span too short ({span1 - span0:.1f}s)"}
        data = raw.get_data(picks=picks, start=int(span0 * fs), stop=int(span1 * fs))
        raw.close()
        data = data * 1e6
        data = data - data.mean(axis=0, keepdims=True)      # CAR, as Li et al.

        soz_idx = np.array([i for i, n in enumerate(ch) if n in soz])
        res_mask = np.array([n in resect for n in ch])
        out = []
        for m in methods:
            t0 = time.time()
            r = compute_fragility_pipeline(
                data=data, fs=fs, ch_names=ch, win_s=WIN_S, step_s=STEP_S,
                onset_s=onset - span0, eval_window_s=(0.0, score_s),
                method=m, device="cpu",
            )
            t = r["start_times"]
            sel = np.where((t >= 0.0) & (t <= score_s))[0]
            if not len(sel):
                out.append({**rec, "method": m, "error": "no ictal windows"})
                continue
            frag = r["fragility_matrix"][:, sel]
            mean_score = frag.mean(axis=1)
            pos = np.zeros(len(ch), dtype=bool)
            pos[soz_idx] = True
            k = len(soz_idx)
            top = set(np.argsort(-mean_score)[:k])
            out.append({
                **rec, "method": m, "n_ch": len(ch), "n_soz": k, "fs": fs,
                "sz_dur_s": round(sz_dur, 1), "score_s": round(score_s, 2),
                "n_windows": len(sel), "median_r2": round(r["median_r2"], 4),
                "ratio_soz90_ref90": round(ratio_90(frag, soz_idx), 4),
                "auc_soz": round(auc(mean_score, pos), 4),
                "soz_recall_at_k": round(len(top & set(soz_idx.tolist())) / k, 4),
                "resect_auc": round(auc(mean_score, res_mask), 4) if res_mask.any() else float("nan"),
                "secs": round(time.time() - t0, 1),
            })
        return out
    except Exception as exc:  # one bad run must not kill a 213-run sweep
        return {**rec, "error": f"{type(exc).__name__}: {exc}"}


def summarise(rows, parts):
    for r in rows:
        o, e = parts.get("sub-" + r["sub"], ("", ""))
        r["outcome"], r["engel"] = o, e

    good = [r for r in rows if not r.get("error") and np.isfinite(r.get("ratio_soz90_ref90", np.nan))]
    print(f"\n{len(good)} scored / {len(rows)} attempted", flush=True)
    if not good:
        return

    print("\n=== Li et al. interpretability ratio  I = F_SOZ(90th) / F_REF(90th) ===")
    print(f"{'method':<13} {'group':<10} {'n':>4} {'mean I':>8} {'median I':>9} {'I>1':>7} "
          f"{'AUC':>7} {'recall@K':>9}")
    for m in sorted({r["method"] for r in good}):
        sub = [r for r in good if r["method"] == m]
        for grp, sel in (("success", [r for r in sub if r["outcome"] == "S"]),
                         ("failure", [r for r in sub if r["outcome"] == "F"]),
                         ("all", sub)):
            if not sel:
                continue
            I = np.array([r["ratio_soz90_ref90"] for r in sel])
            a = np.array([r["auc_soz"] for r in sel], dtype=float)
            k = np.array([r["soz_recall_at_k"] for r in sel], dtype=float)
            print(f"{m:<13} {grp:<10} {len(sel):>4} {I.mean():>8.3f} {np.median(I):>9.3f} "
                  f"{100*np.mean(I > 1):>6.0f}% {np.nanmean(a):>7.3f} {np.nanmean(k):>9.3f}")

        s = np.array([r["ratio_soz90_ref90"] for r in sub if r["outcome"] == "S"])
        f = np.array([r["ratio_soz90_ref90"] for r in sub if r["outcome"] == "F"])
        if len(s) > 2 and len(f) > 2:
            p = mannwhitneyu(s, f, alternative="greater").pvalue
            d = (s.mean() - f.mean()) / np.sqrt((s.var(ddof=1) + f.var(ddof=1)) / 2)
            print(f"{m:<13} success vs failure: Cohen's d = {d:+.3f}, "
                  f"Mann-Whitney p = {p:.4f} (one-sided, S > F)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=DEFAULT_DATASET_DIR)
    ap.add_argument("--method", nargs="+", default=["extended", "ezfragility"])
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--limit", type=int, help="process only the first N runs (smoke test)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..",
                                                  "verification_results",
                                                  "ds004100_fragility.csv"))
    args = ap.parse_args()

    runs = sorted(glob.glob(os.path.join(args.dataset, "sub-*", "ses-*", "ieeg",
                                         "*task-ictal*_ieeg.edf")))
    if args.limit:
        runs = runs[: args.limit]
    print(f"{len(runs)} ictal runs, methods={args.method}, {args.jobs} workers", flush=True)

    jobs = [(p, args.method, args.dataset) for p in runs]
    rows = []
    t0 = time.time()
    with Pool(args.jobs) as pool:
        for i, res in enumerate(pool.imap_unordered(process, jobs), 1):
            batch = res if isinstance(res, list) else [res]
            rows.extend(batch)
            if i % 20 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)} runs, {time.time()-t0:.0f}s", flush=True)

    summarise(rows, load_participants(args.dataset))

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"\n-> {out}", flush=True)


if __name__ == "__main__":
    main()
