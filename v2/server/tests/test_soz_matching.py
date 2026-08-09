"""Name-matching tests for the SOZ fusion step.

Contact names come from imaging (electrode segmentation or the 3D Slicer
import); channel names come from the EDF header. Nothing upstream guarantees
the two use the same convention, and a mismatch is silent by construction --
every lookup misses, every value becomes NaN, every combined score becomes 0,
and the CSV still looks well-formed. These tests pin the detection of that.
"""

import numpy as np
import pytest

from app.services.soz import (
    _by_channel,
    build_result_table,
    describe_name_overlap,
    rank_pct,
)


def _contacts(names):
    return {n: np.array([float(i), 0.0, 0.0]) for i, n in enumerate(names)}


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def test_duplicate_channel_names_are_rejected():
    """dict(zip(...)) would keep only the last, silently discarding results."""
    with pytest.raises(ValueError, match="duplicate channel names"):
        _by_channel(["A1", "A2", "A1"], [1.0, 2.0, 3.0], "EI result")


def test_mismatched_name_and_value_counts_are_rejected():
    with pytest.raises(ValueError, match="3 channel names but 2 values"):
        _by_channel(["A1", "A2", "A3"], [1.0, 2.0], "EI result")


# --------------------------------------------------------------------------
# Overlap reporting
# --------------------------------------------------------------------------

def test_overlap_reports_a_total_mismatch():
    contacts = _contacts(["A1", "A2", "B1"])
    channels = {"POL A1": 0.5, "POL A2": 0.4, "POL B1": 0.3}
    o = describe_name_overlap(contacts, channels, "EI")
    assert o["matched"] == 0
    assert o["n_contacts"] == 3
    assert o["n_channels"] == 3
    # The report must show both conventions so the mismatch is diagnosable.
    assert "A1" in o["unmatched_contacts"]
    assert "POL A1" in o["unused_channels"]


def test_overlap_reports_a_partial_match():
    contacts = _contacts(["A1", "A2", "B1"])
    channels = {"A1": 0.5, "A2": 0.4, "EKG": 0.1}
    o = describe_name_overlap(contacts, channels, "EI")
    assert o["matched"] == 2
    assert o["unmatched_contacts"] == ["B1"]
    assert o["unused_channels"] == ["EKG"]


def test_primed_electrode_names_match_exactly():
    """Bilateral implants use primed shaft names; they must survive verbatim."""
    contacts = _contacts(["X'12", "X12"])
    channels = {"X'12": 0.9, "X12": 0.1}
    assert describe_name_overlap(contacts, channels, "EI")["matched"] == 2


# --------------------------------------------------------------------------
# The failure mode the job-level guard exists to catch
# --------------------------------------------------------------------------

def test_total_mismatch_produces_a_well_formed_but_meaningless_table():
    """CHARACTERISATION of why run_soz_fuse_job refuses this case.

    Nothing in build_result_table itself errors: it returns one row per
    contact, with NaN scores and a combined score of 0 for every one. Sorting
    that is a no-op, so the output reads like a ranking and is not one.
    """
    contacts = _contacts(["A1", "A2", "B1"])
    rows = build_result_table(contacts, {"POL A1": 0.9}, {"POL A2": 5})

    assert len(rows) == 3
    assert all(np.isnan(r["ei"]) for r in rows)
    assert all(r["combined_score"] == 0 for r in rows)


def test_partial_match_ranks_only_the_matched_contacts():
    contacts = _contacts(["A1", "A2", "A3"])
    rows = build_result_table(contacts, {"A1": 0.9, "A2": 0.1}, {})
    by_name = {r["contact"]: r for r in rows}

    assert by_name["A1"]["combined_score"] > by_name["A2"]["combined_score"]
    assert by_name["A3"]["combined_score"] == 0
    assert np.isnan(by_name["A3"]["ei"])
    assert rows[0]["contact"] == "A1"


def test_hfo_absent_entirely_still_ranks_on_ei():
    """Dropping HFO must leave EI-only fusion working (see project-direction.md)."""
    contacts = _contacts(["A1", "A2"])
    rows = build_result_table(contacts, {"A1": 0.2, "A2": 0.8}, {})
    assert rows[0]["contact"] == "A2"
    assert all(np.isnan(r["hi"]) for r in rows)


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
