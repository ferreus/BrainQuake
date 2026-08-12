"""Multi-Modal SOZ (Seizure Onset Zone) Rank Percentile Fusion.

Pure numpy algorithms -- combines Epileptogenicity Index (EI) and High-Frequency Oscillation (HFO)
percentile ranks into a unified target ranking.
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


def fuse_ei_hfo_scores(contact_xyz, ei_by_chan=None, hi_by_chan=None):
    """Fuse EI and HFO channel scores into percentile rankings and combined score.

    Args:
        contact_xyz: Dict mapping contact name -> (x, y, z) 3D coordinate array,
                     or list of contact names.
        ei_by_chan: Dict mapping channel name -> EI score float (or None).
        hi_by_chan: Dict mapping channel name -> HFO event count float (or None).

    Returns:
        List of dict rows sorted by combined_score descending.
    """
    if isinstance(contact_xyz, (list, tuple, set)):
        names = sorted(contact_xyz)
        xyz_dict = {n: (0.0, 0.0, 0.0) for n in names}
    elif isinstance(contact_xyz, dict):
        names = sorted(contact_xyz.keys())
        xyz_dict = contact_xyz
    else:
        raise TypeError("contact_xyz must be a dict or list of channel names")

    ei_by_chan = ei_by_chan or {}
    hi_by_chan = hi_by_chan or {}

    ei_vals = np.array([ei_by_chan.get(n, np.nan) for n in names], dtype=float)
    hi_vals = np.array([hi_by_chan.get(n, np.nan) for n in names], dtype=float)

    ei_mask = ~np.isnan(ei_vals)
    hi_mask = ~np.isnan(hi_vals)
    ei_pct = np.full(len(names), np.nan)
    hi_pct = np.full(len(names), np.nan)

    if ei_mask.any():
        ei_pct[ei_mask] = rank_pct(ei_vals[ei_mask])
    if hi_mask.any():
        hi_pct[hi_mask] = rank_pct(hi_vals[hi_mask])

    stacked = np.vstack([ei_pct, hi_pct])
    valid_counts = np.sum(~np.isnan(stacked), axis=0)
    sums = np.nansum(stacked, axis=0)
    combined = np.divide(sums, valid_counts, out=np.zeros_like(sums), where=valid_counts > 0)

    ei_thresh = np.nanmean(ei_vals) + np.nanstd(ei_vals) if ei_mask.any() else np.inf
    hi_thresh = np.nanmean(hi_vals) + np.nanstd(hi_vals) if hi_mask.any() else np.inf
    suspect_ei = ei_vals > ei_thresh
    suspect_hi = hi_vals > hi_thresh

    rows = []
    for i, name in enumerate(names):
        rows.append({
            'contact': name,
            'x': float(xyz_dict[name][0]), 'y': float(xyz_dict[name][1]), 'z': float(xyz_dict[name][2]),
            'ei': float(ei_vals[i]) if np.isfinite(ei_vals[i]) else np.nan,
            'hi': float(hi_vals[i]) if np.isfinite(hi_vals[i]) else np.nan,
            'ei_percentile': float(ei_pct[i]) if np.isfinite(ei_pct[i]) else np.nan,
            'hi_percentile': float(hi_pct[i]) if np.isfinite(hi_pct[i]) else np.nan,
            'combined_score': float(combined[i]),
            'suspect_ei': bool(suspect_ei[i]),
            'suspect_hi': bool(suspect_hi[i]),
        })
    rows.sort(key=lambda r: r['combined_score'], reverse=True)
    return rows
