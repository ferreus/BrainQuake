import numpy as np
from scipy.signal import butter, filtfilt, iirnotch


def filter_for_display(data, fs, band_low, band_high):
    """Common-average reference, then a 50/100/150Hz notch, then a
    user-specified zero-phase Butterworth bandpass -- ported from
    BrainQuake/client_ictal.py's IctalModule.filter_data(). This is the
    "trace display" filter both the ictal and interictal Qt viewers apply
    before showing/computing on a signal; the new windowed EDF endpoint
    (services/edf.py) reuses it too, so there aren't three copies.

    Distinct from interictal.py's own notch_filt/band_filt (used inside
    HI_preprocess_file for HFO envelope extraction, not display) -- those
    stay separate since they're numerically different (no CAR, different
    filter order) and untouched here to avoid drifting already-verified
    HFO output.
    """
    data = data - np.mean(data, axis=0)
    notch_freqs = np.arange(50, 151, 50)
    for nf in notch_freqs:
        tb, ta = iirnotch(nf / (fs / 2), 30)
        data = filtfilt(tb, ta, data, axis=-1)
    nyq = fs / 2
    b, a = butter(5, np.array([band_low / nyq, band_high / nyq]), btype="bandpass")
    data = filtfilt(b, a, data)
    return data
