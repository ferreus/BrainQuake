"""Epileptogenicity Index numerics, after Bartolomei et al. 2008.

numpy + scipy only -- importable from a notebook without the server stack.
"""
import json
import logging
import os
from typing import NamedTuple

import numpy as np
from scipy.signal import convolve2d

from app.sigproc.filters import DEFAULT_MAINS_FREQ, bandpass, filter_for_display, max_band_high
from app.sigproc.montage import apply_bipolar, project_pairs_to_contacts

logger = logging.getLogger(__name__)

# EI parameters, after Bartolomei et al. 2008. tau keeps the time term finite for
# the first channel and sets how fast later ones decay; the energy window is the
# span integrated from each channel's own onset.
EI_TAU_SEC = 1.0
EI_ENERGY_WINDOW_SEC = 0.25

# Onset threshold: baseline median + k robust sigmas. 1.4826 * MAD estimates
# sigma for Gaussian data, so k is comparable to the old multiplier.
ONSET_THRESHOLD_K = 20.0
MAD_TO_SIGMA = 1.4826

# Bartolomei's energy ratio bands: (theta+alpha) against (beta+gamma).
BARTOLOMEI_LOW_BAND = (3.5, 12.4)
BARTOLOMEI_HIGH_BAND = (12.4, 97.0)

# A window whose first moments already contain the discharge makes every onset
# land at sample 0, which flattens the time term into a constant -- EI then
# silently degenerates into a pure energy ranking.
DEGENERATE_EDGE_SEC = 0.5
DEGENERATE_FRACTION = 0.2

# Ported near-verbatim from client_ictal.py's module-scope compute_* functions
# (git tag legacy-final; already pure numpy/scipy, no Qt/GUI dependency). The interactive
# baseline/target range-select and band-filter text fields become explicit
# request parameters (see routers/ictal.py) instead of mouse clicks.


def _moving_sum(data, window):
    """Sliding-window sum along axis 1, reflect-padded at both ends.

    convolve2d('same') zero-pads instead, which suppressed the energy of the
    first and last window/2 samples -- and determine_threshold_onset() scans
    from sample 0, straight through that region, so onsets were biased later.
    """
    pad = min(window, data.shape[1] - 1)
    padded = np.pad(data, ((0, 0), (pad, pad)), mode="reflect")
    return convolve2d(padded, np.ones((1, window)), 'same')[:, pad:padded.shape[1] - pad]


def compute_hfer(target_data, base_data, fs):
    """High-Frequency Energy Ratio per channel.

    Sliding 0.5 s windowed energy of each channel, divided by that channel's
    mean windowed energy during the baseline. Values >> 1 mean the channel got
    much "louder" than its own quiet period.

    Args:
        target_data: (n_channels, n_target_samples) filtered signal, volts.
        base_data:   (n_channels, n_base_samples) filtered signal, volts.
        fs:          sampling rate, Hz.

    Returns:
        (norm_target_energy, norm_base_energy), each (n_channels, n_samples_of_that_window),
        dimensionless (ratio to baseline mean energy).
    """
    target_sq = target_data ** 2
    base_sq = base_data ** 2
    window = int(fs / 2.0)
    target_energy = _moving_sum(target_sq, window)
    base_energy = _moving_sum(base_sq, window)
    base_energy_ref = np.sum(base_energy, axis=1) / base_energy.shape[1]

    # A flat/disconnected channel has zero baseline energy, which silently
    # produced inf/nan here and was zeroed much later in compute_ei_index.
    # Name the channels instead so a dead electrode is visible in the job log.
    dead = np.flatnonzero(base_energy_ref == 0)
    if dead.size:
        logger.warning(
            "%d channel(s) have zero baseline energy (indices %s); their HFER is undefined "
            "and their EI will be forced to 0", dead.size, dead.tolist(),
        )

    with np.errstate(divide="ignore", invalid="ignore"):
        norm_target_energy = target_energy / base_energy_ref[:, np.newaxis]
        norm_base_energy = base_energy / base_energy_ref[:, np.newaxis]
    return norm_target_energy, norm_base_energy


