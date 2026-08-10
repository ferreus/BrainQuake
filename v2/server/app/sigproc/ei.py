"""Epileptogenicity Index numerics, after Bartolomei et al. 2008.

numpy + scipy only -- importable from a notebook without the server stack.
"""
import logging
import os

import numpy as np
from scipy.signal import convolve2d

logger = logging.getLogger(__name__)

# span integrated from each channel's own onset.
EI_TAU_SEC = 1.0
EI_ENERGY_WINDOW_SEC = 0.25

# Onset threshold: baseline median + k robust sigmas. 1.4826 * MAD estimates
# sigma for Gaussian data, so k is comparable to the old multiplier.
ONSET_THRESHOLD_K = 20.0
MAD_TO_SIGMA = 1.4826

# EI parameters, after Bartolomei et al. 2008. tau keeps the time term finite for
# the first channel and sets how fast later ones decay; the energy window is the
# span integrated from each channel's own onset.
EI_TAU_SEC = 1.0
EI_ENERGY_WINDOW_SEC = 0.25

# Onset threshold: baseline median + k robust sigmas. 1.4826 * MAD estimates
# sigma for Gaussian data, so k is comparable to the old multiplier.
ONSET_THRESHOLD_K = 20.0
MAD_TO_SIGMA = 1.4826

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


def determine_threshold_onset(target, base):
    """Per-channel onset as a sample index into the target window.

    A channel "onsets" at the first target sample whose normalised energy
    exceeds (baseline max + 20 * baseline std). Channels that never cross are
    assigned n_target_samples, i.e. "after the end of the window".

    Args:
        target: (n_channels, n_target_samples) normalised energy from compute_hfer.
        base:   (n_channels, n_base_samples) normalised energy from compute_hfer.

    Returns:
        (n_channels,) float array of sample indices (not seconds).
    """
    # median + k*MAD, not max + k*std: with the max in the threshold, one
    # baseline artefact (an electrode pop, a movement transient) put a channel's
    # bar permanently out of reach and it never registered an onset at all.
    median = np.median(base, axis=1)
    mad = np.median(np.abs(base - median[:, np.newaxis]), axis=1)
    sigma = MAD_TO_SIGMA * mad
    thresh_value = median + ONSET_THRESHOLD_K * sigma

    crossed = target > thresh_value[:, np.newaxis]
    any_crossing = crossed.any(axis=1)
    # argmax on a boolean row gives the first True, or 0 if there is none --
    # hence the explicit where() for channels that never cross.
    first_crossing = crossed.argmax(axis=1).astype(float)
    onset_location = np.where(any_crossing, first_crossing, float(target.shape[1]))

    n_silent = int((~any_crossing).sum())
    if n_silent:
        logger.info(
            "%d/%d channels never crossed their onset threshold; they tie at the end of "
            "the window and their relative EI ranking is arbitrary",
            n_silent, target.shape[0],
        )
    return onset_location


def compute_ei_index(target, base, fs):
    """Epileptogenicity Index per channel.

    EI = sqrt(energy_coefficient * time_coefficient), after Bartolomei et al.

    Args:
        target: (n_channels, n_target_samples) normalised energy from compute_hfer.
        base:   (n_channels, n_base_samples) normalised energy from compute_hfer.
        fs:     sampling rate, Hz.

    Returns:
        (ei, ei_raw, hfer, time_coef), each (n_channels,). `ei` is normalised so
        the highest channel is 1.0 (as published, for the EI > 0.3 threshold);
        `ei_raw` is the same quantity unnormalised, so values stay comparable
        across seizures.
    """
    channel_onset = determine_threshold_onset(target, base)
    seizure_location = np.min(channel_onset)

    # Each channel's energy is integrated from its OWN onset, per Bartolomei.
    # Anchoring every channel to the earliest onset instead sampled late-
    # recruited channels while they were still quiet.
    n_win = int(EI_ENERGY_WINDOW_SEC * fs)
    n_samples = target.shape[1]
    hfer = np.zeros(target.shape[0])
    for i, onset in enumerate(channel_onset):
        start = int(onset)
        if start < n_samples:  # a channel that never onset has no window
            hfer[i] = np.sum(target[i, start:start + n_win]) / n_win

    # 1/(delay + tau) in seconds, not 1/ordinal_rank: ranking divided the 10th
    # channel by 10 even when it onset 2 ms after the 1st, so sample-level noise
    # ordering decided the result.
    delay_sec = (channel_onset - seizure_location) / fs
    time_coef = 1.0 / (delay_sec + EI_TAU_SEC)

    ei_raw = np.sqrt(hfer * time_coef)
    ei_raw = np.nan_to_num(ei_raw, nan=0.0, posinf=0.0, neginf=0.0)

    ei = ei_raw / np.max(ei_raw) if np.max(ei_raw) > 0 else ei_raw.copy()
    return ei, ei_raw, hfer, time_coef


def save_ei_result(edf_filename, chn_names, ei, ei_raw, hfer, time_coef):
    """Persist EI results next to the edf file, in an EIdets/ folder alongside it --
    mirrors where HI_apis.py saves HFOdets/ next to the inter-ictal edf -- so
    downstream steps (soz.py) can reuse them instead of recomputing EI."""
    filedir = os.path.dirname(os.path.abspath(edf_filename))
    results_dir = os.path.join(filedir, 'EIdets')
    os.makedirs(results_dir, exist_ok=True)
    file_pre_ext = os.path.basename(edf_filename).split('.')[0]
    out_path = os.path.join(results_dir, file_pre_ext + '_ei.npz')
    np.savez(out_path, ei=ei, ei_raw=ei_raw, hfer=hfer, time_coef=time_coef,
             chn_names=np.array(chn_names))
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
    return {
        "chn_names": [str(n) for n in data['chn_names']],
        "ei": data['ei'].tolist(),
        "ei_raw": data['ei_raw'].tolist(),
        "hfer": data['hfer'].tolist(),
        "time_coef": data['time_coef'].tolist(),
    }
