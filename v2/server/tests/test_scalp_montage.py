"""Unit tests for the per-file scalp channel contract.

The normalizer is the one module every recording passes through, so it is the
one that must not guess. Labels below are the real ones from Bella's three
scalp studies (Nicolet 2022, Nihon Kohden 2024, Micromed 2025).
"""

import pytest

from app.sigproc.channels import seeg_contacts
from app.sigproc.scalp_montage import (
    classify_channels,
    laterality_chains,
    normalize_labels,
)

ICHILOV = ["Fp2", "F4", "C4", "P4", "O2", "F8", "FT10", "T8", "P8", "Fz", "Cz",
           "Pz", "Fp1", "F3", "C3", "P3", "O1", "F7", "FT9", "T7", "P7", "EKG",
           "F10", "T10", "P10", "F9", "T9", "P9", "Oz", "A1", "A2"]

CLEVELAND = ["FZ", "CZ", "PZ", "FP1", "F3", "C3", "P3", "O1", "F7", "T7", "TP9",
             "NR1", "FP2", "F4", "C4", "P4", "O2", "E", "F8", "T8", "P7", "FT9",
             "P8", "FT10", "TP10", "NR2", "EKG1", "EKG2", "EEG Mark1",
             "EEG Mark2", "Events/Markers"]

SHEBA = ["Fp1-G2", "T1-G2", "Fp2-G2", "F7-G2", "F3-G2", "Fz-G2", "F4-G2",
         "F8-G2", "T3-G2", "C3-G2", "Cz-G2", "C4-G2", "T4-G2", "T5-G2", "P3-G2",
         "Pz-G2", "P4-G2", "T6-G2", "O1-G2", "T2-G2", "O2-G2", "A1-G2", "A2-G2",
         "elA24-G2", "EMG1+-EMG1-", "EMG2+-EMG2-", "EMG3+-EMG3-", "PNG1+-PNG1-",
         "PNG2+-PNG2-", "PNG3+-PNG3-", "ECG1+-ECG1-", "ECG2+-ECG2-",
         "thor+-thor-", "abdo+-abdo-", "xyz+-xyz-", "MKR+-MKR-"]


def canonical(names, sidecar=None):
    m = normalize_labels(names, sidecar)
    eeg, _, _ = classify_channels(names, sidecar)
    return [m[n] for n in eeg]


def test_legacy_1020_names_resolve():
    """T3/T4/T5/T6, `-G2` suffixes and Cleveland's all-caps all land on positions."""
    m = normalize_labels(SHEBA)
    assert m["T3-G2"] == "T7"
    assert m["T4-G2"] == "T8"
    assert m["T5-G2"] == "P7"
    assert m["T6-G2"] == "P8"
    assert m["Fp1-G2"] == "Fp1"

    # Cleveland writes FZ/FP1; case-folding is load-bearing, not cosmetic.
    c = normalize_labels(CLEVELAND)
    assert c["FZ"] == "Fz"
    assert c["FP1"] == "Fp1"
    assert c["FP2"] == "Fp2"


def test_t1_t2_map_to_ft9_ft10():
    """Pins the substitution *and its side*.

    FT9 is left (x = -84.1 mm in standard_1005), FT10 right. Swapping these
    inverts every lateralisation result silently, which is the whole question
    the scalp analysis exists to answer.
    """
    m = normalize_labels(SHEBA)
    assert m["T1-G2"] == "FT9"
    assert m["T2-G2"] == "FT10"

    import mne
    pos = mne.channels.make_standard_montage("standard_1005").get_positions()["ch_pos"]
    assert pos["FT9"][0] < 0 < pos["FT10"][0]


def test_unmapped_labels_are_reported_not_dropped():
    """Nothing unrecognised may reach the eeg list, and it must still be visible."""
    eeg, aux, unknown = classify_channels(SHEBA)
    assert unknown == ["elA24-G2"]
    assert "elA24-G2" not in eeg and "elA24-G2" not in aux
    for a in ("EMG1+-EMG1-", "PNG1+-PNG1-", "ECG1+-ECG1-", "thor+-thor-",
              "abdo+-abdo-", "MKR+-MKR-"):
        assert a in aux

    eeg_c, aux_c, unknown_c = classify_channels(CLEVELAND)
    assert unknown_c == []
    for a in ("NR1", "NR2", "E", "EKG1", "EEG Mark1", "Events/Markers"):
        assert a in aux_c
    assert all(x not in eeg_c for x in ("NR1", "NR2", "E"))

    # Every kept channel resolves; that is what lets set_montage use on_missing="raise".
    m = normalize_labels(SHEBA)
    assert all(m[n] for n in eeg)