def compute_band_ratio(target_data, base_data, fs,
                       low_band=BARTOLOMEI_LOW_BAND, high_band=BARTOLOMEI_HIGH_BAND):
    """Bartolomei's energy ratio: E(beta+gamma) / E(theta+alpha), per channel.

    Same contract as compute_hfer -- (norm_target, norm_base), each normalised
    by that channel's mean baseline value -- so onset detection and the EI
    index are shared between the two methods.

    Expects `data` already common-average referenced and notched; it only
    splits into sub-bands.

    Returns:
        (norm_target, norm_base), dimensionless (ratio to baseline mean ER).
    """
    window = int(fs / 2.0)

    def energy_ratio(data):
        low = bandpass(data, fs, *low_band, context="band_ratio low")
        high = bandpass(data, fs, *high_band, context="band_ratio high")
        low_energy = _moving_sum(low ** 2, window)
        high_energy = _moving_sum(high ** 2, window)
        with np.errstate(divide="ignore", invalid="ignore"):
            return high_energy / low_energy

    target_er = energy_ratio(target_data)
    base_er = energy_ratio(base_data)

    with np.errstate(divide="ignore", invalid="ignore"):
        base_ref = np.nanmean(base_er, axis=1)

    dead = np.flatnonzero(~np.isfinite(base_ref) | (base_ref == 0))
    if dead.size:
        logger.warning(
            "%d channel(s) have no usable baseline energy ratio (indices %s); their EI "
            "is undefined", dead.size, dead.tolist(),
        )

    with np.errstate(divide="ignore", invalid="ignore"):
        return target_er / base_ref[:, np.newaxis], base_er / base_ref[:, np.newaxis]


class OnsetResult(NamedTuple):
    """`onset` is a sample index into the target window; `crossed` says whether
    it means anything. Encoding "never crossed" as an out-of-range index is what
    let non-crossing channels silently collapse to zero energy."""
    onset: np.ndarray
    crossed: np.ndarray


def determine_threshold_onset(target, base, threshold_k=ONSET_THRESHOLD_K):
    """Per-channel onset as a sample index into the target window.

    A channel "onsets" at the first target sample whose normalised energy
    exceeds (baseline max + 20 * baseline std). Channels that never cross are
    assigned n_target_samples, i.e. "after the end of the window".

    Args:
        target: (n_channels, n_target_samples) normalised energy from compute_hfer.
        base:   (n_channels, n_base_samples) normalised energy from compute_hfer.

    Returns:
        OnsetResult(onset, crossed): sample indices (not seconds), and a mask
        saying which of them are real detections.
    """
    # median + k*MAD, not max + k*std: with the max in the threshold, one
    # baseline artefact (an electrode pop, a movement transient) put a channel's
    # bar permanently out of reach and it never registered an onset at all.
    # A dead channel's normalised baseline is entirely inf/nan. Skip those rows
    # rather than feeding an all-NaN slice to nanmedian; their threshold stays
    # NaN, every comparison against it is False, and they never cross.
    safe_base = np.where(np.isfinite(base), base, np.nan)
    has_data = np.isfinite(base).any(axis=1)
    median = np.full(base.shape[0], np.nan)
    mad = np.full(base.shape[0], np.nan)
    if has_data.any():
        rows = safe_base[has_data]
        median[has_data] = np.nanmedian(rows, axis=1)
        mad[has_data] = np.nanmedian(np.abs(rows - median[has_data, np.newaxis]), axis=1)
    thresh_value = median + threshold_k * MAD_TO_SIGMA * mad

    over = target > thresh_value[:, np.newaxis]
    crossed = over.any(axis=1)
    # argmax on a boolean row gives the first True, or 0 if there is none --
    # meaningless unless `crossed` says otherwise.
    onset_location = over.argmax(axis=1).astype(float)

    n_silent = int((~crossed).sum())
    if n_silent:
        logger.info(
            "%d/%d channels never crossed their onset threshold; they are scored on "
            "energy alone, at worst-case delay", n_silent, target.shape[0],
        )
    return OnsetResult(onset_location, crossed)


