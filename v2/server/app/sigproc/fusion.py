"""Multi-Modal SOZ (Seizure Onset Zone) Rank Percentile Fusion.

Pure numpy -- combines any number of processes (EI, HFO, fragility), each with
any number of runs, into one per-contact suspicion score.
"""
import logging

import numpy as np

logger = logging.getLogger(__name__)


def rank_pct(values):
    """Compute percentile rank [0.0, 1.0] of values, preserving original indices."""
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return np.array([], dtype=float)
    order = np.argsort(values)
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(len(values))
    denom = max(len(values) - 1, 1)
    return ranks / denom


def describe_name_overlap(contact_names, by_chan, kind):
    """Evaluate contact/channel label alignment for potential label mismatches."""
    contacts = set(contact_names)
    channels = set(by_chan)
    matched = sorted(contacts & channels)
    return {
        "kind": kind,
        "matched": len(matched),
        "n_contacts": len(contacts),
        "n_channels": len(channels),
        "unmatched_contacts": sorted(contacts - channels)[:10],
        "unused_channels": sorted(channels - contacts)[:10],
    }


def _mean_ignoring_nan(stacked, counts):
    out = np.full(stacked.shape[1], np.nan)
    np.divide(np.nansum(stacked, axis=0), counts, out=out, where=counts > 0)
    return out


def _run_percentiles(names, scores):
    """One run's scores as a per-contact percentile, NaN where the contact is absent."""
    vals = np.array([scores.get(n, np.nan) for n in names], dtype=float)
    pct = np.full(len(names), np.nan)
    mask = np.isfinite(vals)
    if mask.any():
        pct[mask] = rank_pct(vals[mask])
    return vals, pct


def fuse_contact_scores(contact_xyz, runs_by_process):
    """Fuse per-process, per-run channel scores into one ranked contact table.

    Args:
        contact_xyz: {contact name -> (x, y, z)} or a bare list of contact names.
        runs_by_process: {process name -> [ {channel name -> score}, ... ]}, one
            dict per finished run of that process.

    Each run is percentiled on its own before averaging: raw EI, HFO counts and
    fragility live on different scales, and even within one process two
    recordings' raw values are not comparable. Averaging per process first keeps
    five fragility runs from outvoting one EI run.

    Returns rows sorted by combined_score descending, with per-process columns
    `{p}` (mean raw), `{p}_percentile`, `{p}_n_runs` and `suspect_{p}`.
    """
    if isinstance(contact_xyz, dict):
        names = sorted(contact_xyz)
        xyz_dict = contact_xyz
    elif isinstance(contact_xyz, (list, tuple, set)):
        names = sorted(contact_xyz)
        xyz_dict = {n: (0.0, 0.0, 0.0) for n in names}
    else:
        raise TypeError("contact_xyz must be a dict or list of contact names")

    columns = {}
    process_pcts = []
    for process, runs in runs_by_process.items():
        runs = [r for r in (runs or []) if r]
        if not runs:
            continue
        per_run = [_run_percentiles(names, r) for r in runs]
        raw = np.array([v for v, _ in per_run], dtype=float)
        pct = np.array([p for _, p in per_run], dtype=float)
        # A contact missing from some runs averages over the ones that have it;
        # one absent from all of them stays NaN (np.nanmean would warn instead).
        n_runs = np.sum(np.isfinite(pct), axis=0)
        mean_raw = _mean_ignoring_nan(raw, n_runs)
        mean_pct = _mean_ignoring_nan(pct, n_runs)

        thresh = (
            np.nanmean(mean_pct) + np.nanstd(mean_pct)
            if np.isfinite(mean_pct).any() else np.inf
        )
        columns[process] = {
            "raw": mean_raw,
            "pct": mean_pct,
            "n_runs": n_runs,
            "suspect": mean_pct > thresh,
        }
        process_pcts.append(mean_pct)

    if process_pcts:
        stacked = np.vstack(process_pcts)
        valid = np.sum(np.isfinite(stacked), axis=0)
        combined = np.zeros(len(names))
        np.divide(np.nansum(stacked, axis=0), valid, out=combined, where=valid > 0)
    else:
        combined = np.zeros(len(names))

    rows = []
    for i, name in enumerate(names):
        row = {
            'contact': name,
            'x': float(xyz_dict[name][0]),
            'y': float(xyz_dict[name][1]),
            'z': float(xyz_dict[name][2]),
        }
        for process, col in columns.items():
            row[process] = float(col["raw"][i])
            row[f'{process}_percentile'] = float(col["pct"][i])
            row[f'{process}_n_runs'] = int(col["n_runs"][i])
            row[f'suspect_{process}'] = bool(col["suspect"][i])
        row['combined_score'] = float(combined[i])
        rows.append(row)

    rows.sort(key=lambda r: r['combined_score'], reverse=True)
    return rows


def fused_processes(rows):
    """Which processes a fused table carries, in column order."""
    if not rows:
        return []
    return [k[len('suspect_'):] for k in rows[0] if k.startswith('suspect_')]
