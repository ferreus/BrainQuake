"""Interictal High-Frequency Oscillation (HFO) detector numerics and signal processing.

Pure numpy + scipy + MNE algorithms -- importable without database/FastAPI dependencies.
"""
import gc
import logging
import os
import shutil
from functools import reduce

import mne
import numpy as np
from scipy import fftpack
from scipy.signal import butter, filtfilt, iirnotch, hilbert

from app.sigproc.filters import DEFAULT_MAINS_FREQ, clamp_band, mains_harmonics

logger = logging.getLogger(__name__)

segment_time = 50


def notch_filt(data, fs, freqs):
    nyq = fs / 2
    Q = 30
    tmp_data = data.copy()
    for f in freqs:
        tmp_w = f / nyq
        b, a = iirnotch(tmp_w, Q)
        tmp_data = filtfilt(b, a, tmp_data, axis=-1)
    return tmp_data


def band_filt(data, fs, freqband):
    nyq = fs / 2
    b, a = butter(3, [freqband[0] / nyq, freqband[1] / nyq], btype='bandpass')
    return filtfilt(b, a, data, axis=-1)


def hilbert3(x):
    return hilbert(x, N=fftpack.next_fast_len(x.shape[-1]), axis=-1)[..., :x.shape[-1]]


def return_hil_enve(data, fs, freqband):
    filt_data = band_filt(data, fs, freqband)
    return np.abs(hilbert3(filt_data))


def return_hil_enve_norm(data, fs, freqband):
    if freqband[1] - freqband[0] <= 20:
        return return_hil_enve(data, fs, freqband)
    else:
        filter_bank = np.arange(freqband[0], freqband[1], 20)
        filter_bank = np.append(filter_bank, freqband[1])
        filter_bank = list(zip(filter_bank[:-1], filter_bank[1:], strict=True))
        multi_band_enve = []
        for freq in filter_bank:
            tmp_enve = return_hil_enve(data, fs, freq)
            multi_band_enve.append(tmp_enve)
        return np.sum(multi_band_enve, axis=0)


def return_timeRanges(onOff_array, fs, start_time=0):
    times = np.arange(len(onOff_array)) / fs + start_time
    start_index = np.where(np.diff(onOff_array) == 1)[0] + 1
    end_index = np.where(np.diff(onOff_array) == -1)[0]
    if onOff_array[0] == 1:
        start_index = np.append(start_index[::-1], [0])[::-1]
    if onOff_array[-1] == 1:
        end_index = np.append(end_index, [len(onOff_array) - 1])

    if len(start_index) == 0 or len(end_index) == 0:
        return np.array([])
    range_times = np.vstack([times[start_index], times[end_index]]).T
    return range_times


def merge_timeRanges(range_times, min_gap=10):
    merged_times = []
    range_times = range_times.tolist()
    if len(range_times) == 0:
        return []
    merged_times.append(range_times[0])
    for i in range(1, len(range_times)):
        if range_times[i][0] - merged_times[-1][1] < min_gap * 1e-3:
            merged_times[-1][1] = range_times[i][1]
        else:
            merged_times.append(range_times[i])
    return merged_times


def find_high_enveTimes(raw_enve, chns_names, fs, rel_thresh=3., abs_thresh=3., min_gap=20, min_last=50, start_time=0):
    whole_data_median = np.median(raw_enve)
    high_times = []
    for chi in range(len(chns_names)):
        tmp_enve = raw_enve[chi]
        tmp_median = np.median(tmp_enve)
        tmp_highTime = ((tmp_enve > rel_thresh * tmp_median) & (tmp_enve > abs_thresh * whole_data_median)).astype('int')
        tmp_highTime = return_timeRanges(tmp_highTime, fs, start_time)
        tmp_highTime = merge_timeRanges(tmp_highTime, min_gap)
        tmp_highEnveLong = [x[1] - x[0] for x in tmp_highTime]
        further_index = np.where(np.array(tmp_highEnveLong) > min_last * 1e-3)[0]
        if len(further_index) == 0:
            high_times.append([])
        else:
            tmp_highTime = np.array(tmp_highTime)[further_index]
            high_times.append(tmp_highTime.tolist())

    return high_times


def cat_chns_times(times_1, times_2):
    cat_times = []
    for chi in range(len(times_1)):
        cat_times.append(times_1[chi] + times_2[chi])
    return cat_times