def compute_ei_index(target, base, fs, tau_sec=EI_TAU_SEC,
                     energy_window_sec=EI_ENERGY_WINDOW_SEC,
                     threshold_k=ONSET_THRESHOLD_K):
    """Epileptogenicity Index per channel.

    EI = sqrt(energy_coefficient * time_coefficient), after Bartolomei et al.

    Every channel gets a real energy term, including those that never cross the
    onset threshold: they are integrated from the seizure's onset and charged
    the worst-case delay. Leaving them at zero instead tied the whole tail of
    the ranking at EI = 0, which made it carry no information at all.

    Args:
        target: (n_channels, n_target_samples) normalised energy from
                compute_hfer or compute_band_ratio.
        base:   (n_channels, n_base_samples) from the same.
        fs:     sampling rate, Hz.

    Returns:
        (ei, ei_raw, hfer, time_coef), each (n_channels,). `ei` is normalised so
        the highest channel is 1.0 (as published, for the EI > 0.3 threshold);
        `ei_raw` is the same quantity unnormalised, so values stay comparable
        across seizures. Channels with no usable baseline are NaN, not 0 --
        undefined and "quiet" are different answers.
    """
    onset, crossed = determine_threshold_onset(target, base, threshold_k=threshold_k)
    n_channels, n_samples = target.shape
    seizure_location = float(onset[crossed].min()) if crossed.any() else 0.0

    # A dead channel divides by a zero baseline upstream, leaving inf/nan here.
    usable = np.isfinite(target).all(axis=1) & np.isfinite(base).all(axis=1)

    # Each channel's energy is integrated from its OWN onset, per Bartolomei.
    # Anchoring every channel to the earliest onset instead sampled late-
    # recruited channels while they were still quiet.
    n_win = max(1, int(energy_window_sec * fs))
    hfer = np.full(n_channels, np.nan)
    for i in range(n_channels):
        if not usable[i]:
            continue
        start = int(onset[i]) if crossed[i] else int(seizure_location)
        start = min(start, max(0, n_samples - 1))
        hfer[i] = np.sum(target[i, start:start + n_win]) / n_win

    # 1/(delay + tau) in seconds, not 1/ordinal_rank: ranking divided the 10th
    # channel by 10 even when it onset 2 ms after the 1st, so sample-level noise
    # ordering decided the result. A channel with no detected onset is charged a
    # full target window of delay, so it sorts below every real onset but still
    # ranks against its peers on energy.
    max_delay_sec = n_samples / fs
    delay_sec = np.where(crossed, (onset - seizure_location) / fs, max_delay_sec)
    time_coef = 1.0 / (delay_sec + tau_sec)

    with np.errstate(invalid="ignore"):
        ei_raw = np.sqrt(np.clip(hfer, 0.0, None) * time_coef)

    top = np.nanmax(ei_raw) if np.any(np.isfinite(ei_raw)) else 0.0
    ei = ei_raw / top if top > 0 else ei_raw.copy()
    return ei, ei_raw, hfer, time_coef


def ei_diagnostics(onset, crossed, fs, n_samples,
                   edge_sec=DEGENERATE_EDGE_SEC, degenerate_fraction=DEGENERATE_FRACTION):
    """Whether the target window was placed somewhere the time term means anything.

    If a large share of onsets land in the window's first moments, the discharge
    was already under way when the window opened; every delay collapses toward
    zero and EI stops distinguishing early channels from late ones.
    """
    edge_samples = int(edge_sec * fs)
    n_crossed = int(crossed.sum())
    at_edge = int((crossed & (onset <= edge_samples)).sum())
    frac = at_edge / n_crossed if n_crossed else 0.0
    degenerate = bool(n_crossed > 1 and frac > degenerate_fraction)
    if degenerate:
        logger.warning(
            "%d/%d channels that crossed threshold did so within %.2fs of the target "
            "window start: the window opens inside activity that is already under way, "
            "so the EI time term is close to constant and the ranking is effectively "
            "energy-only. Move target_start later, or start it at the marked onset.",
            at_edge, n_crossed, edge_sec,
        )
    return {
        "n_channels": int(len(crossed)),
        "n_crossed": n_crossed,
        "n_never_crossed": int(len(crossed) - n_crossed),
        "frac_onset_at_window_start": round(frac, 4),
        "degenerate_window": degenerate,
        "target_window_sec": round(n_samples / fs, 3),
    }


