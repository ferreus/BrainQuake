import os
import gc
import shutil
import logging
from functools import reduce
import numpy as np
import mne
from scipy.signal import butter, filtfilt, iirnotch, hilbert
from scipy import fftpack
from sqlalchemy.orm import Session
from app.config import settings
from app.models import Job, Subject, Artifact
from app.services.recon import register_artifact

logger = logging.getLogger(__name__)

# Ported from BrainQuake/utils/interictal_utils.py (pure signal-processing helpers)
# and BrainQuake/utils/HI_apis.py (the two entry points HI_preprocess_file/
# HI_count_highEvents_chns), merged into one service module per PLAN.md's Phase (b)
# checklist. Behavior is unchanged except progress reporting, which now updates the
# Job row instead of emitting a Qt signal.

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
        filter_bank = list(zip(filter_bank[:-1], filter_bank[1:]))
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
        further_index = np.where((np.array(tmp_highEnveLong) > min_last * 1e-3))[0]
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


def find_high_enveTimes_dir(enve_dir, segment_time=200, rel_thresh=3.0, abs_thresh=3., min_gap=20, min_last=50):
    whole_enveTimes = []
    seg_chNames = None
    for filename in os.listdir(enve_dir):
        if filename.split('_')[0] == 'rawEnve':
            tmp_filename = os.path.join(enve_dir, filename)
            tmp_enveResults = np.load(tmp_filename)
            seg_enve = tmp_enveResults['rawEnve']
            seg_chNames = tmp_enveResults['valid_chns']
            seg_fs = tmp_enveResults['fs']
            seg_startTime = (int(filename.split('.')[0].split('_')[1]) - 1) * segment_time
            seg_highTimes = find_high_enveTimes(seg_enve, seg_chNames, seg_fs, rel_thresh=rel_thresh, abs_thresh=abs_thresh,
                                                 min_gap=min_gap, min_last=min_last, start_time=seg_startTime)
            whole_enveTimes.append(seg_highTimes)

    whole_enveTimes_cat = reduce(cat_chns_times, whole_enveTimes)
    whole_enveTimes_cat = [sorted(x, key=lambda x: x[0]) for x in whole_enveTimes_cat]

    chns_highEnve_cout = np.array([len(x) for x in whole_enveTimes_cat])

    return whole_enveTimes_cat, chns_highEnve_cout, seg_chNames


def HI_preprocess_file(filename, remain_chns, highpass_freqband, progress_cb):
    filedir = os.path.dirname(os.path.abspath(filename))
    fileBaseName = os.path.basename(filename)
    filePreExt = fileBaseName.split('.')[0]
    fileResultsDir = os.path.join(filedir, 'HFOdets', filePreExt)
    if os.path.exists(fileResultsDir):
        shutil.rmtree(fileResultsDir)
    os.makedirs(fileResultsDir)

    edf_data = mne.io.read_raw_edf(filename, preload=False, stim_channel=None)
    fs = edf_data.info['sfreq']

    valid_chns_index = np.arange(len(edf_data.ch_names))[np.array([x in remain_chns for x in edf_data.ch_names])]
    valid_chns = np.array(edf_data.ch_names)[valid_chns_index]
    valid_chns_st = valid_chns

    time_inter = np.arange(0, edf_data.times[-1], segment_time)
    time_inter = np.append(time_inter, edf_data.times[-1])
    time_ranges = np.array(list(zip(time_inter[:-1], time_inter[1:])))

    for id, tr in enumerate(time_ranges):
        logger.info('part {}/{}'.format(id + 1, time_ranges.shape[0]))
        start, end = edf_data.time_as_index(tr)
        batch_data = edf_data[valid_chns_index, start:end][0]
        batch_data = batch_data - batch_data.mean(axis=0)
        batch_data = notch_filt(batch_data, fs, np.arange(50, highpass_freqband[1] + 10, 50))
        batch_enve = return_hil_enve_norm(batch_data, fs, highpass_freqband)
        batch_t = np.arange(batch_enve.shape[1]) / fs + tr[0]

        np.savez(os.path.join(fileResultsDir, 'rawEnve_{}.npz'.format(id + 1)), rawEnve=batch_enve, rawTimes=batch_t,
                 valid_chns_index=valid_chns_index, valid_chns=valid_chns_st, fs=fs)

        del batch_data, batch_enve
        gc.collect()
        progress_cb(int(90 * (id + 1) / time_ranges.shape[0]))


