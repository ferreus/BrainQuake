import numpy as np
import pytest

from app.sigproc.montage import (
    apply_bipolar,
    bipolar_pairs,
    bipolar_plan,
    parse_contact,
    project_pairs_to_contacts,
)


def names(chn_names):
    return [p.name for p in bipolar_pairs(chn_names)]


def test_pairs_are_adjacent_within_shaft():
    assert names(["A1", "A2", "A3", "B1", "B2"]) == ["A1-A2", "A2-A3", "B1-B2"]


def test_numbering_gap_is_not_paired():
    # A3 excluded: pairing A2-A4 would span two contact spacings.
    assert names(["A1", "A2", "A4"]) == ["A1-A2"]


# The label styles actually present in ds004100, plus Grenoble's primed shafts.
@pytest.mark.parametrize("label,expected", [
    ("LAF1", ("LAF", 1)),
    ("RAFa12", ("RAFa", 12)),
    ("X'12", ("X'", 12)),
    ("EEG RG 01-Ref", ("RG", 1)),
    ("EEG GRID 10-Ref", ("GRID", 10)),
    ("AMFG-A2", ("AMFG-A", 2)),
    ("RAF1-3", ("RAF1", 3)),
    ("POL A5", ("A", 5)),
    ("REF", None),
    ("DC-01", ("DC", 1)),
    ("7", None),
])
def test_parse_contact_label_styles(label, expected):
    assert parse_contact(label) == expected


def test_dashed_shafts_pair_within_shaft_only():
    # AMFG-A and AMFG-B are different shafts despite sharing a prefix.
    assert names(["AMFG-A1", "AMFG-A2", "AMFG-B1", "AMFG-B2"]) == [
        "AMFG-A1-AMFG-A2", "AMFG-B1-AMFG-B2",
    ]


def test_ref_suffixed_names_pair():
    assert names(["EEG RG 01-Ref", "EEG RG 02-Ref", "EEG LG 01-Ref"]) == [
        "EEG RG 01-Ref-EEG RG 02-Ref",
    ]


def test_pairs_follow_recording_order_not_name_order():
    data = np.array([[3.0, 3.0], [1.0, 1.0]])  # A2 first in the file, then A1
    derived, pair_names, _ = apply_bipolar(data, ["A2", "A1"])
    assert pair_names == ["A1-A2"]
    np.testing.assert_allclose(derived, [[-2.0, -2.0]])


def test_bipolar_cancels_a_shared_reference():
    local = np.zeros((3, 100))
    local[0] = 5.0
    data = local + 7.0  # far field / reference on every contact
    derived, pair_names, _ = apply_bipolar(data, ["A1", "A2", "A3"])
    assert pair_names == ["A1-A2", "A2-A3"]
    np.testing.assert_allclose(derived[0], 5.0)  # local gradient survives
    np.testing.assert_allclose(derived[1], 0.0)  # shared component gone


def test_no_pairs_returns_the_data_untouched_for_the_caller_to_reference():
    data = np.arange(20, dtype=float).reshape(2, 10)
    derived, names_out, pairs = apply_bipolar(data, ["REF", "MARKER"])
    assert pairs is None
    assert names_out == ["REF", "MARKER"]
    np.testing.assert_array_equal(derived, data)


def test_projection_ignores_nan_pairs_and_omits_contacts_with_no_finite_pair():
    pairs = bipolar_pairs(["A1", "A2", "A3"])
    scores = {"A1-A2": float("nan"), "A2-A3": 0.4}
    # A1's only pair is undefined, so it is absent -- callers read absent as NaN.
    assert project_pairs_to_contacts(scores, pairs) == {"A2": 0.4, "A3": 0.4}


def test_projection_takes_each_contacts_best_pair():
    pairs = bipolar_pairs(["A1", "A2", "A3", "A4"])
    scores = {"A1-A2": 0.9, "A2-A3": 0.2, "A3-A4": 0.1}
    assert project_pairs_to_contacts(scores, pairs) == {
        "A1": 0.9, "A2": 0.9, "A3": 0.2, "A4": 0.1,
    }


def test_projection_uses_pair_membership_not_name_splitting():
    # Shaft names containing '-' make the pair name ambiguous to split.
    pairs = bipolar_pairs(["AMFG-A1", "AMFG-A2"])
    scores = {pairs[0].name: 0.7}
    assert project_pairs_to_contacts(scores, pairs) == {"AMFG-A1": 0.7, "AMFG-A2": 0.7}


def test_plan_reports_unpairable_names_and_gaps():
    plan = bipolar_plan(["A1", "A2", "A4", "B1", "REF", "MARK"])
    assert [p.name for p in plan["pairs"]] == ["A1-A2"]
    assert plan["skipped_gaps"] == [{"shaft": "A", "between": ["A2", "A4"]}]
    # REF/MARK do not parse, B1 is alone on its shaft, A4 is stranded past the gap.
    assert set(plan["unpairable"]) == {"REF", "MARK", "B1", "A4"}


def test_a_contact_stranded_by_a_gap_is_reported_unpairable():
    """Excluding A2 leaves A1 in no derivation -- it gets no EI score at all."""
    plan = bipolar_plan(["A1", "A3", "A4"])
    assert [p.name for p in plan["pairs"]] == ["A3-A4"]
    assert plan["unpairable"] == ["A1"]


def test_plan_on_a_fully_pairable_montage_reports_nothing_dropped():
    plan = bipolar_plan(["A1", "A2", "A3"])
    assert len(plan["pairs"]) == 2
    assert plan["unpairable"] == []
    assert plan["skipped_gaps"] == []
