#!/usr/bin/env python3
"""Full Dataset Verification Script for BrainQuake v2 on OpenNeuro ds004100.

Evaluates Epileptogenicity Index (EI), High-Frequency Oscillation (HFO) events,
and multi-modal Percentile Fusion (EI+HFO) for SOZ detection across ds004100.
"""

import argparse
import csv
import gc
import glob
import os
import sys
import time
import numpy as np
import mne

# Ensure stdout flushes line by line so output logs are never lost on process interruption
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

# Add server directory to sys.path to import BrainQuake sigproc modules
SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../server"))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from app.sigproc.ei import BARTOLOMEI_HIGH_BAND, BARTOLOMEI_LOW_BAND, ONSET_THRESHOLD_K, compute_ei_pipeline
from app.sigproc.filters import DEFAULT_MAINS_FREQ
from app.sigproc.fusion import fuse_ei_hfo_scores
from app.sigproc.hfo import compute_hfo_pipeline
from app.sigproc.montage import project_pairs_to_contacts

# Import verification_report from the same tools directory
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)
from verification_report import generate_html_report

DEFAULT_DATASET_DIR = "/media/data/eeg/ds004100"
DEFAULT_OUTPUT_CSV = "/home/ferreus/dev/BrainQuake/verification_ds004100_full.csv"
DEFAULT_OUTPUT_HTML = "/home/ferreus/dev/BrainQuake/verification_ds004100_report.html"


def parse_acq(base_name):
    """BIDS acq- entity ('seeg'/'ecog'). Bipolar pairing by adjacent contact
    number is only physically right for depths, so results are split on this."""
    for part in base_name.split("_"):
        if part.startswith("acq-"):
            return part[len("acq-"):]
    return "unknown"


def read_tsv(filepath):
    """Read TSV file into a list of dict rows, stripping UTF-8 BOM if present."""
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter="\t")
        return list(reader)


def load_interictal_raw(ieeg_dir, usable_channels):
    """Attempt to load an interictal EDF recording for the subject, if available."""
    inter_edfs = sorted(glob.glob(os.path.join(ieeg_dir, "*task-interictal*.edf")))
    for path in inter_edfs:
        try:
            if os.path.exists(path) and os.path.getsize(path) > 100000:
                raw = mne.io.read_raw_edf(path, preload=False, verbose=False)
                fs = float(raw.info["sfreq"])
                all_ch = list(raw.ch_names)
                picks = [i for i, name in enumerate(all_ch) if name in usable_channels]
                if not picks:
                    raw.close()
                    continue
                # Load first 60 seconds (or available duration) for fast interictal HFO evaluation
                t_max = min(60.0, float(raw.times[-1]))
                data = raw.get_data(picks=picks, tmin=0.0, tmax=t_max)
                ch_names = [all_ch[i] for i in picks]
                raw.close()
                del raw
                return data, fs, ch_names
        except Exception:
            pass
    return None, None, None


