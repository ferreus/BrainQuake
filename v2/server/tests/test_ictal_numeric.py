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

from app.sigproc.channels import seeg_contacts
from app.sigproc.ei import (
    compute_band_ratio,
    compute_ei_index,
    compute_ei_pipeline,
    compute_hfer,
    determine_threshold_onset,
    ei_diagnostics,
    find_saturated_channels,
    load_ei_result,
    save_ei_result,
)
from app.sigproc.filters import bandpass, filter_for_display

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
    ei, _ei_raw, _hfer, _tc = compute_ei_index(norm_t, norm_b, FS)

    assert ei.shape == (N_CH,)
    assert int(np.argmax(ei)) == 5, f"expected channel 5 to rank first, got {np.argmax(ei)}"
    assert ei.max() == pytest.approx(1.0), "EI is normalised so the top channel is exactly 1.0"


def test_moving_the_onset_moves_the_winner():
    """The ranking must follow the injected signal, not a fixed channel index."""
    target, base = _make_seizure(onset_channel=17)
    norm_t, norm_b = compute_hfer(target, base, FS)
    ei, _, _, _ = compute_ei_index(norm_t, norm_b, FS)
    assert int(np.argmax(ei)) == 17


def test_quiet_recording_yields_no_ei():
    """With no channel crossing threshold, every onset ties and EI must not blow up."""
    base = _noise(N_CH, int(10 * FS), seed=3)
    target = _noise(N_CH, int(10 * FS), seed=4)
    norm_t, norm_b = compute_hfer(target, base, FS)
    ei, _, _, _ = compute_ei_index(norm_t, norm_b, FS)
    assert np.all(np.isfinite(ei))
    assert np.all(ei >= 0)


def test_dead_channel_gets_nan_ei_not_zero():
    """A flat channel has zero baseline energy, so its EI is undefined.

    It used to be forced to 0.0, which is a real score meaning "quiet" -- a dead
    electrode then sorted alongside genuinely silent brain instead of being
    excluded. NaN is the honest answer and soz fusion masks it.
    """
    target, base = _make_seizure()
    base[0] = 0.0
    target[0] = 0.0
    norm_t, norm_b = compute_hfer(target, base, FS)
    ei, _, _, _ = compute_ei_index(norm_t, norm_b, FS)
    assert np.isnan(ei[0])
    assert np.all(np.isfinite(ei[1:])), "one dead channel must not poison the rest"


# --------------------------------------------------------------------------
# Onset detection
# --------------------------------------------------------------------------

def test_onset_detection_orders_channels_by_start_time():
    target, base = _make_seizure(onset_channel=5, onset_time=1.0,
                                 second_channel=10, second_time=3.0)
    norm_t, norm_b = compute_hfer(target, base, FS)
    onsets, crossed = determine_threshold_onset(norm_t, norm_b)
    assert crossed[5] and crossed[10]
    assert onsets[5] < onsets[10], "the earlier burst must be detected earlier"


def test_channels_that_never_cross_are_reported_as_not_crossed():
    """Non-crossing channels are flagged by the mask, not by an out-of-range index.

    The old sentinel (onset == n_samples) doubled as "skip this channel", which
    is what left the whole tail of the ranking tied at EI = 0.
    """
    target, base = _make_seizure()
    norm_t, norm_b = compute_hfer(target, base, FS)
    _onsets, crossed = determine_threshold_onset(norm_t, norm_b)
    quiet = [i for i in range(N_CH) if i not in (5, 10)]
    assert not crossed[quiet].any()
    assert crossed[[5, 10]].all()


def test_non_crossing_channels_are_graded_by_energy_not_tied_at_zero():
    """The tail must carry information: a louder quiet channel outranks a
    quieter one, instead of every non-crosser collapsing to exactly 0."""
    n = int(10 * FS)
    base = _noise(N_CH, n, seed=21)
    target = _noise(N_CH, n, seed=22)
    target[5] += _burst(n, int(1.0 * FS))          # the only channel that crosses
    target[9] *= 1.3                                # elevated, but below threshold
    norm_t, norm_b = compute_hfer(target, base, FS)
    _onsets, crossed = determine_threshold_onset(norm_t, norm_b)
    ei, _, _, _ = compute_ei_index(norm_t, norm_b, FS)

    assert crossed[5] and not crossed[9]
    quiet = [i for i in range(N_CH) if i not in (5, 9)]
    assert ei[9] > np.max(ei[quiet]), "the elevated non-crosser must outrank flat ones"
    assert len(np.unique(ei[quiet])) > 1, "the tail must not be a single tied value"