def save_ei_result(edf_filename, chn_names, ei, ei_raw, hfer, time_coef, diagnostics=None,
                   ei_by_contact=None):
    """Persist EI results next to the edf file, in an EIdets/ folder alongside it --
    mirrors where HI_apis.py saves HFOdets/ next to the inter-ictal edf -- so
    downstream steps (soz.py) can reuse them instead of recomputing EI.

    `chn_names` are the analysed channels, which under a bipolar reference are
    pairs (A1-A2). `ei_by_contact` carries the same result projected back onto
    contacts, since SOZ fusion and the 3D view join on contact names.
    """
    # Keying by name below would keep only the last of each, silently dropping results.
    duplicates = {n for n in chn_names if list(chn_names).count(n) > 1}
    if duplicates:
        raise ValueError(f"EI result: duplicate channel names {sorted(duplicates)}")
    filedir = os.path.dirname(os.path.abspath(edf_filename))
    results_dir = os.path.join(filedir, 'EIdets')
    os.makedirs(results_dir, exist_ok=True)
    file_pre_ext = os.path.basename(edf_filename).split('.')[0]
    out_path = os.path.join(results_dir, file_pre_ext + '_ei.npz')
    by_contact = ei_by_contact if ei_by_contact is not None else dict(zip(chn_names, ei))
    contact_names = list(by_contact)
    # diagnostics as JSON text, not a pickled dict, so loading needs no allow_pickle
    np.savez(out_path, ei=ei, ei_raw=ei_raw, hfer=hfer, time_coef=time_coef,
             chn_names=np.array(chn_names),
             contact_names=np.array(contact_names),
             ei_by_contact=np.array([by_contact[n] for n in contact_names], dtype=float),
             diagnostics=np.array(json.dumps(diagnostics or {})))
    return out_path


def find_saturated_channels(data, frac_threshold=0.01):
    """Indices of channels that spend more than `frac_threshold` of their samples
    pinned at their own extreme value, i.e. clipped by the amplifier.

    Saturated segments are flat-topped, so their windowed energy is meaningless;
    any EI computed over them is measuring the amplifier, not the brain.
    """
    if data.size == 0:
        return []
    lo = data.min(axis=1, keepdims=True)
    hi = data.max(axis=1, keepdims=True)
    span = hi - lo
    tol = span * 1e-4
    at_rail = (np.abs(data - hi) <= tol) | (np.abs(data - lo) <= tol)
    frac = at_rail.mean(axis=1)
    # A flat channel sits at its own min and max simultaneously, which would
    # score 100% "at the rail". It is dead, not clipped -- compute_hfer reports
    # zero-energy channels separately -- so exclude it here.
    frac[span[:, 0] == 0] = 0.0
    return np.flatnonzero(frac > frac_threshold).tolist()


def load_ei_result(path):
    data = np.load(path, allow_pickle=True)
    # archives written before diagnostics/contact projection existed have no such key
    diagnostics = json.loads(str(data['diagnostics'])) if 'diagnostics' in data.files else {}
    chn_names = [str(n) for n in data['chn_names']]
    ei = data['ei'].tolist()
    has_contacts = 'contact_names' in data.files and 'ei_by_contact' in data.files
    return {
        "chn_names": chn_names,
        "ei": ei,
        "ei_raw": data['ei_raw'].tolist(),
        "hfer": data['hfer'].tolist(),
        "time_coef": data['time_coef'].tolist(),
        "diagnostics": diagnostics,
        # Pre-projection archives were all CAR, so their channels are contacts.
        "contact_names": ([str(n) for n in data['contact_names']] if has_contacts else chn_names),
        "ei_by_contact": (data['ei_by_contact'].tolist() if has_contacts else ei),
    }


