#!/usr/bin/env python3
"""Verification Script for BrainQuake v2 EI and HFO Algorithms on OpenNeuro ds004100 dataset.

Runs signal processing verification locally, saves results to verification_results.csv,
and prints a side-by-side comparison table per subject against clinical ground truth.
"""
import glob
import os
import sys
import csv
import json
import numpy as np
import mne

# Add server directory to sys.path to import BrainQuake modules
SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../server"))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from app.sigproc.ei import compute_band_ratio, compute_ei_index, BARTOLOMEI_LOW_BAND, BARTOLOMEI_HIGH_BAND

DATASET_DIR = "/media/data/eeg/ds004100"
OUTPUT_CSV = "/home/ferreus/dev/BrainQuake/verification_results.csv"

def read_tsv(filepath):
    """Read TSV file into a list of dict rows, stripping UTF-8 BOM if present."""
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter="\t")
        return list(reader)

def process_subject_ictal(subject_dir):
    results = []
    sub_id = os.path.basename(subject_dir)
    ieeg_dir = os.path.join(subject_dir, "ses-presurgery", "ieeg")
    if not os.path.exists(ieeg_dir):
        return results

    # Find all downloaded (non-empty) ictal EDF files
    ictal_edfs = [
        f for f in glob.glob(os.path.join(ieeg_dir, "*task-ictal*.edf"))
        if os.path.exists(f) and os.path.getsize(f) > 100000
    ]

    for edf_path in sorted(ictal_edfs):
        base_name = os.path.basename(edf_path).replace("_ieeg.edf", "")
        events_tsv = os.path.join(ieeg_dir, f"{base_name}_events.tsv")
        channels_tsv = os.path.join(ieeg_dir, f"{base_name}_channels.tsv")

        if not os.path.exists(events_tsv) or not os.path.exists(channels_tsv):
            print(f"Skipping {base_name}: missing events or channels tsv")
            continue

        # Load metadata
        events_rows = read_tsv(events_tsv)
        channels_rows = read_tsv(channels_tsv)

        # Find seizure onset time
        sz_onset_time = None
        for row in events_rows:
            trial_type = str(row.get("trial_type", "")).lower()
            if "onset" in trial_type:
                sz_onset_time = float(row["onset"])
                break

        if sz_onset_time is None:
            print(f"Skipping {base_name}: no seizure onset event found")
            continue

        t_onset = sz_onset_time

        # Extract Ground Truth SOZ & Resected channels
        soz_gt = set()
        resect_gt = set()
        bad_channels = set()
        usable_channels = set()

        for row in channels_rows:
            ch_name = row.get("name")
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

        # Load EDF
        raw = mne.io.read_raw_edf(edf_path, preload=True, verbose=False)
        fs = raw.info["sfreq"]
        ch_names = raw.ch_names
        duration = float(raw.times[-1])

        # Pick channels: exclude bad / non-target channels
        picks = [i for i, name in enumerate(ch_names) if name in usable_channels]
        if not picks:
            picks = [i for i, name in enumerate(ch_names) if name not in bad_channels]

        raw.pick(picks)
        ch_names = raw.ch_names
        data, _ = raw[:]

        # Window configuration
        b_start = max(0.0, t_onset - 60.0)
        b_end = max(0.5, t_onset - 10.0)
        t_start = t_onset
        t_end = min(duration, t_onset + 15.0)

        # Slice sample indices
        b_idx0, b_idx1 = int(round(b_start * fs)), int(round(b_end * fs))
        t_idx0, t_idx1 = int(round(t_start * fs)), int(round(t_end * fs))

        base_data = data[:, b_idx0:b_idx1]
        target_data = data[:, t_idx0:t_idx1]

        # Compute EI using Bartolomei Band Ratio
        norm_target, norm_base = compute_band_ratio(
            target_data, base_data, fs,
            low_band=BARTOLOMEI_LOW_BAND, high_band=BARTOLOMEI_HIGH_BAND
        )
        ei, ei_raw, hfer, time_coef = compute_ei_index(norm_target, norm_base, fs)

        # Build ranking
        ei_scores = {name: float(ei[i]) for i, name in enumerate(ch_names)}
        ranked_channels = sorted(
            ei_scores, key=lambda k: ei_scores[k] if np.isfinite(ei_scores[k]) else -np.inf,
            reverse=True,
        )

        top_k = len(soz_gt) if soz_gt else 5
        top_pred_soz = ranked_channels[:top_k]
        top_pred_ei_values = [round(ei_scores[ch], 3) for ch in top_pred_soz]

        # Metric: Hits in top K
        hits = [ch for ch in top_pred_soz if ch in soz_gt]
        recall = len(hits) / len(soz_gt) if soz_gt else 0.0

        results.append({
            "subject": sub_id,
            "run": base_name,
            "t_onset_sec": t_onset,
            "gt_soz_channels": ", ".join(sorted(soz_gt)),
            "gt_soz_count": len(soz_gt),
            "predicted_top_soz": ", ".join(top_pred_soz),
            "predicted_top_ei": ", ".join(map(str, top_pred_ei_values)),
            "soz_hits": ", ".join(hits),
            "hit_count": len(hits),
            "soz_recall": round(recall, 3),
        })

    return results

def main():
    print("Starting BrainQuake Verification on OpenNeuro ds004100...")
    all_results = []
    
    sub_dirs = sorted(glob.glob(os.path.join(DATASET_DIR, "sub-*")))
    for sub_dir in sub_dirs:
        res = process_subject_ictal(sub_dir)
        all_results.extend(res)

    if not all_results:
        print("No valid datasets processed.")
        return

    # Write output to CSV
    fieldnames = list(all_results[0].keys())
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)

    print(f"\nVerification completed! Saved {len(all_results)} run evaluations to: {OUTPUT_CSV}\n")

    # Display Side-by-Side Comparison Table
    print("=" * 115)
    print(f"{'SUBJECT & RUN':<42} | {'GROUND TRUTH SOZ':<20} | {'PREDICTED TOP SOZ (EI)':<35} | {'RECALL':<8}")
    print("=" * 115)
    for row in all_results:
        run_name = f"{row['subject']} ({row['run'].split('_run-')[-1]})"
        gt = row['gt_soz_channels'] if row['gt_soz_channels'] else "None"
        pred_str = ", ".join([f"{ch}:{ei}" for ch, ei in zip(row['predicted_top_soz'].split(', '), row['predicted_top_ei'].split(', '))])
        rec = f"{row['soz_recall']:.0%}"
        print(f"{run_name:<42} | {gt:<20} | {pred_str:<35} | {rec:<8}")
    print("=" * 115)

if __name__ == "__main__":
    main()
