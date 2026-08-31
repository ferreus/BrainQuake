"""Name-matching and multi-run fusion tests for the SOZ step.

Contact names come from imaging (electrode segmentation or the 3D Slicer
import); channel names come from the EDF header. Nothing upstream guarantees
the two use the same convention, and a mismatch is silent by construction --
every lookup misses, every value becomes NaN, every combined score becomes 0,
and the CSV still looks well-formed. These tests pin the detection of that,
and the per-process/per-run averaging that fuses several seizures.
"""

import numpy as np
import pytest

from app.services.processes import PROCESSES
from app.sigproc.ei import save_ei_result
from app.sigproc.fusion import (
    describe_name_overlap,
    fuse_contact_scores,
    fused_processes,
    rank_pct,
)


def _contacts(names):
    return {n: np.array([float(i), 0.0, 0.0]) for i, n in enumerate(names)}


def _ei_scores(path):
    """Read an EI archive the way the fusion job does -- through the registry."""
    spec = PROCESSES["ei"]
    return spec.scores(spec.load(path))


# --------------------------------------------------------------------------
# Overlap reporting
# --------------------------------------------------------------------------

def test_overlap_reports_a_total_mismatch():
    contacts = _contacts(["A1", "A2", "B1"])
    channels = {"POL A1": 0.5, "POL A2": 0.4, "POL B1": 0.3}
    o = describe_name_overlap(contacts, channels, "ei")
    assert o["matched"] == 0
    assert o["n_contacts"] == 3
    assert o["n_channels"] == 3
    # The report must show both conventions so the mismatch is diagnosable.
    assert "A1" in o["unmatched_contacts"]
    assert "POL A1" in o["unused_channels"]


def test_overlap_reports_a_partial_match():
    contacts = _contacts(["A1", "A2", "B1"])
    channels = {"A1": 0.5, "A2": 0.4, "EKG": 0.1}
    o = describe_name_overlap(contacts, channels, "ei")
    assert o["matched"] == 2
    assert o["unmatched_contacts"] == ["B1"]
    assert o["unused_channels"] == ["EKG"]


def test_primed_electrode_names_match_exactly():
    """Bilateral implants use primed shaft names; they must survive verbatim."""
    contacts = _contacts(["X'12", "X12"])
    channels = {"X'12": 0.9, "X12": 0.1}
    assert describe_name_overlap(contacts, channels, "ei")["matched"] == 2


# --------------------------------------------------------------------------
# The failure mode the job-level guard exists to catch
# --------------------------------------------------------------------------

def test_total_mismatch_produces_a_well_formed_but_meaningless_table():
    """CHARACTERISATION of why run_soz_fuse_job refuses this case.

    Nothing in fuse_contact_scores itself errors: it returns one row per
    contact, with NaN scores and a combined score of 0 for every one. Sorting
    that is a no-op, so the output reads like a ranking and is not one.
    """
    contacts = _contacts(["A1", "A2", "B1"])
    rows = fuse_contact_scores(contacts, {"ei": [{"POL A1": 0.9}], "hfo": [{"POL A2": 5}]})

    assert len(rows) == 3
    assert all(np.isnan(r["ei"]) for r in rows)
    assert all(r["combined_score"] == 0 for r in rows)


def test_partial_match_ranks_only_the_matched_contacts():
    contacts = _contacts(["A1", "A2", "A3"])
    rows = fuse_contact_scores(contacts, {"ei": [{"A1": 0.9, "A2": 0.1}]})
    by_name = {r["contact"]: r for r in rows}

    assert by_name["A1"]["combined_score"] > by_name["A2"]["combined_score"]
    assert by_name["A3"]["combined_score"] == 0
    assert np.isnan(by_name["A3"]["ei"])
    assert rows[0]["contact"] == "A1"


def test_hfo_absent_entirely_still_ranks_on_ei():
    """Dropping HFO must leave EI-only fusion working (see project-direction.md)."""
    contacts = _contacts(["A1", "A2"])
    rows = fuse_contact_scores(contacts, {"ei": [{"A1": 0.2, "A2": 0.8}], "hfo": []})
    assert rows[0]["contact"] == "A2"
    assert fused_processes(rows) == ["ei"], "a process with no runs contributes no column"


def test_a_process_with_no_runs_is_not_a_column():
    rows = fuse_contact_scores(["A1", "A2"], {"ei": [], "hfo": [], "fragility": []})
    assert fused_processes(rows) == []
    assert all(r["combined_score"] == 0 for r in rows)


# --------------------------------------------------------------------------
# Several runs of one process
# --------------------------------------------------------------------------

