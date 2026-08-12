import logging

import numpy as np
from scipy.signal import butter, filtfilt, iirnotch, sosfiltfilt

logger = logging.getLogger(__name__)

# TODO(cleanup): 50 Hz is inherited from the legacy app. Callers that matter
# set it explicitly (the web UI exposes it, and Bella's 60 Hz recordings are run
# at 60), so this is a footgun rather than a live bug: leaving it at 50 on a
# 60 Hz recording notches clean signal, leaves the real interference, and warns
# about none of it. Worth making required, or reading from the recording.
DEFAULT_MAINS_FREQ = 50.0

# Fraction of Nyquist that band_high is clamped to. scipy's butter() requires
# 0 < Wn < 1, so band_high == fs/2 raises ValueError rather than meaning
# "everything" -- which is what a caller passing the old 500.0 default against
# a 1 kHz recording intends.
_NYQ_MARGIN = 0.99


def mains_harmonics(mains_freq, fs, up_to=None):
    """Harmonics of the mains frequency that a notch can actually remove.

    Anything at or above Nyquist is unrepresentable, and iirnotch() needs a
    normalized frequency strictly inside (0, 1), so the series stops short of
    it. `up_to` further limits the series to the top of the band of interest.
    """
    nyq = fs / 2.0
    limit = nyq * _NYQ_MARGIN
    if up_to is not None:
        limit = min(limit, up_to)
    if mains_freq <= 0:
        return np.array([])
    return np.arange(mains_freq, limit, mains_freq)


def clamp_band(band_low, band_high, fs, context=""):
    """Clamp a bandpass to something butter() will accept, loudly."""
    nyq = fs / 2.0
    high = float(band_high)
    low = float(band_low)
    if high >= nyq:
        clamped = nyq * _NYQ_MARGIN
        logger.warning(
            "%sband_high=%.1f Hz is at or above Nyquist (%.1f Hz) for fs=%.1f Hz; "
            "clamping to %.1f Hz", f"{context}: " if context else "",
            high, nyq, fs, clamped,
        )
        high = clamped
    if low <= 0:
        raise ValueError(f"band_low must be > 0, got {low}")
    if low >= high:
        raise ValueError(f"band_low ({low}) must be below band_high ({high})")
    return low, high


def bandpass(data, fs, band_low, band_high, order=5, context=""):
    """Zero-phase Butterworth bandpass only -- no CAR, no notch.

    For splitting an already-referenced, already-notched signal into sub-bands
    (ei.compute_band_ratio); filter_for_display would re-reference and re-notch.

    Second-order sections, not (b, a): Bartolomei's theta+alpha band is
    3.5-12.4 Hz, which at 1 kHz is a normalized 0.007-0.025. A 10th-order
    transfer function over a band that narrow is ill-conditioned enough to
    diverge -- it returned ~1e80 energies before this used sosfiltfilt.
    """
    band_low, band_high = clamp_band(band_low, band_high, fs, context or "bandpass")
    nyq = fs / 2.0
    sos = butter(order, np.array([band_low / nyq, band_high / nyq]),
                 btype="bandpass", output="sos")
    return sosfiltfilt(sos, data)


def filter_for_display(data, fs, band_low, band_high, mains_freq=DEFAULT_MAINS_FREQ):
    """Common-average reference, then a mains-harmonic notch, then a
    user-specified zero-phase Butterworth bandpass -- ported from
    client_ictal.py's IctalModule.filter_data() (git tag legacy-final). This is the
    "trace display" filter both the ictal and interictal Qt viewers apply
    before showing/computing on a signal; the new windowed EDF endpoint
    (services/edf.py) reuses it too, so there aren't three copies.

    `mains_freq` must match the grid the data was recorded on. The legacy code
    hardcoded 50/100/150 Hz; on a 60 Hz recording that notches clean signal and
    leaves the actual interference untouched.

    Distinct from interictal.py's own notch_filt/band_filt (used inside
    HI_preprocess_file for HFO envelope extraction, not display) -- those
    stay separate since they're numerically different (no CAR, different
    filter order) and untouched here to avoid drifting already-verified
    HFO output.
    """
    band_low, band_high = clamp_band(band_low, band_high, fs, "filter_for_display")
    # Common-average reference over whatever the caller passed. The numeric
    # callers pass contacts only (edf_common.load_seeg); the viewer passes what
    # the user selected. Skipped below two channels: the average of one channel
    # is that channel, so subtracting it returns exactly zero -- which is what
    # the web client's single-channel drill-down was plotting.
    if np.ndim(data) > 1 and np.shape(data)[0] > 1:
        data = data - np.mean(data, axis=0)
    else:
        logger.info("single channel: returning it unreferenced (a common average of one is itself)")
    # NOTE(v1-quirk): the legacy app notched 50/100/150 Hz only, and this keeps
    # that reach (3 harmonics) rather than sweeping to Nyquist. Harmonics above
    # the 3rd are left in the signal; whether that matters depends on how much
    # mains interference the recording actually carries.
    for nf in mains_harmonics(mains_freq, fs, up_to=mains_freq * 3.5):
        tb, ta = iirnotch(nf / (fs / 2), 30)
        data = filtfilt(tb, ta, data, axis=-1)
    nyq = fs / 2
    b, a = butter(5, np.array([band_low / nyq, band_high / nyq]), btype="bandpass")
    data = filtfilt(b, a, data)
    return data
