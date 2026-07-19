import os
import shutil
import logging
import numpy as np
import mne
from scipy.signal import spectrogram, butter, filtfilt, iirnotch, convolve2d
from scipy.ndimage import gaussian_filter
from sklearn.decomposition import PCA
from sklearn.cluster import KMeans
from sqlalchemy.orm import Session
from app.config import settings
from app.models import Job, Subject, Artifact
from app.services.recon import register_artifact

logger = logging.getLogger(__name__)

# Ported near-verbatim from BrainQuake/client_ictal.py's module-scope compute_*
# functions (already pure numpy/scipy, no Qt/GUI dependency). The interactive
# baseline/target range-select and band-filter text fields become explicit
# request parameters (see routers/ictal.py) instead of mouse clicks.


def compute_hfer(target_data, base_data, fs):
    # High-Frequency Energy Ratio: sliding (0.5s) windowed energy of each
    # channel, normalized by that channel's average energy during the
    # baseline window. Values >> 1 mean the channel got much "louder"
    # (relative to its own quiet-period baseline) during the target window.
    target_sq = target_data ** 2
    base_sq = base_data ** 2
    window = int(fs / 2.0)
    target_energy = convolve2d(target_sq, np.ones((1, window)), 'same')
    base_energy = convolve2d(base_sq, np.ones((1, window)), 'same')
    base_energy_ref = np.sum(base_energy, axis=1) / base_energy.shape[1]
    target_de_matrix = base_energy_ref[:, np.newaxis] * np.ones((1, target_energy.shape[1]))
    base_de_matrix = base_energy_ref[:, np.newaxis] * np.ones((1, base_energy.shape[1]))
    norm_target_energy = target_energy / target_de_matrix.astype(np.float32)
    norm_base_energy = base_energy / base_de_matrix.astype(np.float32)
    return norm_target_energy, norm_base_energy


def determine_threshold_onset(target, base):
    # Per-channel seizure "onset" sample index: the first sample in the
    # target window whose energy exceeds (baseline max + 20 * baseline std).
    base_data = base.copy()
    target_data = target.copy()
    sigma = np.std(base_data, axis=1, ddof=1)
    channel_max_base = np.max(base_data, axis=1)
    thresh_value = channel_max_base + 20 * sigma
    onset_location = np.zeros(shape=(target_data.shape[0],))
    for channel_idx in range(target_data.shape[0]):
        logic_vec = target_data[channel_idx, :] > thresh_value[channel_idx]
        if np.sum(logic_vec) == 0:
            onset_location[channel_idx] = len(logic_vec)
        else:
            onset_location[channel_idx] = np.where(logic_vec != 0)[0][0]
    return onset_location


def compute_ei_index(target, base, fs):
    # Epileptogenicity Index (EI) per channel, following the classic
    # Bartolomei et al. formulation: EI = sqrt(energy_coefficient * time_coefficient).
    ei = np.zeros([1, target.shape[0]])
    hfer = np.zeros([1, target.shape[0]])
    onset_rank = np.zeros([1, target.shape[0]])
    channel_onset = determine_threshold_onset(target, base)
    seizure_location = np.min(channel_onset)
    hfer = np.sum(target[:, int(seizure_location):int(seizure_location + 0.25 * fs)], axis=1) / (fs * 0.25)
    time_rank_tmp = np.argsort(channel_onset)
    onset_rank = np.argsort(time_rank_tmp) + 1
    onset_rank = np.ones((onset_rank.shape[0],)) / np.float32(onset_rank)
    ei = np.sqrt(hfer * onset_rank)
    for i in range(len(ei)):
        if np.isnan(ei[i]) or np.isinf(ei[i]):
            ei[i] = 0
    if np.max(ei) > 0:
        ei = ei / np.max(ei)
    return ei, hfer, onset_rank