def HI_count_highEvents_chns(filename, rel_thresh, abs_thresh, min_gap, min_last):
    filedir = os.path.dirname(os.path.abspath(filename))
    fileBaseName = os.path.basename(filename)
    filePreExt = fileBaseName.split('.')[0]
    hfoDetsDir = os.path.join(filedir, 'HFOdets')
    fileResultsDir = os.path.join(hfoDetsDir, filePreExt)

    file_highEnve_times, file_highEnve_chnsCount, file_chnsNames = find_high_enveTimes_dir(
        fileResultsDir, segment_time, rel_thresh=rel_thresh, abs_thresh=abs_thresh, min_gap=min_gap, min_last=min_last)

    out_path = os.path.join(hfoDetsDir, filePreExt + '_events.npz')
    np.savez(out_path, file_highEventsCount=file_highEnve_chnsCount, file_chnsNames=file_chnsNames,
              file_highEvents_times=np.array(file_highEnve_times, dtype=object))
    shutil.rmtree(fileResultsDir)

    return out_path, [file_highEnve_chnsCount, file_chnsNames, file_highEnve_times]


def _ensure_edf_copy(subject: Subject, artifact: Artifact):
    """Mirrors client_inter.py's edf-import: copy the uploaded edf into
    <subject_dir>/edf/ so HFOdets/ lands next to it, matching soz.py's expectations."""
    src_path = os.path.join(settings.DATA_ROOT, artifact.rel_path)
    edf_dir = os.path.join(settings.SUBJECTS_DIR, subject.name, "edf")
    os.makedirs(edf_dir, exist_ok=True)
    dest_path = os.path.join(edf_dir, os.path.basename(src_path))
    if not os.path.exists(dest_path):
        shutil.copy2(src_path, dest_path)
    return dest_path


def run_hfo_compute_job(db: Session, job: Job, log_file):
    subject = db.query(Subject).filter(Subject.id == job.subject_id).first()
    if not subject:
        raise ValueError("Subject not found")

    params = job.params_json or {}
    artifact = db.query(Artifact).filter(
        Artifact.id == params["edf_artifact_id"], Artifact.subject_id == subject.id
    ).first()
    if not artifact:
        raise FileNotFoundError(f"edf artifact {params.get('edf_artifact_id')} not found for this subject")

    band_low = float(params.get("band_low", 80.0))
    band_high = float(params.get("band_high", 250.0))
    rel_thresh = float(params.get("rel_thresh", 2.0))
    abs_thresh = float(params.get("abs_thresh", 2.0))
    min_gap = float(params.get("min_gap", 20))
    min_last = float(params.get("min_last", 50))

    job.progress_pct = 5.0
    job.progress_message = "Loading edf"
    db.commit()

    edf_path = _ensure_edf_copy(subject, artifact)
    remain_chns = params.get("remain_chns")
    if not remain_chns:
        header = mne.io.read_raw_edf(edf_path, preload=False, stim_channel=None)
        remain_chns = header.ch_names

    def progress_cb(pct):
        # HI_preprocess_file drives 0-90%, event detection below finishes the rest.
        job.progress_pct = min(90.0, float(pct))
        job.progress_message = f"Computing envelope ({job.progress_pct:.0f}%)"
        db.commit()

    HI_preprocess_file(edf_path, remain_chns, [band_low, band_high], progress_cb)

    job.progress_pct = 92.0
    job.progress_message = "Detecting high-envelope events"
    db.commit()

    events_path, _ = HI_count_highEvents_chns(edf_path, rel_thresh, abs_thresh, min_gap, min_last)
    register_artifact(db, subject.id, job.id, "hfo_npz", events_path)

    job.progress_pct = 98.0
    job.progress_message = "HFO computation complete"
    db.commit()


def load_hfo_result(path):
    data = np.load(path, allow_pickle=True)
    return {
        "chn_names": [str(n) for n in data['file_chnsNames']],
        "event_counts": data['file_highEventsCount'].tolist(),
        "event_times": [list(x) for x in data['file_highEvents_times']],
    }
