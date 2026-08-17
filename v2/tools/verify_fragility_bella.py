#!/usr/bin/env python3
"""Validate pure Python Neural Fragility (app.sigproc.fragility) on Bella's 8 seizures.

Cross-validates against reference EZFragility outputs in data/ezfragility_result.txt.
"""

import os
import re
import sys
import time
from collections import Counter
import mne
import numpy as np

# Add server directory to path
SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../server"))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from app.sigproc.fragility import compute_fragility_pipeline

SEEG_CONTACT_RE = re.compile(r"^[A-Za-z]'?\d+$")
SHAFT_RE = re.compile(r"^([A-Za-z]+'?)\d+$")

BELLA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/Bella/BellaEDF"))

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


def get_shaft(contact_name: str) -> str:
    m = SHAFT_RE.match(contact_name.strip())
    return m.group(1) if m else contact_name


def main():
    print(f"Loading Bella EDFs from: {BELLA_DIR}", flush=True)
    if not os.path.exists(BELLA_DIR):
        print(f"Error: {BELLA_DIR} not found.", flush=True)
        sys.exit(1)

    crop_pre = 5.0   # seconds before onset
    crop_post = 3.0  # seconds after onset
    top_n_contacts = 10  # top contacts per seizure voting for shaft

    all_results = {}
    shaft_sizes = None

    t_total_start = time.time()
    for label, filename, pattern in SEIZURE_FILES:
        edf_path = os.path.join(BELLA_DIR, filename)
        if not os.path.exists(edf_path):
            print(f"Warning: {edf_path} not found, skipping {label}.", flush=True)
            continue

        raw = mne.io.read_raw_edf(edf_path, preload=False, stim_channel=None, verbose="error")
        fs = float(raw.info["sfreq"])
        all_ch = list(raw.ch_names)
        contacts = [c for c in all_ch if SEEG_CONTACT_RE.match(c.strip())]

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
            num_freqs=16,
            l2_reg=1e-5,
            eval_window_s=(-crop_pre, crop_post),
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
        return

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


if __name__ == "__main__":
    main()
