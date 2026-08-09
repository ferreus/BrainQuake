import logging
import os
import re

import mne
import numpy as np
from scipy.ndimage import gaussian_filter
from scipy.signal import convolve2d, spectrogram
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sqlalchemy.orm import Session

from app.models import Artifact, Job, Subject
from app.services.edf_common import resolve_edf_path
from app.services.job_control import check_cancelled
from app.services.recon import register_artifact
from app.services.signal_filters import DEFAULT_MAINS_FREQ, filter_for_display

logger = logging.getLogger(__name__)

# KMeans initialisation is random; a fixed seed keeps repeated runs on the same
# recording reproducible, which matters when results are meant to be presented
# and re-derived later.
RANDOM_STATE = 0

# Ported near-verbatim from client_ictal.py's module-scope compute_* functions
# (git tag legacy-final; already pure numpy/scipy, no Qt/GUI dependency). The interactive
# baseline/target range-select and band-filter text fields become explicit
# request parameters (see routers/ictal.py) instead of mouse clicks.


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
    # FIXME(correctness): convolve2d(..., 'same') zero-pads both ends, so the
    # first and last `window/2` samples (0.25 s) of each window have
    # artificially suppressed energy. determine_threshold_onset() scans the
    # target window from sample 0, i.e. exactly through the attenuated region,
    # which biases detected onsets later. Padding the slice by >=0.25 s on each
    # side and trimming after convolution would remove the artefact.
    target_energy = convolve2d(target_sq, np.ones((1, window)), 'same')
    base_energy = convolve2d(base_sq, np.ones((1, window)), 'same')
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
    # FIXME(correctness): the threshold is baseline MAX + 20*sigma, so a single
    # artefact anywhere in the baseline (an electrode pop, a movement transient)
    # raises that channel's bar out of reach for the whole seizure and it never
    # registers an onset. Channels with accidentally quiet baselines win. This
    # is the mechanism suspected in docs/bella_ictal_ei_vs_annotation_discrepancy.md.
    # A robust statistic (median + k*MAD, or a high percentile) would not have
    # this failure mode. Changing it changes every EI value, so it needs a
    # deliberate decision + re-verification, not a silent edit.
    sigma = np.std(base, axis=1, ddof=1)
    channel_max_base = np.max(base, axis=1)
    thresh_value = channel_max_base + 20 * sigma

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
        (ei, hfer, onset_rank), each (n_channels,). `ei` is normalised so the
        highest channel is exactly 1.0 -- see the FIXME below, this differs from
        the published EI.
    """
    channel_onset = determine_threshold_onset(target, base)
    seizure_location = np.min(channel_onset)

    # NOTE(v1-quirk): the energy term is measured in a fixed 0.25 s window that
    # starts at the EARLIEST channel's onset, the same window for every channel.
    # Bartolomei's formulation integrates each channel's energy from its OWN
    # detection time. A channel that onsets 3 s after the first one therefore has
    # its energy sampled while it is still quiet, so its EI reflects when the
    # seizure started elsewhere rather than how strongly it discharges.
    hfer = np.sum(target[:, int(seizure_location):int(seizure_location + 0.25 * fs)], axis=1) / (fs * 0.25)

    # FIXME(correctness): this weights by 1/ordinal_rank, not by the published
    # 1/(detection_delay + tau). With ~200 channels, channels that onset within
    # milliseconds of each other are divided by 1, 2, 3 ... 200 purely because
    # sample-level noise ordered them that way -- the 10th channel is penalised
    # 10x against the 1st even if they are 2 ms apart. The published term decays
    # with elapsed SECONDS, so near-simultaneous channels score near-equally.
    # This is the single most likely cause of an EI ranking that disagrees with
    # visual review (docs/bella_ictal_ei_vs_annotation_discrepancy.md).
    time_rank_tmp = np.argsort(channel_onset)
    onset_rank = np.argsort(time_rank_tmp) + 1
    onset_rank = 1.0 / onset_rank.astype(np.float64)

    ei = np.sqrt(hfer * onset_rank)
    ei = np.nan_to_num(ei, nan=0.0, posinf=0.0, neginf=0.0)

    # FIXME(correctness): dividing by the maximum makes EI relative, so the top
    # channel is always exactly 1.0 no matter how unremarkable it is, and the
    # clinical "EI > 0.3 marks the epileptogenic zone" threshold from the
    # literature cannot be applied. It also makes values incomparable between
    # seizures/recordings, which blocks the cross-seizure aggregation that
    # neural fragility does (see docs/project-direction.md). Return the raw EI
    # alongside the normalised one before using EI for anything quantitative.
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


# Channels that are recorded alongside the SEEG contacts but are not brain
# signal. They matter twice over: they are ranked as if they were contacts, and
# -- worse -- filter_for_display() subtracts the mean across ALL channels, so an
# EKG trace leaks into every SEEG channel through the common-average reference.
_NON_SEEG_CHANNEL_RE = re.compile(
    r"^(ekg|ecg|emg|eog|eeg\s|ref|dc\d|trig|mark|events?|annotations?|"
    r"spo2|pleth|pr|resp|sat|osat|photic|sti\d*)",
    re.IGNORECASE,
)


def find_non_seeg_channels(chn_names):
    """Indices of channels whose names look like non-SEEG auxiliary traces."""
    return [i for i, n in enumerate(chn_names) if _NON_SEEG_CHANNEL_RE.match(str(n).strip())]


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
    mains_freq = float(params.get("mains_freq", DEFAULT_MAINS_FREQ))

    job.progress_pct = 10.0
    job.progress_message = "Loading edf and applying notch + bandpass filter"
    db.commit()

    edf_path = resolve_edf_path(subject, artifact)
    # preload=False reads only the header, so the windows can be validated and
    # the span narrowed before any samples are pulled into memory.
    edf_data = mne.io.read_raw_edf(edf_path, preload=False, stim_channel=None)
    fs = edf_data.info['sfreq']
    chn_names = edf_data.ch_names
    duration = float(edf_data.times[-1])

    # Typed-in windows can land outside the recording or invert; catch it before
    # the expensive load, with a message naming the recording length rather than
    # failing later on an empty slice inside compute_hfer.
    for label, t0, t1 in (("baseline", baseline_start, baseline_end),
                          ("target", target_start, target_end)):
        if t0 < 0 or t1 > duration or t0 >= t1:
            raise ValueError(
                f"{label} window {t0:.3f}-{t1:.3f}s is invalid for a {duration:.3f}s recording"
            )

    # Channels the caller kept in the trace viewer. Applied before anything
    # else, so a dropped channel is out of the common-average reference too --
    # not merely absent from the ranking.
    remain_chns = params.get("remain_chns")
    if remain_chns:
        wanted = set(remain_chns)
        picks = [i for i, n in enumerate(chn_names) if n in wanted]
        missing = wanted - set(chn_names)
        if missing:
            raise ValueError(
                f"remain_chns names {len(missing)} channel(s) not in this recording: "
                f"{sorted(missing)}"
            )
        if not picks:
            raise ValueError("remain_chns excluded every channel")
        dropped = [n for i, n in enumerate(chn_names) if i not in set(picks)]
        if dropped:
            logger.info("excluding %d channel(s) at the caller's request: %s", len(dropped), dropped)
        edf_data.pick(picks)
        chn_names = edf_data.ch_names

    aux = find_non_seeg_channels(chn_names)
    if aux:
        # Still only reported, never auto-excluded: dropping a channel changes
        # every other channel's value through the common-average reference, so
        # it stays the caller's decision. This warning is what makes an
        # overlooked one visible -- a REF channel placing 6th in the EI ranking
        # is what prompted docs/bella_ictal_ei_vs_annotation_discrepancy.md.
        logger.warning(
            "%d channel(s) look like non-SEEG auxiliary traces and were NOT excluded; they are "
            "in both the common-average reference and the EI ranking: %s",
            len(aux), [chn_names[i] for i in aux],
        )

    # Only the two windows are used, but filtfilt needs runway on either side or
    # its edge transient lands inside them. 10 s comfortably covers the impulse
    # response of a 1 Hz-cornered 5th-order Butterworth.
    pad = 10.0
    span_start = max(0.0, min(baseline_start, target_start) - pad)
    span_end = min(duration, max(baseline_end, target_end) + pad)
    edf_data.crop(tmin=span_start, tmax=span_end).load_data()
    raw_data, _ = edf_data[:]

    saturated = find_saturated_channels(raw_data)
    if saturated:
        logger.warning(
            "%d/%d channel(s) are clipped at the amplifier rail for >1%% of the analysed "
            "window; their energy is flat-topped and their EI is not meaningful: %s",
            len(saturated), len(chn_names), [chn_names[i] for i in saturated],
        )

    filtered = filter_for_display(raw_data, fs, band_low, band_high, mains_freq=mains_freq)

    # Re-base the window indices onto the cropped span.
    def _idx(t):
        return int(round((t - span_start) * fs))

    base_start_i, base_end_i = _idx(baseline_start), _idx(baseline_end)
    target_start_i, target_end_i = _idx(target_start), _idx(target_end)

    job.progress_pct = 60.0
    job.progress_message = "Computing HFER + EI index"
    db.commit()

    check_cancelled(db, job)

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
