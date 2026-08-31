"""Per-file scalp channel contract. mne + stdlib only.

The montage is not fixed across recordings: three vendors and several years give
10-10 names, legacy 10-20 names, `-<ref>` suffixes and different aux sets. Every
label is discovered and mapped per file, and anything unmapped is reported
rather than dropped.

**Never route scalp data through `channels.seeg_contacts`.**
`DEFAULT_SEEG_CONTACT_PATTERN` matches `F3`, `C4`, `T7`, `A1`, and this subject's
SEEG shafts are literally named F, T, P, A -- the two paths would silently agree
on the wrong channels.
"""
import logging
import re

import mne

logger = logging.getLogger(__name__)

# Legacy 10-20 -> modern 10-10. Verified co-located in standard_1005 (0.000 mm),
# so this is a rename, not a move. Canonicalising means two studies that name the
# same electrode differently still compare.
LEGACY_ALIASES = {"t3": "T7", "t4": "T8", "t5": "P7", "t6": "P8"}

# T1/T2 exist in no MNE standard montage. FT9/FT10 are their conventional 10-10
# equivalents; FT9 is left (x = -84.1 mm), FT10 right. A left/right swap here
# would invert every lateralisation result, so it is pinned by test.
T1T2_ALIASES = {"t1": "FT9", "t2": "FT10"}

# Non-EEG traces. Matched as prefixes because vendors append indices and polarity
# ("EKG1", "EMG1+-EMG1-", "Events/Markers", "EEG Mark1").
AUX_PREFIXES = (
    "ekg", "ecg", "emg", "eog", "loc", "roc", "png", "resp", "thor", "abdo",
    "xyz", "mkr", "mark", "event", "spo2", "etco2", "co2", "pulse", "belt",
    "dc", "ref", "nr", "trig", "stim", "photic", "annotation",
)
# Whole-name aux: Nihon Kohden's derived/reference traces and a bare mark input.
AUX_EXACT = {"e", "av", "sd", "bn", "aav", "0v", "x1", "x2", "x3"}

_STD_1005 = None


def _std_lookup():
    """lowercase name -> canonical standard_1005 name. Built once."""
    global _STD_1005
    if _STD_1005 is None:
        m = mne.channels.make_standard_montage("standard_1005")
        _STD_1005 = {n.lower(): n for n in m.ch_names}
    return _STD_1005


def _base(name):
    """Strip an `EEG ` prefix and surrounding whitespace."""
    return re.sub(r"^EEG\s+", "", str(name).strip(), flags=re.I)


def is_aux(name):
    b = _base(name).lower()
    return b in AUX_EXACT or b.startswith(AUX_PREFIXES)


def normalize_labels(edf_ch_names, sidecar=None):
    """{edf_label: canonical standard_1005 name or None}.

    Tries the label as written, then with a `-<ref>` suffix removed -- the
    sidecar's `channels[].reference` names that suffix when one is present, so
    the suffix carries no information the montage needs.
    """
    std = _std_lookup()
    refs = _sidecar_refs(sidecar)
    out = {}
    for name in edf_ch_names:
        canon = None
        if not is_aux(name):
            b = _base(name)
            cands = [b]
            ref = refs.get(str(name).strip())
            if ref and b.lower().endswith("-" + ref.lower()):
                cands.append(b[: -(len(ref) + 1)])
            elif "-" in b:
                cands.append(b.split("-", 1)[0])
            for c in cands:
                key = c.lower()
                canon = LEGACY_ALIASES.get(key) or T1T2_ALIASES.get(key) or std.get(key)
                if canon:
                    break
        out[name] = canon
    return out


def _sidecar_refs(sidecar):
    """{edf_label: reference} from an eeg2edf-sidecar/1, or {}."""
    if not sidecar:
        return {}
    return {c["edf_label"]: c.get("reference")
            for c in sidecar.get("channels", []) if c.get("edf_label")}


def classify_channels(edf_ch_names, sidecar=None):
    """(eeg, aux, unknown) as the file's own labels.

    `eeg` are the labels `normalize_labels` resolved to a scalp position; the
    rest are split into recognised auxiliaries and everything else. Unknown
    labels are returned, never folded into either -- a label nobody recognises is
    a normalizer gap to fix, not a channel to guess at.
    """
    mapping = normalize_labels(edf_ch_names, sidecar)
    eeg, aux, unknown = [], [], []
    for name in edf_ch_names:
        if mapping[name]:
            eeg.append(name)
        elif is_aux(name):
            aux.append(name)
        else:
            unknown.append(name)
    if unknown:
        logger.warning("unmapped scalp labels (excluded, not guessed): %s", unknown)
    return eeg, aux, unknown


# Ordered by how well the pair actually samples inferior temporal cortex. The
# first tier the file can satisfy wins, so the contrast degrades with the
# montage instead of failing on it.
LATERALITY_TIERS = (
    ("inferior chain F9/T9/P9", ["F9", "T9", "P9"], ["F10", "T10", "P10"]),
    ("inferior 10-10 FT9/TP9", ["FT9", "TP9"], ["FT10", "TP10"]),
    ("anterior+mid temporal FT9/T7/P7", ["FT9", "T7", "P7"], ["FT10", "T8", "P8"]),
    ("mid temporal T7/P7", ["T7", "P7"], ["T8", "P8"]),
)


# Left/right pairs by region. A single preselected contrast assumes where the
# onset is; reporting every region the montage supports does not.
REGION_PAIRS = (
    ("frontopolar", ["Fp1"], ["Fp2"]),
    ("frontal", ["F3", "F7"], ["F4", "F8"]),
    ("central", ["C3"], ["C4"]),
    ("mid-temporal", ["T7"], ["T8"]),
    ("inferior-temporal", ["FT9", "TP9"], ["FT10", "TP10"]),
    ("inferior-chain", ["F9", "T9", "P9"], ["F10", "T10", "P10"]),
    ("parietal", ["P3", "P7"], ["P4", "P8"]),
    ("occipital", ["O1"], ["O2"]),
    ("ear", ["A1"], ["A2"]),
)


def region_contrasts(canonical_names):
    """[(region, left, right)] for every region this montage fully supports.

    Both sides must be complete, so the contrast stays a mirror image and an
    asymmetric montage cannot manufacture a lateralisation.
    """
    have = set(canonical_names)
    return [(name, list(left), list(right)) for name, left, right in REGION_PAIRS
            if have.issuperset(left) and have.issuperset(right)]


def laterality_chains(canonical_names):
    """(tier_name, left, right) -- the best left/right temporal contrast available.

    Raises if the file carries no temporal chain at all: a lateralisation
    computed from whatever happens to be present is worse than none.
    """
    have = set(canonical_names)
    for name, left, right in LATERALITY_TIERS:
        if have.issuperset(left) and have.issuperset(right):
            return name, list(left), list(right)
    raise ValueError(
        "no left/right temporal chain in this montage; have "
        f"{sorted(have)}. Tiers tried: {[t[0] for t in LATERALITY_TIERS]}"
    )
