#!/usr/bin/env python3
"""Comprehensive Benchmark Runner for BrainQuake v2 on OpenNeuro ds004100.

Evaluates and compares:
1. Python PyFragility (ezfragility estimator) vs R EZFragility package
2. Python PyFragility (our extended estimator) vs ezfragility estimator
3. BrainQuake compute_ei (CAR and Bipolar) vs R EZEI package
4. Performance & execution time across all arms with speedup factors
5. Statistical significance tests and quantitative implementation grades.
"""

import os
# Force 1 thread per process for BLAS to maximize multiprocessing efficiency
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_v] = "1"

import argparse
import csv
import gc
import glob
import json
import subprocess
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import defaultdict

import mne
import numpy as np
import scipy.linalg
from scipy.stats import mannwhitneyu, spearmanr, wilcoxon

# Add server directory to sys.path
SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../server"))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from app.sigproc.ei import BARTOLOMEI_HIGH_BAND, BARTOLOMEI_LOW_BAND, compute_ei_pipeline
from app.sigproc.fragility import compute_fragility_pipeline
from app.sigproc.montage import project_pairs_to_contacts

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
R_EZFRAGILITY_RUNNER = os.path.join(TOOLS_DIR, "fragility", "run_ezfragility_batch.R")
R_EZEI_RUNNER = os.path.join(TOOLS_DIR, "run_ezei_batch.R")

DEFAULT_DATASET_DIR = "/media/data/eeg/ds004100"
RESULTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../verification_results"))
CACHE_DIR = os.path.join(RESULTS_DIR, "cache")
OUTPUT_CSV = os.path.join(RESULTS_DIR, "ds004100_comprehensive_benchmark.csv")
OUTPUT_HTML = os.path.join(RESULTS_DIR, "ds004100_comprehensive_report.html")
R_TIMEOUT_S = 300  # per-run wall clock allowed to each R reference implementation
OUTPUT_MD = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../docs/ds004100_comprehensive_benchmark_report.md"))


def read_tsv(filepath):
    """Read TSV file into a list of dict rows, stripping UTF-8 BOM if present."""
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter="\t")
        return list(reader)


def load_participants(dataset_dir):
    """Map participant_id -> (outcome, engel)."""
    p_path = os.path.join(dataset_dir, "participants.tsv")
    out = {}
    if os.path.exists(p_path):
        for row in read_tsv(p_path):
            pid = row.get("participant_id", "").strip()
            out[pid] = {
                "outcome": row.get("outcome", "").strip().upper(),
                "engel": row.get("engel", "").strip().upper(),
            }
    return out


def parse_acq(base_name):
    """Parse BIDS acq- entity ('seeg'/'ecog')."""
    for part in base_name.split("_"):
        if part.startswith("acq-"):
            return part[len("acq-"):]
    return "unknown"


def compute_ratio_90(frag_mat, soz_indices):
    """Li et al. interpretability ratio I = mean_t F_SOZ(90th) / mean_t F_REF(90th)."""
    n_ch = frag_mat.shape[0]
    ref_indices = np.setdiff1d(np.arange(n_ch), soz_indices)
    if len(soz_indices) == 0 or len(ref_indices) == 0:
        return float("nan")
    soz90 = np.mean(np.percentile(frag_mat[soz_indices], 90, axis=0))
    ref90 = np.mean(np.percentile(frag_mat[ref_indices], 90, axis=0))
    return float(soz90 / ref90) if ref90 > 1e-12 else float("nan")


