"""Synthetic-signal tests for the ictal numeric core.

These do not compare against the legacy app (see docs/cleanup-plan.md -- no
golden-output harness exists). They pin down behaviour that is checkable from
first principles: a channel with an injected early onset must win, a notch must
remove the frequency it is aimed at, a clipped channel must be reported.

Several tests are deliberately *characterisation* tests: they assert what the
current implementation does, including where that deviates from the published
method, so the deviation is visible and any future fix fails loudly rather than
silently changing results. Those are marked CHARACTERISATION and name the
corresponding FIXME in app/services/ictal.py.
"""

import numpy as np
import pytest

from app.services.ictal import (
    compute_ei_index,
    compute_hfer,
    determine_threshold_onset,
    find_non_seeg_channels,
    find_saturated_channels,
)
from app.services.signal_filters import filter_for_display

FS = 1000.0
N_CH = 20


def _noise(n_ch, n_samples, seed=0, scale=1.0):
    rng = np.random.default_rng(seed)
    return rng.normal(0.0, scale, size=(n_ch, n_samples))


def _burst(n_samples, start_sample, fs=FS, freq=80.0, amp=20.0, dur=2.0):
    """A high-frequency, high-amplitude burst starting at `start_sample`."""
    out = np.zeros(n_samples)
    end = min(n_samples, start_sample + int(dur * fs))
    t = np.arange(end - start_sample) / fs
    out[start_sample:end] = amp * np.sin(2 * np.pi * freq * t)
    return out


def _band_power(sig, fs, freq, half_width=2.0):
    """Power of `sig` in a narrow band around `freq`."""
    spec = np.abs(np.fft.rfft(sig))
    freqs = np.fft.rfftfreq(len(sig), 1.0 / fs)
    sel = (freqs >= freq - half_width) & (freqs <= freq + half_width)
    return float(np.sum(spec[sel] ** 2))


# --------------------------------------------------------------------------
# EI: the channel that starts discharging first must win
# --------------------------------------------------------------------------

def _make_seizure(onset_channel=5, onset_time=1.0, second_channel=10, second_time=3.0):
    n_base = int(10 * FS)
    n_target = int(10 * FS)
    base = _noise(N_CH, n_base, seed=1)
    target = _noise(N_CH, n_target, seed=2)
    target[onset_channel] += _burst(n_target, int(onset_time * FS))
    target[second_channel] += _burst(n_target, int(second_time * FS))
    return target, base


def test_ei_ranks_the_injected_onset_channel_first():
    target, base = _make_seizure()
    norm_t, norm_b = compute_hfer(target, base, FS)
    ei, _hfer, _rank = compute_ei_index(norm_t, norm_b, FS)

    assert ei.shape == (N_CH,)
    assert int(np.argmax(ei)) == 5, f"expected channel 5 to rank first, got {np.argmax(ei)}"
    assert ei.max() == pytest.approx(1.0), "EI is normalised so the top channel is exactly 1.0"


def test_moving_the_onset_moves_the_winner():
    """The ranking must follow the injected signal, not a fixed channel index."""
    target, base = _make_seizure(onset_channel=17)
    norm_t, norm_b = compute_hfer(target, base, FS)
    ei, _, _ = compute_ei_index(norm_t, norm_b, FS)
    assert int(np.argmax(ei)) == 17


def test_quiet_recording_yields_no_ei():
    """With no channel crossing threshold, every onset ties and EI must not blow up."""
    base = _noise(N_CH, int(10 * FS), seed=3)
    target = _noise(N_CH, int(10 * FS), seed=4)
    norm_t, norm_b = compute_hfer(target, base, FS)
    ei, _, _ = compute_ei_index(norm_t, norm_b, FS)
    assert np.all(np.isfinite(ei))
    assert np.all(ei >= 0)


def test_dead_channel_gets_zero_ei_not_nan():
    """A flat channel has zero baseline energy; it must not poison the output."""
    target, base = _make_seizure()
    base[0] = 0.0
    target[0] = 0.0
    norm_t, norm_b = compute_hfer(target, base, FS)
    ei, _, _ = compute_ei_index(norm_t, norm_b, FS)
    assert np.all(np.isfinite(ei))
    assert ei[0] == 0.0


# --------------------------------------------------------------------------
# Onset detection
# --------------------------------------------------------------------------

def test_onset_detection_orders_channels_by_start_time():
    target, base = _make_seizure(onset_channel=5, onset_time=1.0,
                                 second_channel=10, second_time=3.0)
    norm_t, norm_b = compute_hfer(target, base, FS)
    onsets = determine_threshold_onset(norm_t, norm_b)
    assert onsets[5] < onsets[10], "the earlier burst must be detected earlier"


def test_channels_that_never_cross_are_placed_at_the_end():
    target, base = _make_seizure()
    norm_t, norm_b = compute_hfer(target, base, FS)
    onsets = determine_threshold_onset(norm_t, norm_b)
    quiet = [i for i in range(N_CH) if i not in (5, 10)]
    assert np.all(onsets[quiet] == norm_t.shape[1])