def compute_ei_pipeline(
    raw_data: np.ndarray,
    fs: float,
    chn_names: list,
    baseline_start: float,
    baseline_end: float,
    target_start: float,
    target_end: float,
    ei_method: str = "band_ratio",
    band_low: float = 1.0,
    band_high: float | None = None,
    mains_freq: float = DEFAULT_MAINS_FREQ,
    low_band: tuple = BARTOLOMEI_LOW_BAND,
    high_band: tuple = BARTOLOMEI_HIGH_BAND,
    threshold_k: float = ONSET_THRESHOLD_K,
    span_start: float = 0.0,
    reference: str = "car",
) -> dict:
    """Run full end-to-end EI calculation on raw array data without DB dependencies.

    Args:
        raw_data: (n_channels, n_samples) un-filtered signal segment.
        fs: Sampling rate in Hz.
        chn_names: List of channel names.
        baseline_start, baseline_end: Baseline window start/end in seconds.
        target_start, target_end: Target window start/end in seconds.
        ei_method: 'band_ratio' (default, Bartolomei et al. 2008) or 'broadband' (HFER).
        band_low, band_high: Pre-filter band limits in Hz. band_high defaults to just
            under this recording's Nyquist. That makes the band sampling-rate dependent,
            which `band_ratio` does not care about (its sub-bands top out at 97 Hz) but
            `broadband` does -- pass an explicit band_high to compare broadband EI across
            recordings with different fs.
        mains_freq: Mains notch frequency in Hz (50 or 60).
        low_band, high_band: Sub-bands for 'band_ratio' method.
        threshold_k: Robust sigma multiplier for onset detection.
        span_start: Offset in seconds of raw_data[0] relative to original recording.
        reference: 'car' (default) or 'bipolar'. Bipolar derives adjacent
            same-shaft pairs, so the returned channel names are pairs (A1-A2).

    Returns:
        Dict with keys:
            ei, ei_raw, hfer, time_coef: ndarrays (n_channels,)
            onset: ndarray (n_channels,) sample indices relative to target window
            crossed: boolean ndarray (n_channels,)
            diagnostics: dict
            ei_scores: dict mapping channel name -> ei score (NaN where undefined)
            ranked_channels: list of channel names sorted by EI score descending
    """
    if ei_method not in ("band_ratio", "broadband"):
        raise ValueError(f"unknown ei_method {ei_method!r}; expected 'band_ratio' or 'broadband'")
    if reference not in ("car", "bipolar"):
        raise ValueError(f"unknown reference {reference!r}; expected 'car' or 'bipolar'")

    # Clipping is a property of the raw contact, so check before any derivation.
    saturated = find_saturated_channels(raw_data)
    saturated_names = [chn_names[i] for i in saturated]
    if saturated:
        logger.warning(
            "%d/%d channel(s) are clipped at the amplifier rail for >1%% of the analysed "
            "window; their energy is flat-topped and their EI is not meaningful: %s",
            len(saturated), len(chn_names), saturated_names,
        )

    pairs = None
    if reference == "bipolar":
        raw_data, chn_names, pairs = apply_bipolar(raw_data, chn_names)
        if pairs is None:
            logger.warning("falling back to CAR: this recording cannot be paired")
            reference = "car"  # diagnostics must record what actually ran
    if band_high is None:
        band_high = max_band_high(fs)
    filtered = filter_for_display(
        raw_data, fs, band_low, band_high, mains_freq=mains_freq,
        reference="none" if reference == "bipolar" else "car",
    )

    def _idx(t):
        return int(round((t - span_start) * fs))

    base_start_i, base_end_i = _idx(baseline_start), _idx(baseline_end)
    target_start_i, target_end_i = _idx(target_start), _idx(target_end)

    baseline_data = filtered[:, base_start_i:base_end_i]
    target_data = filtered[:, target_start_i:target_end_i]

    if ei_method == "band_ratio":
        norm_target, norm_base = compute_band_ratio(
            target_data, baseline_data, fs, low_band=low_band, high_band=high_band
        )
    else:
        norm_target, norm_base = compute_hfer(target_data, baseline_data, fs)

    ei, ei_raw, hfer, time_coef = compute_ei_index(
        norm_target, norm_base, fs, threshold_k=threshold_k
    )

    onset_res = determine_threshold_onset(norm_target, norm_base, threshold_k=threshold_k)
    diag = {
        "method": ei_method,
        "reference": reference,
        "band_low": band_low,
        "band_high": band_high,
        "mains_freq": mains_freq,
        "saturated_channels": saturated_names,
        "dead_channels": [n for n, v in zip(chn_names, np.isfinite(ei_raw)) if not v],
        **ei_diagnostics(onset_res.onset, onset_res.crossed, fs, norm_target.shape[1]),
    }
    if ei_method == "band_ratio":
        diag["er_low_band"] = list(low_band)
        diag["er_high_band"] = list(high_band)

    # NaN stays NaN: a channel with no usable baseline is undefined, not quiet.
    ei_scores = {name: float(ei[i]) for i, name in enumerate(chn_names)}
    ranked_channels = sorted(
        ei_scores, key=lambda k: ei_scores[k] if np.isfinite(ei_scores[k]) else -np.inf,
        reverse=True,
    )

    return {
        "ei": ei,
        "ei_raw": ei_raw,
        "hfer": hfer,
        "time_coef": time_coef,
        "onset": onset_res.onset,
        "crossed": onset_res.crossed,
        "diagnostics": diag,
        "ei_scores": ei_scores,
        "ranked_channels": ranked_channels,
        "pairs": pairs,  # None unless reference='bipolar'
        # Derived names -- pairs under bipolar, contacts otherwise. Callers must
        # use these rather than the names they passed in.
        "chn_names": list(chn_names),
        # Contact-keyed scores for everything downstream that joins on contacts
        # (SOZ fusion, the 3D view). Identity mapping under CAR.
        "ei_by_contact": (project_pairs_to_contacts(ei_scores, pairs)
                          if pairs is not None else dict(ei_scores)),
    }