def find_high_enveTimes_dir(enve_dir, rel_thresh=3.0, abs_thresh=3., min_gap=20, min_last=50):
    whole_enveTimes = []
    seg_chNames = None
    for filename in os.listdir(enve_dir):
        if filename.split('_')[0] == 'rawEnve':
            tmp_filename = os.path.join(enve_dir, filename)
            tmp_enveResults = np.load(tmp_filename)
            seg_enve = tmp_enveResults['rawEnve']
            seg_chNames = tmp_enveResults['valid_chns']
            seg_fs = tmp_enveResults['fs']
            seg_startTime = float(tmp_enveResults['rawTimes'][0])
            seg_highTimes = find_high_enveTimes(seg_enve, seg_chNames, seg_fs, rel_thresh=rel_thresh, abs_thresh=abs_thresh,
                                                 min_gap=min_gap, min_last=min_last, start_time=seg_startTime)
            whole_enveTimes.append(seg_highTimes)

    whole_enveTimes_cat = reduce(cat_chns_times, whole_enveTimes)
    whole_enveTimes_cat = [sorted(x, key=lambda x: x[0]) for x in whole_enveTimes_cat]

    chns_highEnve_cout = np.array([len(x) for x in whole_enveTimes_cat])

    return whole_enveTimes_cat, chns_highEnve_cout, seg_chNames


def HI_preprocess_file(filename, remain_chns, highpass_freqband, progress_cb=None,
                       mains_freq=DEFAULT_MAINS_FREQ, start_time=None, end_time=None):
    filedir = os.path.dirname(os.path.abspath(filename))
    fileBaseName = os.path.basename(filename)
    filePreExt = fileBaseName.split('.')[0]
    fileResultsDir = os.path.join(filedir, 'HFOdets', filePreExt)
    if os.path.exists(fileResultsDir):
        shutil.rmtree(fileResultsDir)
    os.makedirs(fileResultsDir)

    edf_data = mne.io.read_raw_edf(filename, preload=False, stim_channel=None, verbose=False)
    fs = edf_data.info['sfreq']
    highpass_freqband = list(clamp_band(highpass_freqband[0], highpass_freqband[1],
                                        fs, "HI_preprocess_file"))

    valid_chns_index = np.arange(len(edf_data.ch_names))[np.array([x in remain_chns for x in edf_data.ch_names])]
    valid_chns = np.array(edf_data.ch_names)[valid_chns_index]
    valid_chns_st = valid_chns

    t_end_file = float(edf_data.times[-1])
    t0 = 0.0 if start_time is None else max(0.0, float(start_time))
    t1 = t_end_file if end_time is None else min(t_end_file, float(end_time))
    if t1 - t0 < 1.0 / fs:
        raise ValueError(
            f"analysis window {t0:.3f}-{t1:.3f}s is empty "
            f"(recording is 0-{t_end_file:.3f}s)"
        )
    logger.info('analysing %.3f-%.3fs of %.3fs, mains %.1f Hz', t0, t1, t_end_file, mains_freq)

    time_inter = np.arange(t0, t1, segment_time)
    time_inter = np.append(time_inter, t1)
    time_ranges = np.array(list(zip(time_inter[:-1], time_inter[1:], strict=True)))

    for seg_i, tr in enumerate(time_ranges):
        logger.info(f'part {seg_i + 1}/{time_ranges.shape[0]}')
        start, end = edf_data.time_as_index(tr)
        batch_data = edf_data[valid_chns_index, start:end][0]
        batch_data = batch_data - batch_data.mean(axis=0)
        batch_data = notch_filt(
            batch_data, fs,
            mains_harmonics(mains_freq, fs, up_to=highpass_freqband[1] + mains_freq * 0.2),
        )
        batch_enve = return_hil_enve_norm(batch_data, fs, highpass_freqband)
        batch_t = np.arange(batch_enve.shape[1]) / fs + tr[0]

        np.savez(os.path.join(fileResultsDir, f'rawEnve_{seg_i + 1}.npz'), rawEnve=batch_enve, rawTimes=batch_t,
                 valid_chns_index=valid_chns_index, valid_chns=valid_chns_st, fs=fs)

        del batch_data, batch_enve
        gc.collect()
        if progress_cb:
            progress_cb(int(90 * (seg_i + 1) / time_ranges.shape[0]))

    try:
        edf_data.close()
    except Exception:
        pass