def test_two_fragility_runs_average_their_percentiles():
    """One seizure's ranking swings too much to fuse on; both runs must count."""
    contacts = _contacts(["A1", "A2", "A3"])
    rows = fuse_contact_scores(contacts, {
        "fragility": [
            {"A1": 0.9, "A2": 0.5, "A3": 0.1},  # percentiles 1.0 / 0.5 / 0.0
            {"A1": 0.1, "A2": 0.5, "A3": 0.9},  # percentiles 0.0 / 0.5 / 1.0
        ],
    })
    by_name = {r["contact"]: r for r in rows}
    assert by_name["A1"]["fragility_percentile"] == pytest.approx(0.5)
    assert by_name["A3"]["fragility_percentile"] == pytest.approx(0.5)
    assert by_name["A2"]["fragility_percentile"] == pytest.approx(0.5)
    assert all(r["fragility_n_runs"] == 2 for r in rows)


def test_runs_are_percentiled_before_averaging_not_after():
    """Raw scales differ between recordings; averaging raw values would let one
    high-amplitude run set the whole ranking."""
    contacts = _contacts(["A1", "A2"])
    rows = fuse_contact_scores(contacts, {
        # A2 wins run one by a hair; A1 wins run two by 1000x. On percentiles
        # that is a tie; on raw means A1 would run away with it.
        "fragility": [{"A1": 0.1, "A2": 0.2}, {"A1": 1000.0, "A2": 1.0}],
    })
    assert {r["fragility_percentile"] for r in rows} == {0.5}


def test_each_process_weighs_the_same_regardless_of_run_count():
    """Three fragility runs must not outvote one EI run."""
    contacts = _contacts(["A1", "A2"])
    hot_in_frag = [{"A1": 0.0, "A2": 1.0}] * 3
    rows = fuse_contact_scores(contacts, {
        "ei": [{"A1": 1.0, "A2": 0.0}],
        "fragility": hot_in_frag,
    })
    by_name = {r["contact"]: r for r in rows}
    assert by_name["A1"]["combined_score"] == pytest.approx(0.5)
    assert by_name["A2"]["combined_score"] == pytest.approx(0.5)


def test_a_contact_missing_from_one_run_averages_over_the_others():
    contacts = _contacts(["A1", "A2", "A3"])
    rows = fuse_contact_scores(contacts, {
        "fragility": [{"A1": 0.9, "A2": 0.1, "A3": 0.5}, {"A1": 0.9, "A2": 0.1}],
    })
    by_name = {r["contact"]: r for r in rows}
    assert by_name["A3"]["fragility_n_runs"] == 1
    assert by_name["A3"]["fragility_percentile"] == pytest.approx(0.5)
    assert by_name["A1"]["fragility_n_runs"] == 2


# --------------------------------------------------------------------------
# Ranking helper
# --------------------------------------------------------------------------

def test_rank_pct_spans_zero_to_one():
    pct = rank_pct([10.0, 20.0, 30.0])
    assert pct[0] == pytest.approx(0.0)
    assert pct[-1] == pytest.approx(1.0)


def test_rank_pct_handles_a_single_value():
    assert rank_pct([42.0]).tolist() == [0.0]


# --------------------------------------------------------------------------
# HFO segment timing (services/interictal.py)
# --------------------------------------------------------------------------

def test_hfo_segment_start_times_follow_the_analysis_window(tmp_path):
    """Event times must be absolute, not relative to the start of the window.

    Segment start used to be recomputed as (segment_index - 1) * segment_time,
    which assumes the analysis began at t=0. With a non-zero start_time every
    reported HFO time was shifted by the window offset. The real start is now
    read from the rawTimes the preprocessing step already saves.
    """
    from app.services.interictal import find_high_enveTimes_dir

    fs = 1000.0
    window_start = 600.0
    seg_len = 50.0
    rng = np.random.default_rng(0)

    for seg_i in range(2):
        seg_start = window_start + seg_i * seg_len
        n = int(seg_len * fs)
        # A flat baseline with only slight jitter: the detector thresholds at
        # 3x the median, so this keeps the background well clear of it and the
        # test measures segment timing rather than detector sensitivity.
        enve = 1.0 + rng.normal(0.0, 0.01, size=(2, n))
        burst = slice(int(20 * fs), int(20.5 * fs))
        enve[0, burst] = 50.0
        np.savez(
            tmp_path / f"rawEnve_{seg_i + 1}.npz",
            rawEnve=enve,
            rawTimes=np.arange(n) / fs + seg_start,
            valid_chns=np.array(["A1", "A2"]),
            valid_chns_index=np.array([0, 1]),
            fs=fs,
        )

    times, counts, names = find_high_enveTimes_dir(str(tmp_path))

    assert list(names) == ["A1", "A2"]
    assert counts[0] == 2, "one detected burst per segment"
    starts = sorted(t[0] for t in times[0])
    assert starts[0] == pytest.approx(window_start + 20.0, abs=0.1)
    assert starts[1] == pytest.approx(window_start + seg_len + 20.0, abs=0.1)