def test_laterality_chains_degrade_without_inferior_chain():
    """Each montage gets the best contrast it can support; none gets a guess."""
    tier, left, right = laterality_chains(canonical(ICHILOV))
    assert left == ["F9", "T9", "P9"] and right == ["F10", "T10", "P10"]

    # Cleveland has no F9/T9/P9, but FT9/TP9 still sample inferior temporal.
    tier_c, left_c, right_c = laterality_chains(canonical(CLEVELAND))
    assert left_c == ["FT9", "TP9"] and right_c == ["FT10", "TP10"]

    # Sheba reaches inferior temporal only through T1/T2 -> FT9/FT10.
    tier_s, left_s, right_s = laterality_chains(canonical(SHEBA))
    assert left_s == ["FT9", "T7", "P7"] and right_s == ["FT10", "T8", "P8"]

    # Left and right must stay mirror images, or the contrast is biased.
    for l, r in ((left, right), (left_c, right_c), (left_s, right_s)):
        assert len(l) == len(r)

    with pytest.raises(ValueError):
        laterality_chains(["Fz", "Cz", "Pz", "O1", "O2"])


def test_sidecar_reference_is_used_when_present():
    """The sidecar's `reference` names the suffix to strip.

    For a single-dash label the sidecar and plain string-splitting agree, so this
    guards the mechanism rather than a difference in outcome; the sidecar matters
    because it states the recording reference without anyone having to guess.
    """
    sidecar = {"channels": [{"edf_label": "Fp1-G2", "reference": "G2"},
                            {"edf_label": "T1-G2", "reference": "G2"}]}
    m = normalize_labels(["Fp1-G2", "T1-G2"], sidecar)
    assert m == {"Fp1-G2": "Fp1", "T1-G2": "FT9"}
    assert normalize_labels(["Fp1-G2", "T1-G2"]) == m


def test_scalp_montage_excludes_seeg_lookalikes():
    """Regression guard: the SEEG path would claim scalp electrodes.

    channels.DEFAULT_SEEG_CONTACT_PATTERN matches F3/C4/T7/A1, and this subject's
    SEEG shafts are named F, T, P, A. Pinned so nobody "simplifies" the two
    paths together later.
    """
    wrongly_claimed = seeg_contacts(ICHILOV)
    for n in ("F3", "C4", "T7", "A1", "P9"):
        assert n in wrongly_claimed

    eeg, _, _ = classify_channels(ICHILOV)
    assert eeg == [n for n in ICHILOV if n != "EKG"]
    assert len(eeg) == 30


def test_region_contrasts_are_mirrored_and_montage_dependent():
    """Every reported region must have matching left/right, or it biases the contrast.

    A single preselected chain assumes where the onset is. Bella's scalp onsets
    are parietal while the plan's chain was inferior-temporal, which is why the
    contrast has to be reported per region rather than chosen in advance.
    """
    from app.sigproc.scalp_montage import region_contrasts

    for labels in (ICHILOV, CLEVELAND, SHEBA):
        for name, left, right in region_contrasts(canonical(labels)):
            assert len(left) == len(right), name
            assert left != right

    ich = dict((n, (l, r)) for n, l, r in region_contrasts(canonical(ICHILOV)))
    assert "inferior-chain" in ich          # only Ichilov carries F9/T9/P9
    assert ich["parietal"] == (["P3", "P7"], ["P4", "P8"])

    cle = dict((n, (l, r)) for n, l, r in region_contrasts(canonical(CLEVELAND)))
    assert "inferior-chain" not in cle
    assert "inferior-temporal" in cle       # FT9/TP9 instead

    # A montage with only one side of a region must not report that region.
    assert region_contrasts(["P3", "P7", "Fz"]) == []