def process_run(edf_path, ieeg_dir, base_name, sub_id, opts):
    """Process a single seizure run EDF file and evaluate prediction vs Ground Truth."""
    events_tsv = os.path.join(ieeg_dir, f"{base_name}_events.tsv")
    channels_tsv = os.path.join(ieeg_dir, f"{base_name}_channels.tsv")

    if not os.path.exists(events_tsv) or not os.path.exists(channels_tsv):
        return None, "Missing events.tsv or channels.tsv"

    events_rows = read_tsv(events_tsv)
    channels_rows = read_tsv(channels_tsv)

    t_onset = None
    for row in events_rows:
        trial_type = str(row.get("trial_type", "")).lower()
        if "onset" in trial_type:
            try:
                t_onset = float(row["onset"])
                break
            except (ValueError, KeyError):
                pass

    if t_onset is None:
        return None, "No valid 'sz onset' timestamp found in events.tsv"

    soz_gt = set()
    resect_gt = set()
    bad_channels = set()
    usable_channels = set()

    for row in channels_rows:
        ch_name = row.get("name")
        if not ch_name:
            continue
        status = str(row.get("status", "good")).lower()
        status_desc = str(row.get("status_description", "")).lower()

        if status == "bad":
            bad_channels.add(ch_name)
        else:
            usable_channels.add(ch_name)

        if "soz" in status_desc:
            soz_gt.add(ch_name)
        if "resect" in status_desc:
            resect_gt.add(ch_name)

    try:
        raw = mne.io.read_raw_edf(edf_path, preload=False, verbose=False)
    except Exception as e:
        return None, f"Failed to load EDF file: {e}"

    fs = float(raw.info["sfreq"])
    all_ch_names = list(raw.ch_names)
    duration = float(raw.times[-1])

    b_start = max(0.0, t_onset - 60.0)
    b_end = max(0.5, t_onset - 10.0)
    t_start = t_onset
    t_end = min(duration, t_onset + 15.0)

    if t_start >= duration or b_start >= b_end:
        try:
            raw.close()
        except Exception:
            pass
        return None, f"Invalid window times for recording duration {duration:.1f}s"

    picks = [i for i, name in enumerate(all_ch_names) if name in usable_channels]
    if not picks:
        picks = [i for i, name in enumerate(all_ch_names) if name not in bad_channels]

    if not picks:
        try:
            raw.close()
        except Exception:
            pass
        return None, "No usable channels remaining after filtering"

    ch_names = [all_ch_names[i] for i in picks]

    pad = 10.0
    span_start = max(0.0, min(b_start, t_start) - pad)
    span_end = min(duration, max(b_end, t_end) + pad)

    try:
        raw_span_data = raw.get_data(picks=picks, tmin=span_start, tmax=span_end)
    except Exception as e:
        return None, f"Failed to extract span slice: {e}"
    finally:
        try:
            raw.close()
        except Exception:
            pass
        del raw
        gc.collect()

    if raw_span_data.shape[1] == 0:
        return None, "Empty signal slice extracted"

    # Compute EI via standalone module
    ei_res = None
    ei_by_chan = {}
    if opts.mode in ("ei", "fused"):
        try:
            ei_res = compute_ei_pipeline(
                raw_data=raw_span_data,
                fs=fs,
                chn_names=ch_names,
                baseline_start=b_start,
                baseline_end=b_end,
                target_start=t_start,
                target_end=t_end,
                ei_method=opts.ei_method,
                band_low=opts.band_low,
                band_high=opts.band_high,
                mains_freq=opts.mains_freq,
                low_band=opts.er_low_band,
                high_band=opts.er_high_band,
                threshold_k=opts.threshold_k,
                span_start=span_start,
                reference=opts.reference,
            )
            # Bipolar scores name pairs; ground truth names contacts.
            ei_by_chan = (
                project_pairs_to_contacts(ei_res["ei_scores"], ei_res["pairs"])
                if opts.reference == "bipolar" else ei_res["ei_scores"]
            )
        except Exception as e:
            return None, f"EI signal processing error: {e}"

    # Compute HFO via standalone module
    hi_by_chan = {}
    if opts.mode in ("hfo", "fused"):
        inter_data, inter_fs, inter_chns = load_interictal_raw(ieeg_dir, set(ch_names))
        if inter_data is not None and inter_data.shape[1] > 0:
            hfo_res = compute_hfo_pipeline(
                raw_data=inter_data,
                fs=inter_fs,
                chn_names=inter_chns,
                mains_freq=opts.mains_freq,
            )
        else:
            # Fallback: compute HFO on baseline slice of ictal recording
            _idx = lambda t: int(round((t - span_start) * fs))
            b_data = raw_span_data[:, _idx(b_start):_idx(b_end)]
            hfo_res = compute_hfo_pipeline(
                raw_data=b_data,
                fs=fs,
                chn_names=ch_names,
                mains_freq=opts.mains_freq,
            )
        hi_by_chan = hfo_res["event_counts"]

    # Select ranking strategy
    if opts.mode == "ei":
        # Rank from ei_by_chan, not ei_res["ranked_channels"] -- under bipolar the
        # latter ranks pairs, and evaluation is per contact.
        scores_by_chan = ei_by_chan
        ranked_channels = sorted(
            scores_by_chan,
            key=lambda c: scores_by_chan[c] if np.isfinite(scores_by_chan[c]) else -np.inf,
            reverse=True,
        )
    elif opts.mode == "hfo":
        ranked_channels = hfo_res["ranked_channels"]
        scores_by_chan = hi_by_chan
    else:
        # Fused EI + HFO rank percentile score
        fused_rows = fuse_ei_hfo_scores(ch_names, ei_by_chan=ei_by_chan, hi_by_chan=hi_by_chan)
        ranked_channels = [r["contact"] for r in fused_rows]
        scores_by_chan = {r["contact"]: r["combined_score"] for r in fused_rows}

    top_k = len(soz_gt) if soz_gt else 5
    top_pred_soz = ranked_channels[:top_k]
    top_pred_scores = [round(scores_by_chan.get(ch, 0.0), 3) for ch in top_pred_soz]

    soz_hits = [ch for ch in top_pred_soz if ch in soz_gt]
    soz_recall = len(soz_hits) / len(soz_gt) if soz_gt else 0.0

    resect_hits = [ch for ch in top_pred_soz if ch in resect_gt]
    resect_concordance = len(resect_hits) / len(top_pred_soz) if top_pred_soz else 0.0

    run_label = base_name.split("_run-")[-1] if "_run-" in base_name else base_name

    res_dict = {
        "subject": sub_id,
        "run_id": base_name,
        "acq": parse_acq(base_name),
        "run_label": f"Run {run_label}",
        "t_onset_sec": round(t_onset, 2),
        "duration_sec": round(duration, 2),
        "total_channels": len(ch_names),
        "gt_soz_channels": ", ".join(sorted(soz_gt)),
        "gt_soz_count": len(soz_gt),
        "gt_resect_channels": ", ".join(sorted(resect_gt)),
        "gt_resect_count": len(resect_gt),
        "predicted_top_soz": ", ".join(top_pred_soz),
        "predicted_top_ei": ", ".join(map(str, top_pred_scores)),
        "soz_hits": ", ".join(soz_hits),
        "soz_hit_count": len(soz_hits),
        "soz_recall": round(soz_recall, 4),
        "resect_hits": ", ".join(resect_hits),
        "resect_hit_count": len(resect_hits),
        "resect_concordance": round(resect_concordance, 4),
        "status": "SUCCESS",
        "error_message": "",
    }
    return res_dict, None