def save_ei_result(edf_filename, chn_names, ei, hfer, onset_rank):
    """Persist EI results next to the edf file, in an EIdets/ folder alongside it --
    mirrors where HI_apis.py saves HFOdets/ next to the inter-ictal edf -- so
    downstream steps (soz.py) can reuse them instead of recomputing EI."""
    filedir = os.path.dirname(os.path.abspath(edf_filename))
    results_dir = os.path.join(filedir, 'EIdets')
    os.makedirs(results_dir, exist_ok=True)
    file_pre_ext = os.path.basename(edf_filename).split('.')[0]
    out_path = os.path.join(results_dir, file_pre_ext + '_ei.npz')
    np.savez(out_path, ei=ei, hfer=hfer, onset_rank=onset_rank, chn_names=np.array(chn_names))
    return out_path


def choose_kmeans_k(data, k_range):
    k_sse = []
    for k in k_range:
        tmp_kmeans = KMeans(n_clusters=k)
        tmp_kmeans.fit(data)
        k_sse.append(tmp_kmeans.inertia_)
    k_sse = np.array(k_sse)
    k_sseDiff = -np.diff(k_sse)
    k_sseDiffMean = np.mean(k_sseDiff)
    best_index = np.where(k_sseDiff < k_sseDiffMean)[0][0]
    return k_range[best_index]


def find_ei_cluster_ratio(pei, labels, ei_elec_num=10):
    top_elec_ind = list(np.argsort(-pei)[:ei_elec_num])
    top_elec_labels = list(labels[top_elec_ind])
    top_elec_count = {}
    top_elec_set = set(top_elec_labels)
    for i in top_elec_set:
        top_elec_count[i] = top_elec_labels.count(i)
    cluster_ind1 = [k for k, v in top_elec_count.items() if v > ei_elec_num / 2]
    if len(cluster_ind1):
        return np.array(cluster_ind1)
    else:
        cluster_ind2 = [k for k, v in top_elec_count.items() if v > ei_elec_num / 3]
        if len(cluster_ind2):
            return np.array(cluster_ind2)
        else:
            return None


def pad_zero(data, length):
    data_len = len(data)
    if data_len < length:
        tmp_data = np.zeros(int(length))
        tmp_data[:data_len] = data
        return tmp_data
    return data


def cal_zscore(data):
    dmean = np.mean(data, axis=1)
    dstd = np.std(data, axis=1)
    norm_data = (data - dmean[:, None]) / dstd[:, None]
    return norm_data


def cal_specs_matrix(raw, sfreq, method='STFT'):
    win_len = 0.5
    overlap = 0.8
    freq_range = 300
    half_width = win_len * sfreq
    ch_num = raw.shape[0]
    if method == 'STFT':
        for i in range(ch_num):
            time_signal = raw[i, :].ravel()
            time_signal = pad_zero(time_signal, 2 * half_width)
            f, t, hfo_spec = spectrogram(time_signal, fs=int(sfreq), nperseg=int(half_width),
                                          noverlap=int(overlap * half_width),
                                          nfft=1024, mode='magnitude')
            hfo_new = 20 * np.log10(hfo_spec + 1e-10)
            hfo_new = gaussian_filter(hfo_new, sigma=2)
            freq_nums = int(len(f) * freq_range / f.max())
            hfo_new = hfo_new[:freq_nums, :]
            tmp_specs = np.reshape(hfo_new, (-1,))
            if i == 0:
                chan_specs = tmp_specs
            else:
                chan_specs = np.row_stack((chan_specs, tmp_specs))
    f_cut = f[:freq_range]
    return chan_specs, hfo_new.shape, t, f_cut


def norm_specs(specs):
    specs_mean = specs - specs.mean(axis=0)
    specs_norm = specs_mean / specs_mean.std(axis=0)
    return specs_norm


def compute_full_band(raw_data, sfreq, ei):
    # Cluster channels by spectral shape to reveal electrodes that share the
    # seizure-onset zone's spectral signature. Not wired to a router endpoint yet
    # (ported for parity with the legacy module; no REST consumer defined in
    # PLAN.md's Phase (b) checklist).
    ei_elec_num = 10
    raw_specs, spec_shape, t, f = cal_specs_matrix(raw_data, sfreq, 'STFT')
    raw_specs_norm = norm_specs(raw_specs)
    proj_pca = PCA(n_components=10)
    spec_pca = proj_pca.fit_transform(raw_specs_norm)
    k_num = choose_kmeans_k(spec_pca, range(2, 8))
    tmp_kmeans = KMeans(n_clusters=k_num)
    tmp_kmeans.fit(spec_pca)
    pre_labels = tmp_kmeans.labels_
    cluster_ind_ratio = find_ei_cluster_ratio(ei, pre_labels)
    chosen_cluster_ind = np.where(pre_labels == cluster_ind_ratio)[0]
    return spec_pca, pre_labels, chosen_cluster_ind