def test_baseline_artifact_hides_a_real_onset():
    """CHARACTERISATION -- FIXME(correctness) in determine_threshold_onset.

    The threshold is baseline max + 20*sigma, so one artifact in the baseline
    raises a channel's bar out of reach and its real onset is never detected.
    """
    target, base = _make_seizure(onset_channel=5)
    norm_t, norm_b = compute_hfer(target, base, FS)
    clean_onset = determine_threshold_onset(norm_t, norm_b)[5]
    assert clean_onset < norm_t.shape[1], "sanity: the onset is detectable without an artifact"

    base_with_pop = base.copy()
    base_with_pop[5, int(2 * FS)] += 5000.0  # a single-sample electrode pop
    norm_t2, norm_b2 = compute_hfer(target, base_with_pop, FS)
    hidden_onset = determine_threshold_onset(norm_t2, norm_b2)[5]
    assert hidden_onset == norm_t2.shape[1], (
        "one baseline artifact should suppress this channel entirely -- if this "
        "fails, the thresholding was changed and every EI value moved with it"
    )


def test_ranking_of_a_late_channel_comes_entirely_from_the_rank_term():
    """CHARACTERISATION -- the NOTE(v1-quirk) and the 1/rank FIXME in
    compute_ei_index, interacting.

    A channel that starts discharging 2 s after the first one has its energy
    measured in the fixed 0.25 s window anchored to the FIRST channel's onset,
    while it is still quiet. Its energy term is therefore indistinguishable from
    a channel that never seizes at all. It still ranks second -- on the 1/rank
    weighting alone. That is the whole ranking mechanism laid bare: ordinal
    position decides the result, measured signal strength does not.
    """
    target, base = _make_seizure(onset_channel=5, onset_time=1.0,
                                 second_channel=10, second_time=3.0)
    norm_t, norm_b = compute_hfer(target, base, FS)
    ei, hfer, rank = compute_ei_index(norm_t, norm_b, FS)
    quiet = [i for i in range(N_CH) if i not in (5, 10)]

    # The energy term cannot tell the late seizing channel from a quiet one.
    assert hfer[10] == pytest.approx(np.median(hfer[quiet]), rel=0.2)
    # The rank term can, and by a wide margin.
    assert rank[10] > np.median(rank[quiet]) * 3
    # So it places second on rank alone.
    assert list(np.argsort(-ei)[:2]) == [5, 10]


# --------------------------------------------------------------------------
# Filtering
# --------------------------------------------------------------------------

def test_notch_removes_the_mains_frequency_it_is_given():
    n = int(10 * FS)
    t = np.arange(n) / FS
    data = _noise(N_CH, n, seed=5, scale=0.1)
    data[0] += 10 * np.sin(2 * np.pi * 60 * t) + 10 * np.sin(2 * np.pi * 10 * t)

    out = filter_for_display(data, FS, 1.0, 200.0, mains_freq=60.0)

    before_60 = _band_power(data[0], FS, 60.0)
    after_60 = _band_power(out[0], FS, 60.0)
    after_10 = _band_power(out[0], FS, 10.0)
    assert after_60 < before_60 * 0.05, "60 Hz should be strongly attenuated"
    assert after_10 > after_60 * 100, "10 Hz signal of interest must survive"


def test_wrong_mains_frequency_leaves_the_interference():
    """CHARACTERISATION -- FIXME(correctness) on DEFAULT_MAINS_FREQ.

    Filtering a 60 Hz recording with the 50 Hz default leaves the interference
    untouched. This is silent in production: nothing warns.
    """
    n = int(10 * FS)
    t = np.arange(n) / FS
    data = _noise(N_CH, n, seed=6, scale=0.1)
    data[0] += 10 * np.sin(2 * np.pi * 60 * t)

    right = filter_for_display(data, FS, 1.0, 200.0, mains_freq=60.0)
    wrong = filter_for_display(data, FS, 1.0, 200.0, mains_freq=50.0)

    assert _band_power(wrong[0], FS, 60.0) > _band_power(right[0], FS, 60.0) * 50


def test_bandpass_rejects_outside_the_band():
    n = int(10 * FS)
    t = np.arange(n) / FS
    data = _noise(N_CH, n, seed=7, scale=0.1)
    data[0] += 10 * np.sin(2 * np.pi * 5 * t) + 10 * np.sin(2 * np.pi * 200 * t)

    out = filter_for_display(data, FS, 1.0, 40.0, mains_freq=60.0)

    assert _band_power(out[0], FS, 5.0) > _band_power(out[0], FS, 200.0) * 100


# --------------------------------------------------------------------------
# Data-quality screening
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["EKG1", "ecg", "REF2", "DC01", "EMG-L", "Annotations", "SpO2"])
def test_auxiliary_channel_names_are_recognised(name):
    assert find_non_seeg_channels([name]) == [0]


@pytest.mark.parametrize("name", ["A1", "X'12", "L'8", "D6", "M10", "G'3"])
def test_seeg_contact_names_are_not_flagged(name):
    assert find_non_seeg_channels([name]) == []


def test_saturated_channel_is_detected():
    n = int(10 * FS)
    data = _noise(4, n, seed=8)
    rail = 500.0
    data[2] = np.clip(data[2] * 400.0, -rail, rail)
    # Force a large clipped fraction well above the 1% threshold.
    data[2, : int(0.3 * n)] = rail
    assert find_saturated_channels(data) == [2]


def test_clean_channels_are_not_reported_as_saturated():
    data = _noise(4, int(10 * FS), seed=9)
    assert find_saturated_channels(data) == []


def test_flat_channel_is_not_reported_as_saturated():
    """A dead channel is degenerate, not clipped -- compute_hfer reports it separately."""
    data = _noise(4, int(10 * FS), seed=10)
    data[1] = 0.0
    assert 1 not in find_saturated_channels(data)