def main():
    parser = argparse.ArgumentParser(description="Full Verification of BrainQuake v2 on OpenNeuro ds004100")
    parser.add_argument("--dataset-dir", default=DEFAULT_DATASET_DIR, help="Path to ds004100 dataset root")
    parser.add_argument("--output-csv", default=DEFAULT_OUTPUT_CSV, help="Output CSV filepath")
    parser.add_argument("--output-html", default=DEFAULT_OUTPUT_HTML, help="Output HTML filepath")
    parser.add_argument("--ei-method", choices=["band_ratio", "broadband"], default="band_ratio",
                        help="EI algorithm method ('band_ratio' or 'broadband')")
    parser.add_argument("--mode", choices=["ei", "hfo", "fused"], default="fused",
                        help="Evaluation mode ('ei', 'hfo', or 'fused')")
    parser.add_argument("--band-low", type=float, default=1.0, help="Filter low corner frequency in Hz")
    parser.add_argument("--band-high", type=float, default=500.0, help="Filter high corner frequency in Hz")
    parser.add_argument("--mains-freq", type=float, default=DEFAULT_MAINS_FREQ, help="Mains notch frequency in Hz")
    parser.add_argument("--threshold-k", type=float, default=ONSET_THRESHOLD_K, help="Onset detection threshold k")
    parser.add_argument("--reference", choices=["car", "bipolar"], default="car",
                        help="EI re-reference montage")
    parser.add_argument("--acq", choices=["all", "seeg", "ecog"], default="all",
                        help="Restrict to one BIDS acquisition type")

    args = parser.parse_args()
    args.er_low_band = BARTOLOMEI_LOW_BAND
    args.er_high_band = BARTOLOMEI_HIGH_BAND

    sub_dirs = sorted(glob.glob(os.path.join(args.dataset_dir, "sub-*")))
    total_subjects = len(sub_dirs)

    if total_subjects == 0:
        print(f"Error: No subject directories found in '{args.dataset_dir}'", flush=True)
        sys.exit(1)

    print("=" * 90, flush=True)
    print(f"   BRAINQUAKE v2 — DATASET VERIFICATION (OpenNeuro ds004100)", flush=True)
    print("=" * 90, flush=True)
    print(f"Dataset Location : {args.dataset_dir}", flush=True)
    print(f"Total Subjects   : {total_subjects}", flush=True)
    print(f"Evaluation Mode  : {args.mode.upper()}", flush=True)
    print(f"EI Algorithm     : {args.ei_method}", flush=True)
    print(f"Reference        : {args.reference}", flush=True)
    print(f"Output CSV       : {args.output_csv}", flush=True)
    print(f"Output HTML      : {args.output_html}", flush=True)
    print("=" * 90, flush=True)
    print(flush=True)

    all_results = []
    downloaded_subjects_count = 0
    start_time = time.time()

    def update_outputs():
        if not all_results:
            return
        fieldnames = list(all_results[0].keys())
        with open(args.output_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_results)

        eval_runs = [r for r in all_results if r["status"] == "SUCCESS"]
        runs_with_gt = [r for r in eval_runs if r["gt_soz_count"] > 0]
        mean_rec = float(np.mean([r["soz_recall"] for r in runs_with_gt])) if runs_with_gt else 0.0
        mean_res = float(np.mean([r["resect_concordance"] for r in eval_runs])) if eval_runs else 0.0
        summary_info = {
            "total_subjects": total_subjects,
            "downloaded_subjects": downloaded_subjects_count,
            "evaluated_runs": len(eval_runs),
            "mean_soz_recall": mean_rec,
            "mean_resect_concordance": mean_res,
            "total_elapsed_sec": round(time.time() - start_time, 2),
        }
        generate_html_report(all_results, summary_info, args.output_html, mode=args.mode, ei_method=args.ei_method)

    for idx, sub_dir in enumerate(sub_dirs, 1):
        sub_id = os.path.basename(sub_dir)
        ieeg_dir = os.path.join(sub_dir, "ses-presurgery", "ieeg")

        if not os.path.exists(ieeg_dir):
            print(f"[{idx}/{total_subjects}] {sub_id:<12} | SKIPPED (No ieeg directory)", flush=True)
            continue

        edfs = []
        all_edfs = glob.glob(os.path.join(ieeg_dir, "*task-ictal*.edf"))
        for f in all_edfs:
            try:
                if os.path.exists(f) and os.path.getsize(f) > 100000:
                    edfs.append(f)
            except Exception:
                pass

        if args.acq != "all":
            edfs = [f for f in edfs if parse_acq(os.path.basename(f)) == args.acq]

        edfs = sorted(edfs)
        if not edfs:
            print(f"[{idx}/{total_subjects}] {sub_id:<12} | SKIPPED (No downloaded binary EDF files)", flush=True)
            continue

        downloaded_subjects_count += 1
        n_runs = len(edfs)
        print(f"[{idx}/{total_subjects}] {sub_id:<12} | Processing {n_runs} downloaded run(s)...", flush=True)

        for r_idx, edf_path in enumerate(edfs, 1):
            base_name = os.path.basename(edf_path).replace("_ieeg.edf", "")
            run_start_t = time.time()

            try:
                res_dict, err_msg = process_run(edf_path, ieeg_dir, base_name, sub_id, args)
            except Exception as e:
                res_dict, err_msg = None, f"Unhandled exception: {e}"

            elapsed = time.time() - run_start_t

            if res_dict:
                all_results.append(res_dict)
                rec_str = f"{res_dict['soz_recall']:.0%}"
                print(f"   ↳ Run {r_idx}/{n_runs} ({base_name.split('_run-')[-1]}) | Done in {elapsed:.2f}s | "
                      f"GT SOZ: [{res_dict['gt_soz_channels']}] | "
                      f"Pred: [{res_dict['predicted_top_soz']}] | "
                      f"SOZ Recall: {rec_str}", flush=True)
            else:
                all_results.append({
                    "subject": sub_id,
                    "run_id": base_name,
                    "acq": parse_acq(base_name),
                    "run_label": base_name,
                    "t_onset_sec": 0,
                    "duration_sec": 0,
                    "total_channels": 0,
                    "gt_soz_channels": "",
                    "gt_soz_count": 0,
                    "gt_resect_channels": "",
                    "gt_resect_count": 0,
                    "predicted_top_soz": "",
                    "predicted_top_ei": "",
                    "soz_hits": "",
                    "soz_hit_count": 0,
                    "soz_recall": 0.0,
                    "resect_hits": "",
                    "resect_hit_count": 0,
                    "resect_concordance": 0.0,
                    "status": "SKIPPED",
                    "error_message": err_msg,
                })
                print(f"   ↳ Run {r_idx}/{n_runs} ({base_name.split('_run-')[-1]}) | SKIPPED ({err_msg})", flush=True)

            update_outputs()
            gc.collect()

    total_elapsed = time.time() - start_time
    eval_runs = [r for r in all_results if r["status"] == "SUCCESS"]
    runs_with_gt = [r for r in eval_runs if r["gt_soz_count"] > 0]
    mean_recall = np.mean([r["soz_recall"] for r in runs_with_gt]) if runs_with_gt else 0.0
    mean_resect = np.mean([r["resect_concordance"] for r in eval_runs]) if eval_runs else 0.0

    print(flush=True)
    print("=" * 90, flush=True)
    print("                      VERIFICATION COMPLETE SUMMARY", flush=True)
    print("=" * 90, flush=True)
    print(f"Total Elapsed Time           : {total_elapsed:.2f} s", flush=True)
    print(f"Total Subjects Scanned       : {total_subjects}", flush=True)
    print(f"Subjects with Downloaded Data: {downloaded_subjects_count}", flush=True)
    print(f"Successfully Evaluated Runs  : {len(eval_runs)}", flush=True)
    print(f"Overall Mean SOZ Recall @ K  : {mean_recall:.1%}", flush=True)
    print(f"Resection Area Concordance   : {mean_resect:.1%}", flush=True)
    print(f"CSV Saved To                 : {args.output_csv}", flush=True)
    print(f"HTML Report Saved To         : {args.output_html}", flush=True)
    print("=" * 90, flush=True)


if __name__ == "__main__":
    main()