def test_baseline_artifact_no_longer_hides_a_real_onset():
    """A single baseline pop must not suppress the channel's real onset.

    Regression on the old max + 20*sigma threshold, which one artifact put out
    of reach for the whole seizure.
    """
    target, base = _make_seizure(onset_channel=5)
    norm_t, norm_b = compute_hfer(target, base, FS)
    clean = determine_threshold_onset(norm_t, norm_b)
    assert clean.crossed[5], "sanity: the onset is detectable without an artifact"

    base_with_pop = base.copy()
    base_with_pop[5, int(2 * FS)] += 5000.0  # a single-sample electrode pop
    norm_t2, norm_b2 = compute_hfer(target, base_with_pop, FS)
    popped = determine_threshold_onset(norm_t2, norm_b2)
    assert popped.crossed[5], "the pop must not suppress the channel"
    assert popped.onset[5] == pytest.approx(clean.onset[5], abs=0.05 * FS)


def test_late_channel_energy_is_measured_at_its_own_onset():
    """The energy term must see a late channel's discharge, not the quiet signal
    at the first channel's onset."""
    target, base = _make_seizure(onset_channel=5, onset_time=1.0,
                                 second_channel=10, second_time=3.0)
    norm_t, norm_b = compute_hfer(target, base, FS)
    ei, _ei_raw, hfer, _tc = compute_ei_index(norm_t, norm_b, FS)
    quiet = [i for i in range(N_CH) if i not in (5, 10)]

    assert hfer[10] > np.median(hfer[quiet]) * 10, (
        "a channel that discharges 2 s late must show real energy in its own window"
    )
    assert list(np.argsort(-ei)[:2]) == [5, 10]


def test_near_simultaneous_channels_are_not_split_by_ordinal_rank():
    """Two channels 10 ms apart must score comparably.

    Regression on the 1/ordinal_rank time term, which divided the second channel
    by 2 regardless of how close in time it actually was.
    """
    target, base = _make_seizure(onset_channel=5, onset_time=1.0,
                                 second_channel=10, second_time=1.01)
    norm_t, norm_b = compute_hfer(target, base, FS)
    _ei, _ei_raw, _hfer, time_coef = compute_ei_index(norm_t, norm_b, FS)
    assert time_coef[10] > time_coef[5] * 0.95


def test_raw_ei_is_returned_unnormalised():
    """`ei` is scaled so the top channel is 1.0; `ei_raw` must not be."""
    target, base = _make_seizure()
    norm_t, norm_b = compute_hfer(target, base, FS)
    ei, ei_raw, _hfer, _tc = compute_ei_index(norm_t, norm_b, FS)
    assert ei.max() == pytest.approx(1.0)
    assert ei_raw.max() != pytest.approx(1.0)
    assert ei == pytest.approx(ei_raw / ei_raw.max())


def test_energy_is_not_suppressed_at_the_window_edges():
    """A burst at the very start of the target window must register as strongly
    as the same burst in the middle -- regression on the zero-padded convolution.
    """
    n = int(10 * FS)
    base = _noise(N_CH, n, seed=11)
    early = _noise(N_CH, n, seed=12)
    early[0] += _burst(n, 0, dur=1.0)
    middle = _noise(N_CH, n, seed=12)
    middle[0] += _burst(n, int(5 * FS), dur=1.0)

    norm_early, _ = compute_hfer(early, base, FS)
    norm_middle, _ = compute_hfer(middle, base, FS)
    assert norm_early[0].max() == pytest.approx(norm_middle[0].max(), rel=0.1)


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

# Every auxiliary name below is real -- taken from the 203-channel Nihon Kohden
# export of Bella's clip 17 (184 SEEG contacts + 19 of these).
@pytest.mark.parametrize(
    "name",
    ["EKG1", "EKG2", "ecg", "REF1", "REF2", "DC01", "DC10", "UNUSED248", "E",
     "EMG-L", "Annotations", "SpO2"],
)
def test_auxiliary_channel_names_are_excluded(name):
    assert seeg_contacts(["A1", name, "B2"]) == ["A1", "B2"]


@pytest.mark.parametrize("name", ["A1", "X'12", "L'8", "D6", "M10", "G'3", "Q6", "K'12"])
def test_seeg_contact_names_are_kept(name):
    assert seeg_contacts([name]) == [name]


