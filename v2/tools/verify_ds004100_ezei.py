#!/usr/bin/env python3
"""Dataset Verification Script for EZEI R Package (Bartolomei et al. Multitaper EI)
on OpenNeuro ds004100 dataset.

Features:
- Clear subject-by-subject progress logging
- Signal downsampling for fs > 500 Hz to prevent R multitaper spectrogram timeouts
- Exports CSV and interactive HTML report
"""

import argparse
import csv
import gc
import glob
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
import mne

# Force unbuffered output so logs print immediately per subject
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from verification_report import generate_html_report

DEFAULT_DATASET_DIR = "/media/data/eeg/ds004100"
DEFAULT_OUTPUT_CSV = "/home/ferreus/dev/BrainQuake/v2/verification_results/ds004100_ezei.csv"
DEFAULT_OUTPUT_HTML = "/home/ferreus/dev/BrainQuake/v2/verification_results/ds004100_ezei.html"
R_RUNNER = os.path.join(TOOLS_DIR, "run_ezei_batch.R")


def read_tsv(filepath):
    """Read TSV file into a list of dict rows."""
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter="\t")
        return list(reader)


def process_single_run(args_tuple):
    """Worker function to process a single seizure run."""
    edf_path, ieeg_dir, base_name, sub_id = args_tuple

    events_tsv = os.path.join(ieeg_dir, f"{base_name}_events.tsv")
    channels_tsv = os.path.join(ieeg_dir, f"{base_name}_channels.tsv")

    if not os.path.exists(events_tsv) or not os.path.exists(channels_tsv):
        return None, "Missing events.tsv or channels.tsv", base_name, sub_id

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
        return None, "No valid seizure onset timestamp found in events.tsv", base_name, sub_id

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
        return None, f"Failed to load EDF file: {e}", base_name, sub_id

    fs = float(raw.info["sfreq"])
    all_ch_names = list(raw.ch_names)
    duration = float(raw.times[-1])

    # Slices around seizure onset (-10s baseline, +20s target seizure window)
    t_start = max(0.0, t_onset - 10.0)
    t_end = min(duration, t_onset + 20.0)

    if t_onset >= duration or t_start >= t_end:
        try:
            raw.close()
        except Exception:
            pass
        return None, f"Invalid window times for recording duration {duration:.1f}s", base_name, sub_id

    picks = [i for i, name in enumerate(all_ch_names) if name in usable_channels]
    if not picks:
        picks = [i for i, name in enumerate(all_ch_names) if name not in bad_channels]

    if not picks:
        try:
            raw.close()
        except Exception:
            pass
        return None, "No usable channels remaining", base_name, sub_id

    ch_names = [all_ch_names[i] for i in picks]

    try:
        data = raw.get_data(picks=picks, tmin=t_start, tmax=t_end)
    except Exception as e:
        return None, f"Failed to extract signal slice: {e}", base_name, sub_id
    finally:
        try:
            raw.close()
        except Exception:
            pass
        del raw
        gc.collect()

    if data.shape[1] == 0:
        return None, "Empty signal slice extracted", base_name, sub_id

    # Downsample high sampling rate data (fs > 500 Hz) to 500 Hz for efficient multitaper computation
    if fs > 500.0:
        target_fs = 500.0
        n_samples_orig = data.shape[1]
        n_samples_new = int(round(n_samples_orig * (target_fs / fs)))
        try:
            from scipy.signal import resample
            data = resample(data, n_samples_new, axis=1)
            fs = target_fs
        except Exception:
            pass

    # Unique temp file per task
    pid = os.getpid()
    tid = time.time_ns()
    tmp_in = f"/tmp/ezei_in_{pid}_{tid}.csv"
    tmp_out = f"/tmp/ezei_out_{pid}_{tid}.csv"

    try:
        with open(tmp_in, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for i, ch in enumerate(ch_names):
                writer.writerow([ch] + list(data[i]))

        # Run EZEI via Rscript
        cmd = ["Rscript", R_RUNNER, tmp_in, str(fs), tmp_out]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if p.returncode != 0 or not os.path.exists(tmp_out):
            err_msg = p.stderr.strip() or p.stdout.strip() or "Rscript failed"
            return None, f"EZEI execution error: {err_msg}", base_name, sub_id

        # Read EZEI output scores
        scores_by_chan = {}
        with open(tmp_out, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ch = row["channel"]
                try:
                    sc = float(row["score"])
                except (ValueError, KeyError):
                    sc = 0.0
                scores_by_chan[ch] = sc

    finally:
        if os.path.exists(tmp_in):
            try:
                os.remove(tmp_in)
            except Exception:
                pass
        if os.path.exists(tmp_out):
            try:
                os.remove(tmp_out)
            except Exception:
                pass

    # Rank channels by EZEI score
    ranked_channels = sorted(ch_names, key=lambda c: scores_by_chan.get(c, 0.0), reverse=True)

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
    return res_dict, None, base_name, sub_id


def main():
    parser = argparse.ArgumentParser(description="Dataset Verification of EZEI R Package on ds004100")
    parser.add_argument("--dataset-dir", default=DEFAULT_DATASET_DIR, help="Path to ds004100 dataset root")
    parser.add_argument("--output-csv", default=DEFAULT_OUTPUT_CSV, help="Output CSV filepath")
    parser.add_argument("--output-html", default=DEFAULT_OUTPUT_HTML, help="Output HTML filepath")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel worker processes")

    args = parser.parse_args()

    sub_dirs = sorted(glob.glob(os.path.join(args.dataset_dir, "sub-*")))
    total_subjects = len(sub_dirs)

    if total_subjects == 0:
        print(f"Error: No subject directories found in '{args.dataset_dir}'", flush=True)
        sys.exit(1)

    print("=" * 90, flush=True)
    print(f"   EZEI R PACKAGE — DATASET VERIFICATION (OpenNeuro ds004100)", flush=True)
    print("=" * 90, flush=True)
    print(f"Dataset Location : {args.dataset_dir}", flush=True)
    print(f"Total Subjects   : {total_subjects}", flush=True)
    print(f"Parallel Workers : {args.workers}", flush=True)
    print(f"Output CSV       : {args.output_csv}", flush=True)
    print(f"Output HTML      : {args.output_html}", flush=True)
    print("=" * 90, flush=True)
    print(flush=True)

    # Group runs by subject for clear subject-by-subject logging
    subject_tasks = []
    total_runs_count = 0
    for sub_dir in sub_dirs:
        sub_id = os.path.basename(sub_dir)
        ieeg_dir = os.path.join(sub_dir, "ses-presurgery", "ieeg")
        if not os.path.exists(ieeg_dir):
            continue
        all_edfs = glob.glob(os.path.join(ieeg_dir, "*task-ictal*.edf"))
        run_tasks = []
        for edf_path in sorted(all_edfs):
            try:
                if os.path.exists(edf_path) and os.path.getsize(edf_path) > 100000:
                    base_name = os.path.basename(edf_path).replace("_ieeg.edf", "")
                    run_tasks.append((edf_path, ieeg_dir, base_name, sub_id))
            except Exception:
                pass
        if run_tasks:
            subject_tasks.append((sub_id, run_tasks))
            total_runs_count += len(run_tasks)

    print(f"Found {len(subject_tasks)} subjects with {total_runs_count} total ictal runs.\n", flush=True)

    all_results = []
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
            "downloaded_subjects": len(set(r["subject"] for r in all_results)),
            "evaluated_runs": len(eval_runs),
            "mean_soz_recall": mean_rec,
            "mean_resect_concordance": mean_res,
            "total_elapsed_sec": round(time.time() - start_time, 2),
        }
        generate_html_report(all_results, summary_info, args.output_html, mode="ei", ei_method="EZEI (R Package)")

    for s_idx, (sub_id, run_tasks) in enumerate(subject_tasks, 1):
        print(f"┌── [Subject {s_idx:02d}/{len(subject_tasks):02d}] {sub_id} ({len(run_tasks)} runs)", flush=True)

        if args.workers > 1 and len(run_tasks) > 1:
            with ProcessPoolExecutor(max_workers=min(args.workers, len(run_tasks))) as executor:
                futures = {executor.submit(process_single_run, task): task for task in run_tasks}
                for future in as_completed(futures):
                    try:
                        res_dict, err, base_name, _ = future.result()
                    except Exception as exc:
                        task_info = futures[future]
                        base_name = task_info[2]
                        res_dict, err = None, str(exc)

                    if res_dict:
                        all_results.append(res_dict)
                        rec_str = f"{res_dict['soz_recall']*100:5.1f}%" if res_dict["gt_soz_count"] > 0 else "  N/A"
                        hit_str = f"{res_dict['soz_hit_count']}/{res_dict['gt_soz_count']}" if res_dict["gt_soz_count"] > 0 else "N/A"
                        print(
                            f"│   ├── {res_dict['run_label']:<8} | SOZ Hits: {hit_str:<6} | Recall: {rec_str:<6} | Status: SUCCESS",
                            flush=True,
                        )
                    else:
                        fail_dict = {
                            "subject": sub_id,
                            "run_id": base_name,
                            "run_label": base_name,
                            "t_onset_sec": 0.0,
                            "duration_sec": 0.0,
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
                            "status": "ERROR",
                            "error_message": err,
                        }
                        all_results.append(fail_dict)
                        print(f"│   ├── {base_name:<20} | Status: ERROR ({err})", flush=True)
                    update_outputs()
        else:
            for task in run_tasks:
                res_dict, err, base_name, _ = process_single_run(task)
                if res_dict:
                    all_results.append(res_dict)
                    rec_str = f"{res_dict['soz_recall']*100:5.1f}%" if res_dict["gt_soz_count"] > 0 else "  N/A"
                    hit_str = f"{res_dict['soz_hit_count']}/{res_dict['gt_soz_count']}" if res_dict["gt_soz_count"] > 0 else "N/A"
                    print(
                        f"│   ├── {res_dict['run_label']:<8} | SOZ Hits: {hit_str:<6} | Recall: {rec_str:<6} | Status: SUCCESS",
                        flush=True,
                    )
                else:
                    fail_dict = {
                        "subject": sub_id,
                        "run_id": base_name,
                        "run_label": base_name,
                        "t_onset_sec": 0.0,
                        "duration_sec": 0.0,
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
                        "status": "ERROR",
                        "error_message": err,
                    }
                    all_results.append(fail_dict)
                    print(f"│   ├── {base_name:<20} | Status: ERROR ({err})", flush=True)
                update_outputs()

        print(f"└── Done [Subject {s_idx:02d}/{len(subject_tasks):02d}] {sub_id}\n", flush=True)

    print("=" * 90, flush=True)
    print("VERIFICATION COMPLETE", flush=True)
    print(f"Total Evaluated Runs: {len(all_results)}", flush=True)
    print(f"Results CSV: {args.output_csv}", flush=True)
    print(f"Report HTML: {args.output_html}", flush=True)
    print("=" * 90, flush=True)


if __name__ == "__main__":
    main()
