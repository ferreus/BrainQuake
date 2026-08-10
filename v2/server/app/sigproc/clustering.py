"""Spectral clustering helpers for the full-band EI view. Needs sklearn."""
import logging

import numpy as np
from scipy.ndimage import gaussian_filter
from scipy.signal import spectrogram
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA

logger = logging.getLogger(__name__)

# KMeans initialisation is random; a fixed seed keeps repeated runs on the same
# recording reproducible.
RANDOM_STATE = 0


def choose_kmeans_k(data, k_range, random_state=RANDOM_STATE):
    """Elbow heuristic: the first k whose marginal SSE improvement falls below
    the mean improvement across `k_range`."""
    k_sse = []
    for k in k_range:
        tmp_kmeans = KMeans(n_clusters=k, n_init=10, random_state=random_state)
        tmp_kmeans.fit(data)
        k_sse.append(tmp_kmeans.inertia_)
    k_sse = np.array(k_sse)
    k_sseDiff = -np.diff(k_sse)
    k_sseDiffMean = np.mean(k_sseDiff)
    below = np.flatnonzero(k_sseDiff < k_sseDiffMean)
    # NOTE(v1-quirk): best_index indexes the DIFFERENCES, which are one shorter
    # than k_range, so the elbow path can never return the last k. Callers pass
    # range(2, 8) but only k=2..6 are reachable here; k=7 comes out solely via
    # the fallback below. The legacy code had the same off-by-one and no
    # fallback at all -- it raised IndexError when SSE improved monotonically.
    best_index = below[0] if below.size else len(k_range) - 1
    return k_range[best_index]


def find_ei_cluster_ratio(pei, labels, ei_elec_num=10):
    # NOTE(v1-quirk): this is what bounds the useful cluster count upstream. A
    # cluster only qualifies if it holds more than half (then more than a third)
    # of the top-10 EI channels, so the more clusters choose_kmeans_k() picks,
    # the more those 10 channels fragment and the likelier it is that nothing
    # qualifies and this returns None. The k range and this threshold are
    # coupled, and neither was documented in the legacy code.
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
    """Flattened dB spectrogram per channel, truncated to 0-300 Hz.

    Args:
        raw:   (n_channels, n_samples) signal, volts.
        sfreq: sampling rate, Hz.

    Returns:
        (chan_specs, spec_shape, t, f_cut) -- chan_specs is
        (n_channels, n_freq_bins * n_time_bins) in dB, spec_shape is the
        per-channel (n_freq_bins, n_time_bins) needed to unflatten it, t is in
        seconds and f_cut in Hz.
    """
    if method != 'STFT':
        raise ValueError(f"unsupported method {method!r}; only 'STFT' is implemented")
    if raw.shape[0] == 0:
        raise ValueError("no channels to compute spectrograms for")

    win_len = 0.5
    overlap = 0.8
    freq_range = 300  # Hz
    half_width = win_len * sfreq
    ch_num = raw.shape[0]

    # TODO(cleanup): this grew a row at a time via vstack, which reallocates and
    # copies the whole matrix on every channel -- O(n_channels^2) in memory
    # traffic. Collect into a list and stack once.
    specs = []
    f = t = None
    freq_nums = None
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
        specs.append(hfo_new.reshape(-1))
        spec_shape = hfo_new.shape

    chan_specs = np.vstack(specs)
    # Previously `f[:freq_range]`, which sliced by a frequency in Hz as if it
    # were a bin count. Harmless while every caller discarded f, but wrong.
    f_cut = f[:freq_nums]
    return chan_specs, spec_shape, t, f_cut


def norm_specs(specs):
    specs_mean = specs - specs.mean(axis=0)
    specs_norm = specs_mean / specs_mean.std(axis=0)
    return specs_norm


def compute_full_band(raw_data, sfreq, ei):
    """Cluster channels by spectral shape to reveal electrodes sharing the
    seizure-onset zone's spectral signature.

    Not wired to a router endpoint (ported for parity with the legacy module;
    no REST consumer defined).

    Returns:
        (spec_pca, pre_labels, chosen_cluster_ind) -- the PCA projection, the
        per-channel cluster label, and the indices of channels in the cluster
        that dominates the top-EI channels (empty if no cluster dominates).
    """
    raw_specs, _spec_shape, _t, _f = cal_specs_matrix(raw_data, sfreq, 'STFT')
    raw_specs_norm = norm_specs(raw_specs)
    proj_pca = PCA(n_components=10, random_state=RANDOM_STATE)
    spec_pca = proj_pca.fit_transform(raw_specs_norm)
    k_num = choose_kmeans_k(spec_pca, range(2, 8))
    tmp_kmeans = KMeans(n_clusters=k_num, n_init=10, random_state=RANDOM_STATE)
    tmp_kmeans.fit(spec_pca)
    pre_labels = tmp_kmeans.labels_
    cluster_ind_ratio = find_ei_cluster_ratio(ei, pre_labels)
    if cluster_ind_ratio is None:
        # No cluster held a majority (or a third) of the top-EI channels.
        # Comparing labels against None elementwise would have produced an
        # all-False mask via a numpy deprecation warning; say so instead.
        logger.info("no spectral cluster dominates the top-EI channels")
        return spec_pca, pre_labels, np.array([], dtype=int)
    chosen_cluster_ind = np.flatnonzero(np.isin(pre_labels, cluster_ind_ratio))
    return spec_pca, pre_labels, chosen_cluster_ind