def _ensure_edf_copy(subject: Subject, artifact: Artifact):
    """Mirrors client_ictal.py's dialog_inputedfdata(): copy the uploaded edf into
    <subject_dir>/edf/ so results land next to it under edf/EIdets/, matching the
    convention soz.py's fusion step expects."""
    src_path = os.path.join(settings.DATA_ROOT, artifact.rel_path)
    edf_dir = os.path.join(settings.SUBJECTS_DIR, subject.name, "edf")
    os.makedirs(edf_dir, exist_ok=True)
    dest_path = os.path.join(edf_dir, os.path.basename(src_path))
    if not os.path.exists(dest_path):
        shutil.copy2(src_path, dest_path)
    return dest_path


def _filter_signal(data, fs, band_low, band_high):
    """Port of IctalModule.filter_data(): common-average reference, then a
    50/100/150Hz notch, then a user-specified bandpass, all zero-phase."""
    data = data - np.mean(data, axis=0)
    notch_freqs = np.arange(50, 151, 50)
    for nf in notch_freqs:
        tb, ta = iirnotch(nf / (fs / 2), 30)
        data = filtfilt(tb, ta, data, axis=-1)
    nyq = fs / 2
    b, a = butter(5, np.array([band_low / nyq, band_high / nyq]), btype='bandpass')
    data = filtfilt(b, a, data)
    return data


def run_ei_compute_job(db: Session, job: Job, log_file):
    subject = db.query(Subject).filter(Subject.id == job.subject_id).first()
    if not subject:
        raise ValueError("Subject not found")

    params = job.params_json or {}
    artifact = db.query(Artifact).filter(
        Artifact.id == params["edf_artifact_id"], Artifact.subject_id == subject.id
    ).first()
    if not artifact:
        raise FileNotFoundError(f"edf artifact {params.get('edf_artifact_id')} not found for this subject")

    band_low = float(params.get("band_low", 1.0))
    band_high = float(params.get("band_high", 500.0))
    baseline_start = float(params["baseline_start"])
    baseline_end = float(params["baseline_end"])
    target_start = float(params["target_start"])
    target_end = float(params["target_end"])

    job.progress_pct = 10.0
    job.progress_message = "Loading edf and applying notch + bandpass filter"
    db.commit()

    edf_path = _ensure_edf_copy(subject, artifact)
    edf_data = mne.io.read_raw_edf(edf_path, preload=True, stim_channel=None)
    fs = edf_data.info['sfreq']
    chn_names = edf_data.ch_names
    raw_data, _ = edf_data[:]

    filtered = _filter_signal(raw_data, fs, band_low, band_high)

    base_start_i = int(baseline_start * fs)
    base_end_i = int(baseline_end * fs)
    target_start_i = int(target_start * fs)
    target_end_i = int(target_end * fs)

    job.progress_pct = 60.0
    job.progress_message = "Computing HFER + EI index"
    db.commit()

    baseline_data = filtered[:, base_start_i:base_end_i]
    target_data = filtered[:, target_start_i:target_end_i]
    norm_target, norm_base = compute_hfer(target_data, baseline_data, fs)
    ei, hfer, onset_rank = compute_ei_index(norm_target, norm_base, fs)

    ei_result_path = save_ei_result(edf_path, chn_names, ei, hfer, onset_rank)
    register_artifact(db, subject.id, job.id, "ei_npz", ei_result_path)

    job.progress_pct = 95.0
    job.progress_message = "EI computation complete"
    db.commit()


def load_ei_result(path):
    data = np.load(path, allow_pickle=True)
    return {
        "chn_names": [str(n) for n in data['chn_names']],
        "ei": data['ei'].tolist(),
        "hfer": data['hfer'].tolist(),
        "onset_rank": data['onset_rank'].tolist(),
    }
