import logging

import numpy as np
from scipy.signal import butter, filtfilt, iirnotch

logger = logging.getLogger(__name__)

# FIXME(correctness): 50 Hz is inherited from the legacy app's hardcoded value.
# The justification used to be bit-identical reproduction of the S1 golden
# output, which is no longer a goal (docs/cleanup-plan.md). What remains is a
# default that is simply wrong for this project's own data -- Bella's recordings
# are 60 Hz -- and getting it wrong is silent: it notches 50/100/150 Hz of clean
# signal and leaves the real 60/120/180 Hz interference in place. This should
# become a required parameter, or be read from the recording's metadata, rather
# than defaulting to the value that is wrong here.
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
    # FIXME(correctness): this common-average reference averages over EVERY
    # channel in the file, including EKG/REF/DC auxiliary traces, so non-brain
    # signal is subtracted into every SEEG channel. See find_non_seeg_channels()
    # in services/ictal.py, which reports them but does not exclude them.
    data = data - np.mean(data, axis=0)
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