def HI_count_highEvents_chns(filename, rel_thresh, abs_thresh, min_gap, min_last):
    filedir = os.path.dirname(os.path.abspath(filename))
    fileBaseName = os.path.basename(filename)
    filePreExt = fileBaseName.split('.')[0]
    hfoDetsDir = os.path.join(filedir, 'HFOdets')
    fileResultsDir = os.path.join(hfoDetsDir, filePreExt)

    file_highEnve_times, file_highEnve_chnsCount, file_chnsNames = find_high_enveTimes_dir(
        fileResultsDir, rel_thresh=rel_thresh, abs_thresh=abs_thresh, min_gap=min_gap, min_last=min_last)

    out_path = os.path.join(hfoDetsDir, filePreExt + '_events.npz')
    np.savez(out_path, file_highEventsCount=file_highEnve_chnsCount, file_chnsNames=file_chnsNames,
              file_highEvents_times=np.array(file_highEnve_times, dtype=object))
    shutil.rmtree(fileResultsDir)

    return out_path, [file_highEnve_chnsCount, file_chnsNames, file_highEnve_times]


def compute_hfo_pipeline(
    raw_data: np.ndarray,
    fs: float,
    chn_names: list,
    band_low: float = 80.0,
    band_high: float = 250.0,
    rel_thresh: float = 2.0,
    abs_thresh: float = 2.0,
    min_gap: float = 20.0,
    min_last: float = 50.0,
    mains_freq: float = DEFAULT_MAINS_FREQ,
) -> dict:
    """Run HFO envelope detection and event counting directly on memory arrays.

    Args:
        raw_data: (n_channels, n_samples) un-filtered signal data.
        fs: Sampling rate in Hz.
        chn_names: List of channel names corresponding to raw_data rows.
        band_low, band_high: HFO passband limits (e.g. 80-250 Hz for ripple).
        rel_thresh: Relative envelope median multiplier threshold.
        abs_thresh: Absolute whole-dataset envelope median multiplier threshold.
        min_gap: Minimum gap in ms between adjacent event candidate segments.
        min_last: Minimum duration in ms for an event to be retained.
        mains_freq: Mains notch frequency (50 or 60 Hz).

    Returns:
        Dict with keys:
            event_counts: dict mapping channel_name -> event_count (int)
            event_times: dict mapping channel_name -> list of [start_sec, end_sec]
            ranked_channels: list of channel names sorted by HFO count descending
    """
    freqband = list(clamp_band(band_low, band_high, fs, "compute_hfo_pipeline"))
    # Common-average reference subtraction
    data_car = raw_data - np.mean(raw_data, axis=0, keepdims=True)
    # Notch filter mains harmonics
    notch_freqs = mains_harmonics(mains_freq, fs, up_to=freqband[1] + mains_freq * 0.2)
    data_notched = notch_filt(data_car, fs, notch_freqs)
    # Envelope extraction
    enve = return_hil_enve_norm(data_notched, fs, freqband)
    # Find high envelope event windows
    high_times = find_high_enveTimes(
        enve, chn_names, fs,
        rel_thresh=rel_thresh, abs_thresh=abs_thresh,
        min_gap=min_gap, min_last=min_last, start_time=0.0
    )

    counts = [len(t) for t in high_times]
    event_counts = dict(zip(chn_names, counts))
    event_times = dict(zip(chn_names, high_times))
    ranked_channels = sorted(event_counts.keys(), key=lambda k: event_counts[k], reverse=True)

    return {
        "event_counts": event_counts,
        "event_times": event_times,
        "ranked_channels": ranked_channels,
        "raw_envelope": enve,
    }


def load_hfo_result(path):
    data = np.load(path, allow_pickle=True)
    return {
        "chn_names": [str(n) for n in data['file_chnsNames']],
        "event_counts": data['file_highEventsCount'].tolist(),
        # Plain floats, not the object-dtype arrays np.savez round-trips: FastAPI's
        # encoder cannot serialise those and the result endpoint 500s on every
        # real result (the web panel then just says "not computed yet").
        "event_times": [
            [[float(a), float(b)] for a, b in np.asarray(x, dtype=float).reshape(-1, 2)]
            for x in data['file_highEvents_times']
        ],
    }