def test_file_matching_no_contact_name_keeps_every_channel():
    # A recording on another naming convention must not be emptied out.
    names = ["CH1", "CH2", "Fp1-F7", "SpO2"]
    assert seeg_contacts(names) == names


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


# --------------------------------------------------------------------------
# Bartolomei band ratio
# --------------------------------------------------------------------------

def _osc(n, freq, amp, fs=FS):
    return amp * np.sin(2 * np.pi * freq * np.arange(n) / fs)


def _two_band_noise(seed_low, seed_high, n):
    """Signal with independent theta/alpha and beta/gamma content, so the energy
    ratio has real variance -- a pure sinusoid makes the baseline ratio so
    stationary that MAD collapses and every channel crosses threshold."""
    low = bandpass(_noise(N_CH, n, seed=seed_low), FS, 3.5, 12.4) * 5
    high = bandpass(_noise(N_CH, n, seed=seed_high), FS, 12.4, 97.0)
    return low, high


def _band_shift_seizure(shift_channel=5, shift_time=2.0):
    """One channel moves its energy from theta/alpha into beta/gamma, at
    constant total power. Only a band-ratio method can see this."""
    n = int(10 * FS)
    low_b, high_b = _two_band_noise(31, 131, n)
    low_t, high_t = _two_band_noise(32, 132, n)
    target = low_t + high_t
    k = int(shift_time * FS)
    low_seg, high_seg = low_t[shift_channel, k:], high_t[shift_channel, k:]
    e_low, e_high = np.sum(low_seg ** 2), np.sum(high_seg ** 2)
    a = 0.05  # gut the low band, put its energy into the high band
    b = np.sqrt((e_low + e_high - a * a * e_low) / e_high)
    target[shift_channel, k:] = a * low_seg + b * high_seg
    return target, low_b + high_b


def test_band_ratio_detects_energy_moving_from_low_to_high_band():
    target, base = _band_shift_seizure()
    ei, _, _, _ = compute_ei_index(*compute_band_ratio(target, base, FS), FS)
    assert int(np.argmax(ei)) == 5


def test_broadband_is_blind_to_a_pure_band_shift():
    """The shift preserves total power, so the older broadband method has
    nothing to detect. This is why band_ratio is the default."""
    target, base = _band_shift_seizure()
    ei, _, _, _ = compute_ei_index(*compute_hfer(target, base, FS), FS)
    assert int(np.argmax(ei)) != 5
    assert ei[5] < 0.5, "broadband must not rank a constant-power band shift highly"


def test_band_ratio_only_responds_to_its_configured_high_band():
    """40 Hz added on top of an unchanged low band: seen by a 12.4-97 Hz high
    band, invisible to a 60-97 Hz one."""
    n = int(10 * FS)
    low_b, high_b = _two_band_noise(41, 141, n)
    low_t, high_t = _two_band_noise(42, 142, n)
    target = low_t + high_t
    k = int(2.0 * FS)
    target[5, k:] += _osc(n, 40.0, 4.0)[k:]
    base = low_b + high_b

    inside, _, _, _ = compute_ei_index(
        *compute_band_ratio(target, base, FS, high_band=(12.4, 97.0)), FS)
    outside, _, _, _ = compute_ei_index(
        *compute_band_ratio(target, base, FS, high_band=(60.0, 97.0)), FS)
    assert int(np.argmax(inside)) == 5
    assert int(np.argmax(outside)) != 5


def test_band_ratio_clamps_a_band_above_nyquist():
    """A 97 Hz gamma edge is impossible at fs=150; clamp instead of raising."""
    fs = 150.0
    n = int(10 * fs)
    base = _noise(4, n, seed=33)
    target = _noise(4, n, seed=34)
    norm_t, norm_b = compute_band_ratio(target, base, fs)
    assert norm_t.shape[0] == 4 and np.isfinite(norm_t).any()


# --------------------------------------------------------------------------
# Degenerate target window
# --------------------------------------------------------------------------

def test_degenerate_window_flagged_when_discharge_precedes_the_window():
    n = int(10 * FS)
    base = _noise(N_CH, n, seed=41)
    target = _noise(N_CH, n, seed=42)
    for i in range(N_CH):  # every channel already discharging at sample 0
        target[i] += _burst(n, 0, dur=8.0)
    norm_t, norm_b = compute_hfer(target, base, FS)
    onset, crossed = determine_threshold_onset(norm_t, norm_b)
    diag = ei_diagnostics(onset, crossed, FS, norm_t.shape[1])
    assert diag["degenerate_window"]
    assert diag["frac_onset_at_window_start"] > 0.2