# --------------------------------------------------------------------------
# Bipolar EI must still join to contacts
# --------------------------------------------------------------------------

def test_bipolar_ei_archive_loads_keyed_by_contact(tmp_path):
    """The regression that motivated storing a contact projection.

    Under a bipolar reference the analysed channels are pairs (A1-A2), which
    match no contact in the electrode map -- every lookup would miss, every EI
    would become NaN, and the fusion would silently degrade to HFO-only.
    """
    edf = tmp_path / "rec.edf"
    edf.write_bytes(b"")
    path = save_ei_result(
        str(edf), ["A1-A2", "A2-A3"],
        np.array([1.0, 0.4]), np.array([2.0, 0.8]),
        np.array([4.0, 1.0]), np.array([1.0, 0.5]),
        diagnostics={"reference": "bipolar"},
        ei_by_contact={"A1": 1.0, "A2": 1.0, "A3": 0.4},
    )
    by_chan = _ei_scores(path)
    assert by_chan == {"A1": 1.0, "A2": 1.0, "A3": 0.4}

    overlap = describe_name_overlap(["A1", "A2", "A3"], by_chan, "ei")
    assert overlap["matched"] == 3, "bipolar EI must still match the electrode map"

    rows = fuse_contact_scores(_contacts(["A1", "A2", "A3"]), {"ei": [by_chan]})
    ranked = [r["contact"] for r in rows]
    assert set(ranked[:2]) == {"A1", "A2"}, "both members of the hot pair rank above A3"
    assert ranked[-1] == "A3"
    assert all(not np.isnan(r["ei"]) for r in rows)


def test_car_ei_archive_still_loads_by_channel(tmp_path):
    edf = tmp_path / "rec.edf"
    edf.write_bytes(b"")
    path = save_ei_result(
        str(edf), ["A1", "A2"],
        np.array([1.0, 0.5]), np.array([2.0, 1.0]),
        np.array([4.0, 1.0]), np.array([1.0, 0.5]),
        diagnostics={"reference": "car"},
    )
    assert _ei_scores(path) == {"A1": 1.0, "A2": 0.5}


def test_a_dead_channel_stays_nan_through_the_production_save_path(tmp_path):
    """run_ei_compute_job always passes ei_by_contact, so that is the path that
    decides whether an undefined EI survives -- 0.0 would rank it as 'quiet' and
    shift every other contact's percentile."""
    edf = tmp_path / "rec.edf"
    edf.write_bytes(b"")
    ei = np.array([1.0, 0.5, np.nan])
    names = ["A1", "A2", "A3"]
    path = save_ei_result(
        str(edf), names, ei, ei, ei, ei,
        diagnostics={"reference": "car"},
        ei_by_contact=dict(zip(names, ei)),
    )
    loaded = _ei_scores(path)
    assert np.isnan(loaded["A3"])

    rows = {r["contact"]: r for r in fuse_contact_scores(names, {"ei": [loaded]})}
    assert np.isnan(rows["A3"]["ei_percentile"])
    assert rows["A1"]["ei_percentile"] == 1.0
    assert rows["A2"]["ei_percentile"] == 0.0


def test_duplicate_channel_names_are_refused_at_save_time(tmp_path):
    """Keying by name would keep only the last of each, silently discarding results."""
    edf = tmp_path / "rec.edf"
    edf.write_bytes(b"")
    ones = np.ones(2)
    with pytest.raises(ValueError, match="duplicate channel names"):
        save_ei_result(str(edf), ["A1", "A1"], ones, ones, ones, ones)


def test_ei_archive_without_a_contact_projection_falls_back(tmp_path):
    """Archives written before the projection existed were all CAR."""
    path = tmp_path / "old_ei.npz"
    np.savez(path, ei=np.array([1.0, 0.5]), ei_raw=np.array([2.0, 1.0]),
             hfer=np.array([4.0, 1.0]), time_coef=np.array([1.0, 0.5]),
             chn_names=np.array(["A1", "A2"]))
    assert _ei_scores(str(path)) == {"A1": 1.0, "A2": 0.5}