def run_r_ezfragility(data, ch_names, fs, pre_s, score_s, base_name, cache_dir):
    """Run R EZFragility calcAdjFrag with disk caching."""
    os.makedirs(os.path.join(cache_dir, "ezfragility_r"), exist_ok=True)
    cache_file = os.path.join(cache_dir, "ezfragility_r", f"{base_name}.json")

    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as fh:
                cached = json.load(fh)
            return cached["scores"], cached["r2_median"], cached["elapsed_sec"]
        except Exception:
            pass

    pid = os.getpid()
    tid = time.time_ns()
    tmp_in = f"/tmp/ezfrag_in_{pid}_{tid}.csv"
    tmp_out = f"/tmp/ezfrag_out_{pid}_{tid}.csv"

    try:
        with open(tmp_in, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for i, ch in enumerate(ch_names):
                writer.writerow([ch] + list(data[i]))

        cmd = ["Rscript", R_EZFRAGILITY_RUNNER, tmp_in, str(fs), str(pre_s), str(score_s), tmp_out]
        t0 = time.perf_counter()
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=R_TIMEOUT_S)
        elapsed = time.perf_counter() - t0

        if p.returncode != 0 or not os.path.exists(tmp_out):
            return None, 0.0, elapsed

        scores = {}
        r2_med = 0.0
        with open(tmp_out, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                scores[row["channel"]] = float(row["score"])
                r2_med = float(row.get("r2_median", 0.0))

        # Cache result
        with open(cache_file, "w", encoding="utf-8") as fh:
            json.dump({"scores": scores, "r2_median": r2_med, "elapsed_sec": elapsed}, fh)

        return scores, r2_med, elapsed
    except subprocess.TimeoutExpired:
        return None, 0.0, float(R_TIMEOUT_S)   # a lower bound, not zero
    except Exception:
        return None, 0.0, 0.0
    finally:
        for p in (tmp_in, tmp_out):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


def run_r_ezei(data, ch_names, fs, base_name, cache_dir):
    """Run R EZEI computeEpileptogenicIndex with disk caching."""
    os.makedirs(os.path.join(cache_dir, "ezei_r"), exist_ok=True)
    cache_file = os.path.join(cache_dir, "ezei_r", f"{base_name}.json")

    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as fh:
                cached = json.load(fh)
            return cached["scores"], cached["elapsed_sec"]
        except Exception:
            pass

    # Downsample fs > 500 to 500 Hz for multitaper stability
    proc_data = data
    proc_fs = fs
    if fs > 500.0:
        target_fs = 500.0
        n_samples_orig = data.shape[1]
        n_samples_new = int(round(n_samples_orig * (target_fs / fs)))
        try:
            from scipy.signal import resample
            proc_data = resample(data, n_samples_new, axis=1)
            proc_fs = target_fs
        except Exception:
            pass

    pid = os.getpid()
    tid = time.time_ns()
    tmp_in = f"/tmp/ezei_in_{pid}_{tid}.csv"
    tmp_out = f"/tmp/ezei_out_{pid}_{tid}.csv"

    try:
        with open(tmp_in, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for i, ch in enumerate(ch_names):
                writer.writerow([ch] + list(proc_data[i]))

        cmd = ["Rscript", R_EZEI_RUNNER, tmp_in, str(proc_fs), tmp_out]
        t0 = time.perf_counter()
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=R_TIMEOUT_S)
        elapsed = time.perf_counter() - t0

        if p.returncode != 0 or not os.path.exists(tmp_out):
            return None, elapsed

        scores = {}
        with open(tmp_out, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                scores[row["channel"]] = float(row.get("score", 0.0))

        with open(cache_file, "w", encoding="utf-8") as fh:
            json.dump({"scores": scores, "elapsed_sec": elapsed}, fh)

        return scores, elapsed
    except subprocess.TimeoutExpired:
        return None, float(R_TIMEOUT_S)   # a lower bound, not zero
    except Exception:
        return None, 0.0
    finally:
        for p in (tmp_in, tmp_out):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


def evaluate_scoring(scores_dict, ch_names, soz_gt, resect_gt):
    """Evaluate channel scoring against Ground Truth SOZ and Resection."""
    ranked = sorted(
        ch_names,
        key=lambda c: scores_dict.get(c, -np.inf) if np.isfinite(scores_dict.get(c, np.nan)) else -np.inf,
        reverse=True,
    )
    k = len(soz_gt) if soz_gt else 5
    top_k = ranked[:k]
    top_4 = ranked[:4]

    soz_hits = [c for c in top_k if c in soz_gt]
    soz_recall = len(soz_hits) / len(soz_gt) if soz_gt else 0.0
    soz_hit_top4 = 1 if any(c in soz_gt for c in top_4) else 0

    resect_hits = [c for c in top_k if c in resect_gt]
    resect_conc = len(resect_hits) / k if k else 0.0
    resect_hit_top4 = 1 if any(c in resect_gt for c in top_4) else 0

    return {
        "ranked_channels": ranked,
        "top_soz": top_k,
        "soz_recall": round(soz_recall, 4),
        "soz_hit_top4": soz_hit_top4,
        "resect_conc": round(resect_conc, 4),
        "resect_hit_top4": resect_hit_top4,
    }


def compute_spearman_overlap(scores_a, scores_b, ch_names):
    """Compute Spearman correlation and Top-K overlaps between two scoring methods."""
    valid_ch = [c for c in ch_names if c in scores_a and c in scores_b and
                np.isfinite(scores_a[c]) and np.isfinite(scores_b[c])]
    if len(valid_ch) < 3:
        return float("nan"), 0.0, 0.0

    a_vals = [scores_a[c] for c in valid_ch]
    b_vals = [scores_b[c] for c in valid_ch]
    rho = float(spearmanr(a_vals, b_vals).statistic)

    top4_a = set(sorted(valid_ch, key=lambda c: scores_a[c], reverse=True)[:4])
    top4_b = set(sorted(valid_ch, key=lambda c: scores_b[c], reverse=True)[:4])
    ov4 = len(top4_a & top4_b) / 4.0

    top10_a = set(sorted(valid_ch, key=lambda c: scores_a[c], reverse=True)[:10])
    top10_b = set(sorted(valid_ch, key=lambda c: scores_b[c], reverse=True)[:10])
    ov10 = len(top10_a & top10_b) / min(10.0, len(valid_ch))

    return round(rho, 4), round(ov4, 4), round(ov10, 4)


def process_single_run_benchmark(job_args):
    """Worker to benchmark all 6 algorithm arms on a single run."""
    edf_path, ieeg_dir, base_name, sub_id, participant_info, cache_dir = job_args
    acq = parse_acq(base_name)
    run_label = base_name.split("_run-")[-1] if "_run-" in base_name else base_name

    events_tsv = os.path.join(ieeg_dir, f"{base_name}_events.tsv")
    channels_tsv = os.path.join(ieeg_dir, f"{base_name}_channels.tsv")

    if not os.path.exists(events_tsv) or not os.path.exists(channels_tsv):
        return {"status": "SKIPPED", "error": "Missing metadata TSVs", "run_id": base_name, "subject": sub_id}

    events = read_tsv(events_tsv)
    channels = read_tsv(channels_tsv)

    t_onset = None
    t_offset = None
    for row in events:
        tt = str(row.get("trial_type", "")).lower()
        if "onset" in tt and t_onset is None:
            try:
                t_onset = float(row["onset"])
            except Exception:
                pass
        if "offset" in tt and t_offset is None:
            try:
                t_offset = float(row["onset"])
            except Exception:
                pass

    if t_onset is None:
        return {"status": "SKIPPED", "error": "No seizure onset timestamp", "run_id": base_name, "subject": sub_id}

    sz_dur = (t_offset - t_onset) if (t_offset is not None and t_offset > t_onset) else 30.0

    usable_channels = set()
    bad_channels = set()
    soz_gt = set()
    resect_gt = set()

    for row in channels:
        name = row.get("name", "").strip()
        if not name:
            continue
        status = str(row.get("status", "good")).lower()
        desc = str(row.get("status_description", "")).lower()
        if status == "bad":
            bad_channels.add(name)
        else:
            usable_channels.add(name)
        if "soz" in desc:
            soz_gt.add(name)
        if "resect" in desc:
            resect_gt.add(name)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            raw = mne.io.read_raw_edf(edf_path, preload=False, verbose="error")
    except Exception as e:
        return {"status": "ERROR", "error": f"EDF load failed: {e}", "run_id": base_name, "subject": sub_id}

    fs = float(raw.info["sfreq"])
    raw_ch_names = list(raw.ch_names)
    duration = float(raw.times[-1])

    picks = [i for i, n in enumerate(raw_ch_names) if n in usable_channels]
    if not picks:
        picks = [i for i, n in enumerate(raw_ch_names) if n not in bad_channels]
    if not picks:
        raw.close()
        return {"status": "SKIPPED", "error": "No usable channels", "run_id": base_name, "subject": sub_id}

    ch_names = [raw_ch_names[i] for i in picks]
    n_ch = len(ch_names)

    # 1. Prepare Fragility Slice (first 5% of seizure)
    min_score_s = 2.0
    score_s = max(min_score_s, 0.05 * sz_dur)
    pre_s = 10.0
    span0 = max(0.0, t_onset - pre_s)
    span1 = min(duration, t_onset + min(score_s + 5.0, 90.0))

    try:
        frag_data = raw.get_data(picks=picks, start=int(span0 * fs), stop=int(span1 * fs)) * 1e6
        # CAR for Fragility
        frag_data = frag_data - frag_data.mean(axis=0, keepdims=True)
    except Exception as e:
        raw.close()
        return {"status": "ERROR", "error": f"Fragility slice error: {e}", "run_id": base_name, "subject": sub_id}

    # 2. Prepare EI Slice (-60s to -10s baseline, 0 to +15s target)
    b_start = max(0.0, t_onset - 60.0)
    b_end = max(0.5, t_onset - 10.0)
    t_start = t_onset
    t_end = min(duration, t_onset + 15.0)
    pad = 10.0
    ei_span0 = max(0.0, min(b_start, t_start) - pad)
    ei_span1 = min(duration, max(b_end, t_end) + pad)

    try:
        ei_data = raw.get_data(picks=picks, start=int(ei_span0 * fs), stop=int(ei_span1 * fs))
    except Exception as e:
        raw.close()
        return {"status": "ERROR", "error": f"EI slice error: {e}", "run_id": base_name, "subject": sub_id}
    finally:
        raw.close()
        del raw
        gc.collect()

    soz_idx = np.array([i for i, n in enumerate(ch_names) if n in soz_gt])

    # === BENCHMARK ARM 1: Python Fragility (ezfragility port) ===
    t0 = time.perf_counter()
    r_frag_py_ez = compute_fragility_pipeline(
        data=frag_data, fs=fs, ch_names=ch_names, win_s=0.25, step_s=0.125,
        onset_s=t_onset - span0, eval_window_s=(0.0, score_s),
        method="ezfragility", device="cpu",
    )
    time_frag_py_ez = time.perf_counter() - t0
    scores_frag_py_ez = r_frag_py_ez["channel_scores"]
    r2_frag_py_ez = r_frag_py_ez["median_r2"]
    i_frag_py_ez = compute_ratio_90(r_frag_py_ez["fragility_matrix"], soz_idx)
    eval_frag_py_ez = evaluate_scoring(scores_frag_py_ez, ch_names, soz_gt, resect_gt)

    # === BENCHMARK ARM 2: Python Fragility (extended - BrainQuake) ===
    t0 = time.perf_counter()
    r_frag_py_ext = compute_fragility_pipeline(
        data=frag_data, fs=fs, ch_names=ch_names, win_s=0.25, step_s=0.125,
        onset_s=t_onset - span0, eval_window_s=(0.0, score_s),
        method="extended", device="cpu",
    )
    time_frag_py_ext = time.perf_counter() - t0
    scores_frag_py_ext = r_frag_py_ext["channel_scores"]
    r2_frag_py_ext = r_frag_py_ext["median_r2"]
    i_frag_py_ext = compute_ratio_90(r_frag_py_ext["fragility_matrix"], soz_idx)
    eval_frag_py_ext = evaluate_scoring(scores_frag_py_ext, ch_names, soz_gt, resect_gt)

    # === BENCHMARK ARM 3: R EZFragility Package ===
    scores_frag_r, r2_frag_r, time_frag_r = run_r_ezfragility(
        frag_data, ch_names, fs, t_onset - span0, score_s, base_name, cache_dir
    )
    eval_frag_r = evaluate_scoring(scores_frag_r, ch_names, soz_gt, resect_gt) if scores_frag_r else None

    # === BENCHMARK ARM 4: Python ComputeEI (CAR) ===
    t0 = time.perf_counter()
    r_ei_car = compute_ei_pipeline(
        raw_data=ei_data, fs=fs, chn_names=ch_names,
        baseline_start=b_start, baseline_end=b_end,
        target_start=t_start, target_end=t_end,
        ei_method="band_ratio", reference="car", span_start=ei_span0,
    )
    time_ei_car = time.perf_counter() - t0
    scores_ei_car = r_ei_car["ei_scores"]
    eval_ei_car = evaluate_scoring(scores_ei_car, ch_names, soz_gt, resect_gt)

    # === BENCHMARK ARM 5: Python ComputeEI (Bipolar) ===
    t0 = time.perf_counter()
    r_ei_bip = compute_ei_pipeline(
        raw_data=ei_data, fs=fs, chn_names=ch_names,
        baseline_start=b_start, baseline_end=b_end,
        target_start=t_start, target_end=t_end,
        ei_method="band_ratio", reference="bipolar", span_start=ei_span0,
    )
    time_ei_bip = time.perf_counter() - t0
    scores_ei_bip = (
        project_pairs_to_contacts(r_ei_bip["ei_scores"], r_ei_bip["pairs"])
        if r_ei_bip.get("pairs") else r_ei_bip["ei_scores"]
    )
    eval_ei_bip = evaluate_scoring(scores_ei_bip, ch_names, soz_gt, resect_gt)

    # === BENCHMARK ARM 6: R EZEI Package ===
    # Slices around seizure onset (-10s baseline, +20s target) for EZEI
    ezei_start = max(0.0, t_onset - 10.0)
    ezei_end = min(duration, t_onset + 20.0)
    idx0 = int(round((ezei_start - ei_span0) * fs))
    idx1 = int(round((ezei_end - ei_span0) * fs))
    ezei_data = ei_data[:, idx0:idx1]

    scores_ei_r, time_ei_r = run_r_ezei(ezei_data, ch_names, fs, base_name, cache_dir)
    eval_ei_r = evaluate_scoring(scores_ei_r, ch_names, soz_gt, resect_gt) if scores_ei_r else None

    # Parity comparisons
    rho_frag_ez_r, ov4_frag_ez_r, ov10_frag_ez_r = (
        compute_spearman_overlap(scores_frag_py_ez, scores_frag_r, ch_names)
        if scores_frag_r else (np.nan, np.nan, np.nan)
    )
    rho_frag_ext_ez, ov4_frag_ext_ez, ov10_frag_ext_ez = compute_spearman_overlap(
        scores_frag_py_ext, scores_frag_py_ez, ch_names
    )
    rho_frag_ext_r, ov4_frag_ext_r, _ = (
        compute_spearman_overlap(scores_frag_py_ext, scores_frag_r, ch_names)
        if scores_frag_r else (np.nan, np.nan, np.nan)
    )
    rho_ei_car_r, ov4_ei_car_r, ov10_ei_car_r = (
        compute_spearman_overlap(scores_ei_car, scores_ei_r, ch_names)
        if scores_ei_r else (np.nan, np.nan, np.nan)
    )
    rho_ei_bip_car, _, _ = compute_spearman_overlap(scores_ei_bip, scores_ei_car, ch_names)

    outcome = participant_info.get("outcome", "")
    engel = participant_info.get("engel", "")

    return {
        "status": "SUCCESS",
        "subject": sub_id,
        "run_id": base_name,
        "run_label": run_label,
        "acq": acq,
        "outcome": outcome,
        "engel": engel,
        "n_channels": n_ch,
        "n_soz_gt": len(soz_gt),
        "n_resect_gt": len(resect_gt),
        "gt_soz_channels": ", ".join(sorted(soz_gt)),
        "gt_resect_channels": ", ".join(sorted(resect_gt)),
        "fs": fs,
        "sz_dur_s": round(sz_dur, 2),
        "score_s": round(score_s, 2),
        # Timing & Speedups
        "time_frag_py_ez": round(time_frag_py_ez, 3),
        "time_frag_py_ext": round(time_frag_py_ext, 3),
        "time_frag_r": round(time_frag_r, 3),
        "time_ei_car": round(time_ei_car, 3),
        "time_ei_bip": round(time_ei_bip, 3),
        "time_ei_r": round(time_ei_r, 3),
        # Parity
        "rho_frag_ez_vs_r": rho_frag_ez_r,
        "ov4_frag_ez_vs_r": ov4_frag_ez_r,
        "rho_frag_ext_vs_ez": rho_frag_ext_ez,
        "rho_frag_ext_vs_r": rho_frag_ext_r,
        "ov4_frag_ext_vs_r": ov4_frag_ext_r,
        "rho_ei_car_vs_r": rho_ei_car_r,
        "rho_ei_bip_vs_car": rho_ei_bip_car,
        # Fragility Metrics: SOZ Recall @ K
        "soz_recall_frag_py_ez": eval_frag_py_ez["soz_recall"],
        "soz_recall_frag_py_ext": eval_frag_py_ext["soz_recall"],
        "soz_recall_frag_r": eval_frag_r["soz_recall"] if eval_frag_r else np.nan,
        # Fragility Metrics: SOZ Top-4 Hit Rate
        "soz_hit_frag_py_ez": eval_frag_py_ez["soz_hit_top4"],
        "soz_hit_frag_py_ext": eval_frag_py_ext["soz_hit_top4"],
        "soz_hit_frag_r": eval_frag_r["soz_hit_top4"] if eval_frag_r else np.nan,
        # Fragility Metrics: Resection Concordance
        "resect_conc_frag_py_ez": eval_frag_py_ez["resect_conc"],
        "resect_conc_frag_py_ext": eval_frag_py_ext["resect_conc"],
        "resect_conc_frag_r": eval_frag_r["resect_conc"] if eval_frag_r else np.nan,
        # Fragility Interpretability Ratio I
        "i_ratio_frag_py_ez": round(i_frag_py_ez, 4),
        "i_ratio_frag_py_ext": round(i_frag_py_ext, 4),
        # EI Metrics: SOZ Recall @ K
        "soz_recall_ei_car": eval_ei_car["soz_recall"],
        "soz_recall_ei_bip": eval_ei_bip["soz_recall"],
        "soz_recall_ei_r": eval_ei_r["soz_recall"] if eval_ei_r else np.nan,
        # EI Metrics: SOZ Top-4 Hit Rate
        "soz_hit_ei_car": eval_ei_car["soz_hit_top4"],
        "soz_hit_ei_bip": eval_ei_bip["soz_hit_top4"],
        "soz_hit_ei_r": eval_ei_r["soz_hit_top4"] if eval_ei_r else np.nan,
        # EI Metrics: Resection Concordance
        "resect_conc_ei_car": eval_ei_car["resect_conc"],
        "resect_conc_ei_bip": eval_ei_bip["resect_conc"],
        "resect_conc_ei_r": eval_ei_r["resect_conc"] if eval_ei_r else np.nan,
        # Model fit quality
        "r2_frag_py_ez": round(r2_frag_py_ez, 4),
        "r2_frag_py_ext": round(r2_frag_py_ext, 4),
        "r2_frag_r": round(r2_frag_r, 4),
    }


def synthesize_and_report(results, out_md_path, out_html_path, out_csv_path, total_elapsed):
    """Synthesize complete benchmark results, statistical comparisons, and grades."""
    succ = [r for r in results if r["status"] == "SUCCESS"]
    with_gt_soz = [r for r in succ if float(r.get("n_soz_gt", 0)) > 0]
    with_gt_resect = [r for r in succ if float(r.get("n_resect_gt", 0)) > 0]

    n_runs = len(succ)
    n_subs = len(set(r["subject"] for r in succ))

    # Helper stats
    def calc_mean(rows, key):
        vals = [float(r[key]) for r in rows if r.get(key) is not None and np.isfinite(float(r[key]))]
        return float(np.mean(vals)) if vals else 0.0

    def calc_median(rows, key):
        vals = [float(r[key]) for r in rows if r.get(key) is not None and np.isfinite(float(r[key]))]
        return float(np.median(vals)) if vals else 0.0

    def calc_p95(rows, key):
        vals = [float(r[key]) for r in rows if r.get(key) is not None and np.isfinite(float(r[key]))]
        return float(np.percentile(vals, 95)) if vals else 0.0

    # 1. Performance & Execution Time Summary
    perf_summary = {
        "py_frag_ez": {
            "mean": calc_mean(succ, "time_frag_py_ez"),
            "median": calc_median(succ, "time_frag_py_ez"),
            "p95": calc_p95(succ, "time_frag_py_ez"),
        },
        "py_frag_ext": {
            "mean": calc_mean(succ, "time_frag_py_ext"),
            "median": calc_median(succ, "time_frag_py_ext"),
            "p95": calc_p95(succ, "time_frag_py_ext"),
        },
        "r_frag": {
            "mean": calc_mean(succ, "time_frag_r"),
            "median": calc_median(succ, "time_frag_r"),
            "p95": calc_p95(succ, "time_frag_r"),
        },
        "py_ei_car": {
            "mean": calc_mean(succ, "time_ei_car"),
            "median": calc_median(succ, "time_ei_car"),
            "p95": calc_p95(succ, "time_ei_car"),
        },
        "py_ei_bip": {
            "mean": calc_mean(succ, "time_ei_bip"),
            "median": calc_median(succ, "time_ei_bip"),
            "p95": calc_p95(succ, "time_ei_bip"),
        },
        "r_ei": {
            "mean": calc_mean(succ, "time_ei_r"),
            "median": calc_median(succ, "time_ei_r"),
            "p95": calc_p95(succ, "time_ei_r"),
        },
    }

    # Speedup factors
    speedup_frag_ez = (perf_summary["r_frag"]["mean"] / perf_summary["py_frag_ez"]["mean"]) if perf_summary["py_frag_ez"]["mean"] > 0 else 0.0
    speedup_frag_ext = (perf_summary["r_frag"]["mean"] / perf_summary["py_frag_ext"]["mean"]) if perf_summary["py_frag_ext"]["mean"] > 0 else 0.0
    speedup_ei_car = (perf_summary["r_ei"]["mean"] / perf_summary["py_ei_car"]["mean"]) if perf_summary["py_ei_car"]["mean"] > 0 else 0.0
    speedup_ei_bip = (perf_summary["r_ei"]["mean"] / perf_summary["py_ei_bip"]["mean"]) if perf_summary["py_ei_bip"]["mean"] > 0 else 0.0

    # 2. Clinical Metrics Table
    methods = [
        ("py_frag_ez", "PyFragility (ezfragility)", "time_frag_py_ez", "soz_recall_frag_py_ez", "soz_hit_frag_py_ez", "resect_conc_frag_py_ez"),
        ("py_frag_ext", "PyFragility (extended)", "time_frag_py_ext", "soz_recall_frag_py_ext", "soz_hit_frag_py_ext", "resect_conc_frag_py_ext"),
        ("r_frag", "R EZFragility", "time_frag_r", "soz_recall_frag_r", "soz_hit_frag_r", "resect_conc_frag_r"),
        ("py_ei_car", "BrainQuake EI (CAR)", "time_ei_car", "soz_recall_ei_car", "soz_hit_ei_car", "resect_conc_ei_car"),
        ("py_ei_bip", "BrainQuake EI (Bipolar)", "time_ei_bip", "soz_recall_ei_bip", "soz_hit_ei_bip", "resect_conc_ei_bip"),
        ("r_ei", "R EZEI Package", "time_ei_r", "soz_recall_ei_r", "soz_hit_ei_r", "resect_conc_ei_r"),
    ]

    metrics_table = {}
    for code, label, t_key, rec_key, hit_key, res_key in methods:
        metrics_table[code] = {
            "label": label,
            "mean_soz_recall": calc_mean(with_gt_soz, rec_key),
            "soz_hit_rate": calc_mean(with_gt_soz, hit_key),
            "mean_resect_conc": calc_mean(with_gt_resect, res_key),
            "mean_latency": calc_mean(succ, t_key),
        }

    # 3. Sub-cohort breakdown: SEEG vs ECoG
    seeg_runs = [r for r in with_gt_soz if r["acq"] == "seeg"]
    ecog_runs = [r for r in with_gt_soz if r["acq"] == "ecog"]

    cohort_stats = {
        "seeg": {
            "n": len(seeg_runs),
            "py_ei_bip_recall": calc_mean(seeg_runs, "soz_recall_ei_bip"),
            "py_ei_car_recall": calc_mean(seeg_runs, "soz_recall_ei_car"),
            "r_ei_recall": calc_mean(seeg_runs, "soz_recall_ei_r"),
            "py_frag_ext_recall": calc_mean(seeg_runs, "soz_recall_frag_py_ext"),
            "py_frag_ez_recall": calc_mean(seeg_runs, "soz_recall_frag_py_ez"),
            "r_frag_recall": calc_mean(seeg_runs, "soz_recall_frag_r"),
        },
        "ecog": {
            "n": len(ecog_runs),
            "py_ei_bip_recall": calc_mean(ecog_runs, "soz_recall_ei_bip"),
            "py_ei_car_recall": calc_mean(ecog_runs, "soz_recall_ei_car"),
            "r_ei_recall": calc_mean(ecog_runs, "soz_recall_ei_r"),
            "py_frag_ext_recall": calc_mean(ecog_runs, "soz_recall_frag_py_ext"),
            "py_frag_ez_recall": calc_mean(ecog_runs, "soz_recall_frag_py_ez"),
            "r_frag_recall": calc_mean(ecog_runs, "soz_recall_frag_r"),
        },
    }

    # 4. Parity metrics
    mean_rho_frag_ez_r = calc_mean(succ, "rho_frag_ez_vs_r")
    mean_ov4_frag_ez_r = calc_mean(succ, "ov4_frag_ez_vs_r")
    mean_rho_frag_ext_ez = calc_mean(succ, "rho_frag_ext_vs_ez")
    mean_rho_ei_car_r = calc_mean(succ, "rho_ei_car_vs_r")

    # 5. Outcome separation (Li et al. interpretability ratio I on Success vs Failure)
    succ_outcomes = [r for r in succ if r["outcome"] == "S"]
    fail_outcomes = [r for r in succ if r["outcome"] == "F"]

    def outcome_separation(key):
        s_vals = [float(r[key]) for r in succ_outcomes if np.isfinite(float(r.get(key, np.nan)))]
        f_vals = [float(r[key]) for r in fail_outcomes if np.isfinite(float(r.get(key, np.nan)))]
        if len(s_vals) > 2 and len(f_vals) > 2:
            p = mannwhitneyu(s_vals, f_vals, alternative="greater").pvalue
            pooled_sd = np.sqrt((np.var(s_vals, ddof=1) + np.var(f_vals, ddof=1)) / 2)
            d = (np.mean(s_vals) - np.mean(f_vals)) / pooled_sd if pooled_sd > 1e-6 else 0.0
            return np.mean(s_vals), np.mean(f_vals), d, p
        return 0.0, 0.0, 0.0, 1.0

    i_s_ext, i_f_ext, d_ext, p_ext = outcome_separation("i_ratio_frag_py_ext")
    i_s_ez, i_f_ez, d_ez, p_ez = outcome_separation("i_ratio_frag_py_ez")

    # 6. Statistical Significance (Wilcoxon paired tests)
    def calc_wilcoxon(rows, key_a, key_b):
        pairs = [(float(r[key_a]), float(r[key_b])) for r in rows if
                 r.get(key_a) is not None and r.get(key_b) is not None and
                 np.isfinite(float(r[key_a])) and np.isfinite(float(r[key_b]))]
        if len(pairs) < 5:
            return 1.0
        a = [p[0] for p in pairs]
        b = [p[1] for p in pairs]
        diffs = np.array(a) - np.array(b)
        if np.all(diffs == 0):
            return 1.0
        try:
            return wilcoxon(a, b, alternative="two-sided").pvalue
        except Exception:
            return 1.0

    p_ei_bip_vs_car = calc_wilcoxon(seeg_runs, "soz_recall_ei_bip", "soz_recall_ei_car")
    p_ei_bip_vs_r = calc_wilcoxon(seeg_runs, "soz_recall_ei_bip", "soz_recall_ei_r")
    p_frag_ext_vs_ez = calc_wilcoxon(with_gt_soz, "soz_recall_frag_py_ext", "soz_recall_frag_py_ez")

    # Compute paired subset stats (where both R Fragility and Python completed)
    paired_r_frag = [r for r in with_gt_soz if r.get("soz_recall_frag_r") != "" and r.get("soz_recall_frag_r") != "nan" and np.isfinite(float(r.get("soz_recall_frag_r", np.nan)))]
    n_paired = len(paired_r_frag)
    paired_py_ez_recall = calc_mean(paired_r_frag, "soz_recall_frag_py_ez")
    paired_r_frag_recall = calc_mean(paired_r_frag, "soz_recall_frag_r")
    paired_py_ext_recall = calc_mean(paired_r_frag, "soz_recall_frag_py_ext")
    paired_py_ez_hit = calc_mean(paired_r_frag, "soz_hit_frag_py_ez")
    paired_r_frag_hit = calc_mean(paired_r_frag, "soz_hit_frag_r")
    paired_py_ext_hit = calc_mean(paired_r_frag, "soz_hit_frag_py_ext")
    paired_py_ez_res = calc_mean(paired_r_frag, "resect_conc_frag_py_ez")
    paired_r_frag_res = calc_mean(paired_r_frag, "resect_conc_frag_r")
    paired_py_ext_res = calc_mean(paired_r_frag, "resect_conc_frag_py_ext")
    paired_mean_rho = calc_mean(paired_r_frag, "rho_frag_ez_vs_r")
    paired_mean_ov4 = calc_mean(paired_r_frag, "ov4_frag_ez_vs_r")
    paired_rho_ext_ez = calc_mean(paired_r_frag, "rho_frag_ext_vs_ez")
    paired_rho_ext_r = calc_mean(paired_r_frag, "rho_frag_ext_vs_r")
    paired_ov4_ext_r = calc_mean(paired_r_frag, "ov4_frag_ext_vs_r")
    # A rank correlation needs both sides' per-channel scores, so it can cover fewer runs
    # than R itself does -- e.g. when R is re-run alone and the Python arms are reused.
    # Reporting it under n_paired would claim a sample it does not have.
    n_parity = len([r for r in paired_r_frag
                    if r.get("rho_frag_ez_vs_r") not in (None, "")
                    and np.isfinite(float(r["rho_frag_ez_vs_r"]))])

    # Write Markdown Report
    frag_ez = metrics_table["py_frag_ez"]
    frag_ext = metrics_table["py_frag_ext"]
    ei_bip, ei_car, ei_r = metrics_table["py_ei_bip"], metrics_table["py_ei_car"], metrics_table["r_ei"]
    n_soz = len(with_gt_soz)
    n_timeout = n_soz - n_paired
    seeg, ecog = cohort_stats["seeg"], cohort_stats["ecog"]

    perf_summary["r_frag"] = {
        "mean": calc_mean(paired_r_frag, "time_frag_r"),
        "median": calc_median(paired_r_frag, "time_frag_r"),
        "p95": calc_p95(paired_r_frag, "time_frag_r"),
    }
    r_frag = metrics_table["r_frag"]
    r_complete = n_timeout == 0
    if r_complete:
        r_coverage_md = (f"R `EZFragility` also completed all {n_soz} runs "
                         f"(median {perf_summary['r_frag']['median']:.0f}s per run).")
        r_caveat_md = ""
        r_subset_md = "R completed every run, so this is the full cohort, not a subset."
        r_footnote_md = ""
        r_row_md = (f"| **R EZFragility Package** | {n_soz} | {perf_summary['r_frag']['mean']:.2f}s "
                    f"| {r_frag['mean_soz_recall']*100:.2f}% | {r_frag['soz_hit_rate']*100:.2f}% "
                    f"| {r_frag['mean_resect_conc']*100:.2f}% | \u2014 |")
        r_coverage_html = f"All implementations completed all {n_soz} runs."
        r_speed_html = ""
    else:
        r_coverage_md = (f"R `EZFragility` timed out (>{R_TIMEOUT_S} s) on {n_timeout} of "
                         f"{n_soz} runs.")
        r_caveat_md = (
            f"\n> **Caveat on every R comparison in this report.** The {n_paired} runs R "
            f"completed are the low-channel subset. Python scores "
            f"{paired_py_ez_recall*100:.2f}% recall on them versus "
            f"{frag_ez['mean_soz_recall']*100:.2f}% across the full cohort, so R's "
            f"paired-subset figures measure an easier problem and are **not** comparable "
            f"to any full-cohort number.\n")
        r_subset_md = "This subset is the low-channel end of the cohort \u2014 see the caveat under Q1."
        r_footnote_md = (f"\n*\\*R EZFragility timed out (>{R_TIMEOUT_S} s) on {n_timeout} of the "
                         f"{n_soz} runs, so it has no full-cohort statistics. Its paired-subset "
                         f"figures are in section 2 and are not comparable to this table.*\n")
        r_row_md = (f"| **R EZFragility Package** | {n_paired}* | "
                    f"{perf_summary['r_frag']['mean']:.2f}s | n/a* | n/a* | n/a* | \u2014 |")
        r_coverage_html = (f"Python implementations completed all {n_soz} runs without timeouts. "
                           f"R EZFragility timed out (&gt;{R_TIMEOUT_S}s) on {n_timeout} runs.")
        r_speed_html = (f'<div class="note"><strong>*R speedups are against the {n_paired} runs R '
                        f'completed</strong> \u2014 the low-channel subset. R abandoned {n_timeout} runs '
                        f'at the {R_TIMEOUT_S} s cap, so its true mean is higher.</div>')

    i_mean_ez = calc_mean(with_gt_soz, "i_ratio_frag_py_ez")
    i_mean_ext = calc_mean(with_gt_soz, "i_ratio_frag_py_ext")

    def per_min(v):
        return 60.0 / v if v > 0 else 0.0

    # Emphasis is derived, never hand-placed: the best cell in each metric column wins,
    # ties included. Marking a row by hand goes stale the moment the numbers move.
    def html_rows(header_cells, rows, metric_cols):
        best = {}
        for c in metric_cols:
            vals = [r["cells"][c][0] for r in rows if r["cells"][c][0] is not None]
            best[c] = max(vals) if vals else None
        out = ["            <tr>\n                " +
               "".join(f"<th>{h}</th>" for h in header_cells) + "\n            </tr>"]
        for r in rows:
            tds = []
            for i, (val, text) in enumerate(r["cells"]):
                win = (i in metric_cols and val is not None and best[i] is not None
                       and abs(val - best[i]) < 1e-9)
                tds.append(f'<td class="win">{text}</td>' if win else f"<td>{text}</td>")
            out.append("            <tr>\n                " + "".join(tds) + "\n            </tr>")
        return "\n".join(out)

    def pct(v):
        return (v, f"{v*100:.2f}%") if v is not None else (None, "&mdash;")

    def txt(s):
        return (None, s)

    frag_paired_table = html_rows(
        ["Method / Package", "Evaluated Runs", "Spearman vs R (&rho;)", "Top-4 Overlap vs R",
         "SOZ Recall @ K", "Top-4 Hit Rate", "Resection Concordance"],
        [
            {"cells": [txt("<strong>PyFragility (ezfragility)</strong> <em>(Python Port)</em>"),
                       txt(str(n_paired)), txt(f"{paired_mean_rho:.4f} (n={n_parity})"),
                       txt(f"{paired_mean_ov4*100:.1f}%"),
                       pct(paired_py_ez_recall), pct(paired_py_ez_hit), pct(paired_py_ez_res)]},
            {"cells": [txt("<strong>R EZFragility Package</strong> <em>(Reference R)</em>"),
                       txt(str(n_paired)), txt("&mdash;"), txt("&mdash;"),
                       pct(paired_r_frag_recall), pct(paired_r_frag_hit), pct(paired_r_frag_res)]},
            {"cells": [txt("<strong>PyFragility (extended)</strong> <em>(Ours)</em>"),
                       txt(str(n_paired)), txt(f"{paired_rho_ext_r:.4f} (n={n_parity})"),
                       txt(f"{paired_ov4_ext_r*100:.1f}%"),
                       pct(paired_py_ext_recall), pct(paired_py_ext_hit), pct(paired_py_ext_res)]},
        ],
        metric_cols={4, 5, 6},
    )

    def cohort_row(label, m, secs, speed):
        return {"cells": [txt(label), txt(str(n_soz)),
                          pct(m["mean_soz_recall"]), pct(m["soz_hit_rate"]),
                          pct(m["mean_resect_conc"]),
                          (-secs, f"{secs:.2f}s"), txt(speed)]}

    cohort_table = html_rows(
        ["Method", "Evaluated Runs", "SOZ Recall @ K", "Top-4 Hit Rate",
         "Resection Concordance", "Mean Latency", "Speedup vs R"],
        [
            cohort_row("<strong>BrainQuake EI (Bipolar)</strong> <em>(Ours)</em>", ei_bip,
                       perf_summary["py_ei_bip"]["mean"], f"{speedup_ei_bip:.1f}x vs R EZEI"),
            cohort_row("BrainQuake EI (CAR)", ei_car,
                       perf_summary["py_ei_car"]["mean"], f"{speedup_ei_car:.1f}x vs R EZEI"),
            cohort_row("R EZEI Package", ei_r, perf_summary["r_ei"]["mean"], "baseline"),
            cohort_row("<strong>PyFragility (ezfragility)</strong> <em>(Python Port)</em>", frag_ez,
                       perf_summary["py_frag_ez"]["mean"], f"{speedup_frag_ez:.1f}x vs R*"),
            cohort_row("<strong>PyFragility (extended)</strong> <em>(Ours)</em>", frag_ext,
                       perf_summary["py_frag_ext"]["mean"], f"{speedup_frag_ext:.1f}x vs R*"),
        ],
        metric_cols={2, 3, 4, 5},   # 5 is latency, stored negated so lowest wins
    )

    report_md = f"""# Comprehensive Benchmark Report: PyFragility & ComputeEI vs R Packages (OpenNeuro ds004100)

**Dataset:** OpenNeuro `ds004100` (Intracranial EEG from focal epilepsy patients)  
**Evaluated Cohort:** {n_runs} ictal seizure recordings across {n_subs} subjects ({n_soz} runs with ground truth SOZ)  
**Benchmarked Implementations:**
1. **PyFragility (ezfragility port):** Python reproduction of Li et al. 2021
2. **PyFragility (extended):** BrainQuake scale-invariant LTV estimator + 0.5 Hz high-pass
3. **R EZFragility Package:** Reference R implementation (`calcAdjFrag`)
4. **BrainQuake ComputeEI (CAR):** Epileptogenicity Index under Common Average Reference
5. **BrainQuake ComputeEI (Bipolar):** Epileptogenicity Index under adjacent shaft Bipolar derivation
6. **R EZEI Package:** Reference Bartolomei et al. Multitaper EI implementation

Every figure below is computed from `ds004100_comprehensive_benchmark.csv` at report time.

---

## 1. Executive Summary & Question Answers

### Q1: Our PyFragility (`ezfragility` estimator) accuracy compared to R EZFragility
- **Parity ($n = {n_parity}$ of the {n_paired} runs R completed -- the rest have R scores but no
  stored Python per-channel scores to correlate against):**
  - Mean Spearman rank correlation: $\\rho = \\mathbf{{{paired_mean_rho:.4f}}}$ across all contacts
  - Top-4 channel overlap: $\\mathbf{{{paired_mean_ov4*100:.1f}\\%}}$
  - SOZ recall @ K: {paired_py_ez_recall*100:.2f}% (Python) vs {paired_r_frag_recall*100:.2f}% (R)
  - SOZ Top-4 hit rate: {paired_py_ez_hit*100:.2f}% (Python) vs {paired_r_frag_hit*100:.2f}% (R)
- **Full dataset:** Python `ezfragility` completed all {n_soz} runs at {frag_ez['mean_soz_recall']*100:.2f}% mean SOZ
  recall and {frag_ez['soz_hit_rate']*100:.2f}% Top-4 hit rate. {r_coverage_md}

{r_caveat_md}
### Q2: How our own estimator (`extended`) compares with the `ezfragility` estimator
- **SOZ localization across all {n_soz} runs:** `extended` achieves
  **{frag_ext['mean_soz_recall']*100:.2f}%** SOZ recall and **{frag_ext['soz_hit_rate']*100:.2f}%**
  Top-4 hit rate, versus {frag_ez['mean_soz_recall']*100:.2f}% and {frag_ez['soz_hit_rate']*100:.2f}%
  for `ezfragility` (paired Wilcoxon on recall, $p = {p_frag_ext_vs_ez:.3g}$).
- **Resection overlap:** {frag_ext['mean_resect_conc']*100:.2f}% vs {frag_ez['mean_resect_conc']*100:.2f}%.
- **Rank agreement between the two estimators:** mean Spearman $\\rho = {mean_rho_frag_ext_ez:.4f}$.
- **Surgical outcome separation ($S$ vs $F$), Li et al. interpretability ratio $I$:**
  - `extended`: Cohen's $d = {d_ext:+.3f}$, Mann-Whitney $p = {p_ext:.4f}$
    ($I_{{Success}} = {i_s_ext:.3f}$ vs $I_{{Failure}} = {i_f_ext:.3f}$)
  - `ezfragility`: Cohen's $d = {d_ez:+.3f}$, Mann-Whitney $p = {p_ez:.4f}$
    ($I_{{Success}} = {i_s_ez:.3f}$ vs $I_{{Failure}} = {i_f_ez:.3f}$)
  - Neither estimator separates surgical successes from failures on this cohort. Localization
    accuracy and outcome prognosis are separate claims; only the former is supported here.
- **Speed:** {perf_summary['py_frag_ext']['mean']:.2f} s mean latency vs
  {perf_summary['py_frag_ez']['mean']:.2f} s for `ezfragility`.

### Q3: `compute_ei` vs R `EZEI`
- **SEEG sub-cohort ($n = {seeg['n']}$):** BrainQuake EI (Bipolar) {seeg['py_ei_bip_recall']*100:.2f}%
  SOZ recall vs {seeg['r_ei_recall']*100:.2f}% for R EZEI
  ({(seeg['py_ei_bip_recall']-seeg['r_ei_recall'])*100:+.2f} pp, paired Wilcoxon $p = {p_ei_bip_vs_r:.3g}$).
- **Overall ({n_soz} runs):** {ei_bip['mean_soz_recall']*100:.2f}% (Bipolar) and
  {ei_car['mean_soz_recall']*100:.2f}% (CAR) vs {ei_r['mean_soz_recall']*100:.2f}% for R EZEI.
- **Speed:** {perf_summary['py_ei_bip']['mean']:.2f} s per run vs {perf_summary['r_ei']['mean']:.2f} s
  for R EZEI ({speedup_ei_bip:.1f}$\\times$).

---

## 2. Head-to-Head Comparison on the Runs R Completed ($n = {n_paired}$)

| Method / Package | Evaluated Runs | Spearman vs R ($\\rho$) | Top-4 Overlap vs R | SOZ Recall @ K | Top-4 Hit Rate | Resection Concordance |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **PyFragility (ezfragility)** *(Python Port)* | {n_paired} | **{paired_mean_rho:.4f}** (n={n_parity}) | **{paired_mean_ov4*100:.1f}%** | {paired_py_ez_recall*100:.2f}% | {paired_py_ez_hit*100:.2f}% | {paired_py_ez_res*100:.2f}% |
| **R EZFragility Package** *(Reference R)* | {n_paired} | — | — | {paired_r_frag_recall*100:.2f}% | {paired_r_frag_hit*100:.2f}% | {paired_r_frag_res*100:.2f}% |
| **PyFragility (extended)** *(Ours)* | {n_paired} | {paired_rho_ext_r:.4f} (n={n_parity}) | {paired_ov4_ext_r*100:.1f}% | {paired_py_ext_recall*100:.2f}% | {paired_py_ext_hit*100:.2f}% | {paired_py_ext_res*100:.2f}% |

`extended` deliberately differs from Li et al., so its two parity columns measure divergence
from R, not a target it is failing to hit. Its mean rank correlation with the `ezfragility`
port on this subset is $\\rho = {paired_rho_ext_ez:.4f}$.
{r_subset_md}

---

## 3. Full Cohort Benchmark Table (All {n_soz} Seizure Runs)

| Method / Implementation | Evaluated Runs | Mean Latency | SOZ Recall @ K | Top-4 Hit Rate ($\\ge 1$ Hit) | Resection Concordance | Li et al. Ratio ($I$) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **BrainQuake EI (Bipolar)** *(Ours)* | {n_soz} | {perf_summary['py_ei_bip']['mean']:.2f}s | {ei_bip['mean_soz_recall']*100:.2f}% | {ei_bip['soz_hit_rate']*100:.2f}% | {ei_bip['mean_resect_conc']*100:.2f}% | — |
| **BrainQuake EI (CAR)** | {n_soz} | {perf_summary['py_ei_car']['mean']:.2f}s | {ei_car['mean_soz_recall']*100:.2f}% | {ei_car['soz_hit_rate']*100:.2f}% | {ei_car['mean_resect_conc']*100:.2f}% | — |
| **R EZEI Package** | {n_soz} | {perf_summary['r_ei']['mean']:.2f}s | {ei_r['mean_soz_recall']*100:.2f}% | {ei_r['soz_hit_rate']*100:.2f}% | {ei_r['mean_resect_conc']*100:.2f}% | — |
| **PyFragility (ezfragility)** *(Python)* | {n_soz} | {perf_summary['py_frag_ez']['mean']:.2f}s | {frag_ez['mean_soz_recall']*100:.2f}% | {frag_ez['soz_hit_rate']*100:.2f}% | {frag_ez['mean_resect_conc']*100:.2f}% | {i_mean_ez:.3f} |
| **PyFragility (extended)** *(Python)* | {n_soz} | {perf_summary['py_frag_ext']['mean']:.2f}s | {frag_ext['mean_soz_recall']*100:.2f}% | {frag_ext['soz_hit_rate']*100:.2f}% | {frag_ext['mean_resect_conc']*100:.2f}% | {i_mean_ext:.3f} |
{r_row_md}
{r_footnote_md}
---

## 4. Sub-Cohort Analysis (SEEG vs ECoG)

Mean SOZ recall @ K.

| Modality | Cohort Size | BrainQuake EI (Bipolar) | BrainQuake EI (CAR) | R EZEI | PyFragility (extended) | PyFragility (ezfragility) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **SEEG (Depths)** | {seeg['n']} runs | {seeg['py_ei_bip_recall']*100:.2f}% | {seeg['py_ei_car_recall']*100:.2f}% | {seeg['r_ei_recall']*100:.2f}% | {seeg['py_frag_ext_recall']*100:.2f}% | {seeg['py_frag_ez_recall']*100:.2f}% |
| **ECoG (Grids)** | {ecog['n']} runs | {ecog['py_ei_bip_recall']*100:.2f}% | {ecog['py_ei_car_recall']*100:.2f}% | {ecog['r_ei_recall']*100:.2f}% | {ecog['py_frag_ext_recall']*100:.2f}% | {ecog['py_frag_ez_recall']*100:.2f}% |

---

## 5. Execution Time & Performance Benchmark

| Pipeline | Mean Latency | Median Latency | 95th Percentile | Throughput |
| :--- | :---: | :---: | :---: | :---: |
| **BrainQuake EI (Bipolar)** | {perf_summary['py_ei_bip']['mean']:.3f}s | {perf_summary['py_ei_bip']['median']:.3f}s | {perf_summary['py_ei_bip']['p95']:.3f}s | {per_min(perf_summary['py_ei_bip']['mean']):.1f} runs/min |
| **BrainQuake EI (CAR)** | {perf_summary['py_ei_car']['mean']:.3f}s | {perf_summary['py_ei_car']['median']:.3f}s | {perf_summary['py_ei_car']['p95']:.3f}s | {per_min(perf_summary['py_ei_car']['mean']):.1f} runs/min |
| **R EZEI** | {perf_summary['r_ei']['mean']:.3f}s | {perf_summary['r_ei']['median']:.3f}s | {perf_summary['r_ei']['p95']:.3f}s | {per_min(perf_summary['r_ei']['mean']):.1f} runs/min |
| **PyFragility (extended)** | {perf_summary['py_frag_ext']['mean']:.3f}s | {perf_summary['py_frag_ext']['median']:.3f}s | {perf_summary['py_frag_ext']['p95']:.3f}s | {per_min(perf_summary['py_frag_ext']['mean']):.1f} runs/min |
| **PyFragility (ezfragility)** | {perf_summary['py_frag_ez']['mean']:.3f}s | {perf_summary['py_frag_ez']['median']:.3f}s | {perf_summary['py_frag_ez']['p95']:.3f}s | {per_min(perf_summary['py_frag_ez']['mean']):.1f} runs/min |
| **R EZFragility** *(completed runs only)* | {perf_summary['r_frag']['mean']:.3f}s | {perf_summary['r_frag']['median']:.3f}s | {perf_summary['r_frag']['p95']:.3f}s | {per_min(perf_summary['r_frag']['mean']):.1f} runs/min |

R EZFragility's timings cover only the {n_paired} runs it finished; the {n_timeout} it abandoned
at the {R_TIMEOUT_S} s cap are excluded, so its true mean is higher than shown.
"""

    with open(out_md_path, "w", encoding="utf-8") as fh:
        fh.write(report_md)
    print(f"\nSaved Markdown Report to: {out_md_path}", flush=True)

    # Write HTML Report
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>BrainQuake ds004100 Benchmark Dashboard</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 40px; background: #f8fafc; color: #1e293b; line-height: 1.5; }}
        .container {{ max-width: 1200px; margin: auto; background: white; padding: 35px; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }}
        h1, h2, h3 {{ color: #0f172a; margin-top: 25px; }}
        table {{ width: 100%; border-collapse: collapse; margin: 15px 0 25px 0; }}
        th, td {{ padding: 12px 14px; text-align: left; border-bottom: 1px solid #e2e8f0; }}
        th {{ background: #f1f5f9; font-weight: 600; color: #334155; }}
        .note {{ background: #eff6ff; border-left: 4px solid #3b82f6; padding: 12px 16px; margin: 15px 0; border-radius: 0 6px 6px 0; font-size: 0.95em; }}
        .win {{ background: #dcfce7; color: #166534; font-weight: 600; }}
        .legend {{ font-size: 0.85em; color: #64748b; margin: -15px 0 25px 0; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>⚡ BrainQuake Comprehensive Benchmark Dashboard (OpenNeuro ds004100)</h1>
        <p>Retrospective benchmark across <strong>{n_runs} seizure runs</strong> ({n_subs} subjects) evaluating PyFragility and ComputeEI vs R packages.</p>
        
        <h2>1. Head-to-Head Parity on Paired Completed R Runs (n = {n_paired})</h2>
        <div class="note">
            On the {n_paired} runs where R <code>calcAdjFrag</code> completed within {R_TIMEOUT_S}s, the Python
            <code>ezfragility</code> port reaches <strong>{paired_mean_rho:.4f}</strong> mean Spearman rank correlation (over the {n_parity} of them with stored Python per-channel scores)
            and <strong>{paired_mean_ov4*100:.1f}%</strong> Top-4 overlap against it. These runs are the low-channel
            subset ({paired_py_ez_recall*100:.2f}% recall here vs {metrics_table['py_frag_ez']['mean_soz_recall']*100:.2f}%
            over the full cohort), so they are not comparable to any full-cohort figure.
        </div>
        <table>
{frag_paired_table}
        </table>
        <p class="legend">Green marks the best value in each metric column; ties are all marked.</p>

        <h2>2. Full Cohort Benchmark Metrics (All {len(with_gt_soz)} Seizure Runs)</h2>
        <div class="note">
            <strong>Full Cohort Coverage:</strong> {r_coverage_html}
        </div>
        <table>
{cohort_table}
        </table>
        <p class="legend">Green marks the best value in each metric column; ties are all marked.</p>

        <h2>3. SEEG Depths vs ECoG Grids Sub-Cohort Analysis</h2>
        <table>
            <tr>
                <th>Modality</th><th>Cohort Size</th><th>BrainQuake EI (Bipolar)</th><th>BrainQuake EI (CAR)</th><th>R EZEI</th><th>PyFragility (extended)</th><th>PyFragility (ezfragility)</th>
            </tr>
            <tr>
                <td><strong>SEEG (Depth Shafts)</strong></td><td>{cohort_stats['seeg']['n']} runs</td><td><strong>{cohort_stats['seeg']['py_ei_bip_recall']*100:.2f}%</strong></td><td>{cohort_stats['seeg']['py_ei_car_recall']*100:.2f}%</td><td>{cohort_stats['seeg']['r_ei_recall']*100:.2f}%</td><td>{cohort_stats['seeg']['py_frag_ext_recall']*100:.2f}%</td><td><strong>{cohort_stats['seeg']['py_frag_ez_recall']*100:.2f}%</strong></td>
            </tr>
            <tr>
                <td><strong>ECoG (2D Surface Grids)</strong></td><td>{cohort_stats['ecog']['n']} runs</td><td>{cohort_stats['ecog']['py_ei_bip_recall']*100:.2f}%</td><td>{cohort_stats['ecog']['py_ei_car_recall']*100:.2f}%</td><td><strong>{cohort_stats['ecog']['r_ei_recall']*100:.2f}%</strong></td><td>{cohort_stats['ecog']['py_frag_ext_recall']*100:.2f}%</td><td><strong>{cohort_stats['ecog']['py_frag_ez_recall']*100:.2f}%</strong></td>
            </tr>
        </table>
        
{r_speed_html}
    </div>
</body>
</html>
"""
    with open(out_html_path, "w", encoding="utf-8") as fh:
        fh.write(html_content)
    print(f"Saved HTML Report to: {out_html_path}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Comprehensive ds004100 Benchmark")
    parser.add_argument("--dataset", default=DEFAULT_DATASET_DIR)
    parser.add_argument("--jobs", type=int, default=12)
    parser.add_argument("--limit", type=int, help="Limit number of runs to process")
    parser.add_argument("--out-csv", default=OUTPUT_CSV)
    parser.add_argument("--out-html", default=OUTPUT_HTML)
    parser.add_argument("--out-md", default=OUTPUT_MD)
    parser.add_argument("--from-csv", action="store_true",
                        help="regenerate the reports from an existing --out-csv, without re-running")
    args = parser.parse_args()

    if args.from_csv:
        with open(args.out_csv, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        synthesize_and_report(rows, args.out_md, args.out_html, args.out_csv, 0.0)
        return

    os.makedirs(RESULTS_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)

    participants = load_participants(args.dataset)

    all_edfs = sorted(glob.glob(os.path.join(args.dataset, "sub-*", "ses-*", "ieeg", "*task-ictal*_ieeg.edf")))
    if args.limit:
        all_edfs = all_edfs[:args.limit]

    print("=" * 90, flush=True)
    print("      BRAINQUAKE v2 — COMPREHENSIVE BENCHMARK ON OPENNEURO ds004100", flush=True)
    print("=" * 90, flush=True)
    print(f"Dataset Path      : {args.dataset}", flush=True)
    print(f"Total Ictal Runs  : {len(all_edfs)}", flush=True)
    print(f"Parallel Workers  : {args.jobs}", flush=True)
    print(f"Results CSV       : {args.out_csv}", flush=True)
    print(f"Report Markdown   : {args.out_md}", flush=True)
    print(f"Report HTML       : {args.out_html}", flush=True)
    print("=" * 90, flush=True)

    job_args = []
    for edf_path in all_edfs:
        ieeg_dir = os.path.dirname(edf_path)
        base_name = os.path.basename(edf_path).replace("_ieeg.edf", "")
        sub_id = os.path.basename(os.path.dirname(os.path.dirname(ieeg_dir)))
        part_info = participants.get(sub_id, {})
        job_args.append((edf_path, ieeg_dir, base_name, sub_id, part_info, CACHE_DIR))

    results = []
    t_start = time.time()

    with ProcessPoolExecutor(max_workers=args.jobs) as executor:
        futures = {executor.submit(process_single_run_benchmark, arg): arg[2] for arg in job_args}
        for idx, future in enumerate(as_completed(futures), 1):
            base_name = futures[future]
            try:
                res = future.result()
                results.append(res)
                if res.get("status") == "SUCCESS":
                    rec_ext = f"{res['soz_recall_frag_py_ext']:.0%}"
                    rec_bip = f"{res['soz_recall_ei_bip']:.0%}"
                    print(f"[{idx:03d}/{len(job_args):03d}] {res['subject']:<10} | Run: {res['run_label']:<10} | "
                          f"Frag Ext: {rec_ext:<5} | EI Bip: {rec_bip:<5} | Status: SUCCESS", flush=True)
                else:
                    print(f"[{idx:03d}/{len(job_args):03d}] {base_name:<25} | Status: {res.get('status')} ({res.get('error')})", flush=True)
            except Exception as exc:
                print(f"[{idx:03d}/{len(job_args):03d}] {base_name:<25} | Exception: {exc}", flush=True)

    total_time = time.time() - t_start

    # Save CSV
    if results:
        fieldnames = list(results[0].keys())
        with open(args.out_csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        print(f"\nSaved raw benchmark CSV to: {args.out_csv}", flush=True)

    # Synthesize results & reports
    synthesize_and_report(results, args.out_md, args.out_html, args.out_csv, total_time)
    print("\nBenchmark completed successfully in {:.1f}s!\n".format(total_time), flush=True)


if __name__ == "__main__":
    main()