def test_clean_seizure_is_not_flagged_as_degenerate():
    target, base = _make_seizure(onset_channel=5, onset_time=1.0,
                                 second_channel=10, second_time=3.0)
    norm_t, norm_b = compute_hfer(target, base, FS)
    onset, crossed = determine_threshold_onset(norm_t, norm_b)
    diag = ei_diagnostics(onset, crossed, FS, norm_t.shape[1])
    assert not diag["degenerate_window"]
    assert diag["n_crossed"] == 2
    assert diag["n_never_crossed"] == N_CH - 2


# --------------------------------------------------------------------------
# Result archive compatibility
# --------------------------------------------------------------------------

def test_ei_result_written_before_diagnostics_existed_still_loads(tmp_path):
    path = tmp_path / "old_ei.npz"
    np.savez(path, ei=np.array([1.0, 0.5]), ei_raw=np.array([2.0, 1.0]),
             hfer=np.array([4.0, 1.0]), time_coef=np.array([1.0, 0.5]),
             chn_names=np.array(["A1", "A2"]))
    result = load_ei_result(str(path))
    assert result["chn_names"] == ["A1", "A2"]
    assert result["diagnostics"] == {}
    # Pre-projection archives were all CAR, so their channels are their contacts.
    assert result["contact_names"] == ["A1", "A2"]
    assert result["ei_by_contact"] == [1.0, 0.5]


def test_ei_result_round_trips_the_contact_projection(tmp_path):
    edf = tmp_path / "rec.edf"
    edf.write_bytes(b"")
    path = save_ei_result(
        str(edf), ["A1-A2", "A2-A3"],
        np.array([1.0, 0.4]), np.array([2.0, 0.8]),
        np.array([4.0, 1.0]), np.array([1.0, 0.5]),
        diagnostics={"reference": "bipolar"},
        ei_by_contact={"A1": 1.0, "A2": 1.0, "A3": 0.4},
    )
    result = load_ei_result(path)
    assert result["chn_names"] == ["A1-A2", "A2-A3"]
    assert result["diagnostics"]["reference"] == "bipolar"
    assert dict(zip(result["contact_names"], result["ei_by_contact"])) == {
        "A1": 1.0, "A2": 1.0, "A3": 0.4,
    }


def test_bipolar_pipeline_reports_pairs_and_a_contact_projection():
    fs, n = 500.0, 4000
    rng = np.random.default_rng(3)
    data = rng.normal(0, 1e-5, (4, n))
    data[2, 2500:] += rng.normal(0, 5e-4, n - 2500)  # A3 gets the discharge
    names = ["A1", "A2", "A3", "A4"]
    res = compute_ei_pipeline(
        raw_data=data, fs=fs, chn_names=names,
        baseline_start=0.0, baseline_end=3.0, target_start=5.0, target_end=8.0,
        reference="bipolar",
    )
    assert res["chn_names"] == ["A1-A2", "A2-A3", "A3-A4"]
    assert res["diagnostics"]["reference"] == "bipolar"
    assert set(res["ei_by_contact"]) == set(names)
    # A3 sits in the two hottest derivations, so it must outrank the far end.
    assert res["ei_by_contact"]["A3"] > res["ei_by_contact"]["A1"]


def test_unpairable_names_fall_back_to_car():
    """Diagnostics must record the reference that actually ran, not the one asked for."""
    fs, n = 500.0, 4000
    data = np.random.default_rng(5).normal(0, 1e-5, (2, n))
    names = ["REF", "MARKER"]
    res = compute_ei_pipeline(
        raw_data=data, fs=fs, chn_names=names,
        baseline_start=0.0, baseline_end=3.0, target_start=5.0, target_end=8.0,
        reference="bipolar",
    )
    assert res["pairs"] is None
    assert res["chn_names"] == names
    assert res["diagnostics"]["reference"] == "car"


def test_car_pipeline_contact_projection_is_the_identity():
    fs, n = 500.0, 4000
    data = np.random.default_rng(4).normal(0, 1e-5, (4, n))
    names = ["A1", "A2", "A3", "A4"]
    res = compute_ei_pipeline(
        raw_data=data, fs=fs, chn_names=names,
        baseline_start=0.0, baseline_end=3.0, target_start=5.0, target_end=8.0,
        reference="car",
    )
    assert res["chn_names"] == names
    assert res["ei_by_contact"] == res["ei_scores"]
