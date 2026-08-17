"""SEEG re-referencing montages. numpy + stdlib only, so ei.py stays importable
without the server stack."""
import logging
import math
import re
from typing import NamedTuple

import numpy as np

logger = logging.getLogger(__name__)

# Trailing digits are the contact number; everything before is the shaft. Looser
# than channels.DEFAULT_SEEG_CONTACT_PATTERN on purpose -- that one decides
# "contact or aux trace?", this one only splits an already-chosen contact, so it
# has to cope with every amplifier's label style at once:
#   LAF1 -> (LAF, 1)          EEG RG 01-Ref -> (RG, 1)
#   X'12 -> (X', 12)          AMFG-A2       -> (AMFG-A, 2)
# Shafts can therefore contain '-', which is why pair membership is carried
# explicitly below instead of being recovered by splitting the pair name.
_CONTACT_RE = re.compile(r"^(?P<shaft>.*?)(?P<num>\d+)$")
_PREFIX_RE = re.compile(r"^\s*(EEG|SEEG|POL)\s+", re.IGNORECASE)
_SUFFIX_RE = re.compile(r"\s*-\s*(Ref|REF)\s*$")

PAIR_SEP = "-"


def parse_contact(name):
    """(shaft, number) for a contact label, or None if it does not parse."""
    text = _SUFFIX_RE.sub("", _PREFIX_RE.sub("", str(name))).strip()
    m = _CONTACT_RE.match(text)
    if not m:
        return None
    shaft = m.group("shaft").strip().rstrip(PAIR_SEP + " _")
    return (shaft, int(m.group("num"))) if shaft else None


class BipolarPair(NamedTuple):
    name: str
    idx_a: int
    idx_b: int
    contact_a: str
    contact_b: str


def bipolar_plan(chn_names):
    """What a bipolar montage over `chn_names` would build, without building it.

    Returns {pairs, unpairable, skipped_gaps}. Only consecutive contact numbers
    pair: a gap means a contact was excluded or never recorded, and differencing
    across it spans two contact spacings -- a different physical measurement
    wearing the same kind of name.
    """
    by_shaft = {}
    for i, name in enumerate(chn_names):
        parsed = parse_contact(name)
        if parsed:
            by_shaft.setdefault(parsed[0], []).append((parsed[1], i, name))

    pairs = []
    skipped_gaps = []
    for shaft in sorted(by_shaft):
        contacts = sorted(by_shaft[shaft])
        for (n_a, i_a, name_a), (n_b, i_b, name_b) in zip(contacts, contacts[1:]):
            if n_b == n_a + 1:
                pairs.append(BipolarPair(f"{name_a}{PAIR_SEP}{name_b}", i_a, i_b, name_a, name_b))
            else:
                skipped_gaps.append({"shaft": shaft, "between": [name_a, name_b]})

    paired = {c for p in pairs for c in (p.contact_a, p.contact_b)}
    unpairable = [n for n in chn_names if n not in paired]
    if unpairable:
        logger.info("%d channel(s) cannot be paired: %s", len(unpairable), unpairable[:10])
    if skipped_gaps:
        logger.info("%d pair(s) skipped: numbering gap between them", len(skipped_gaps))
    return {"pairs": pairs, "unpairable": unpairable, "skipped_gaps": skipped_gaps}


def bipolar_pairs(chn_names):
    """Adjacent same-shaft contact pairs, in shaft/contact order."""
    return bipolar_plan(chn_names)["pairs"]


def apply_bipolar(data, chn_names):
    """Bipolar re-reference. Returns (derived_data, pair_names, pairs).

    Cancels the shared reference and the volume-conducted far field, leaving the
    local gradient -- which is what makes a per-contact ranking mean anything.

    `pairs` is None when nothing could be paired: the data comes back untouched
    for the caller to reference some other way.
    """
    pairs = bipolar_pairs(chn_names)
    if not pairs:
        logger.warning(
            "no adjacent same-shaft contact pairs among %d channels, so no bipolar "
            "montage is possible: %s", len(chn_names), list(chn_names)[:6]
        )
        return np.asarray(data), list(chn_names), None
    data = np.asarray(data)
    derived = data[[p.idx_a for p in pairs]] - data[[p.idx_b for p in pairs]]
    logger.info("bipolar: %d contacts -> %d derivations", len(chn_names), len(pairs))
    return derived, [p.name for p in pairs], pairs


def project_pairs_to_contacts(scores_by_pair, pairs):
    """Pair scores -> per-contact scores, taking each contact's best pair.

    Everything downstream (SOZ fusion, the 3D view, dataset ground truth) is
    keyed by contact. Max rather than mean: a hot pair should not be diluted by
    the quiet one on its other side.
    """
    out = {}
    for pair in pairs:
        score = scores_by_pair.get(pair.name)
        # NaN compares False against everything below, so order would decide the winner.
        # A contact with no finite pair is left out; callers read absent as NaN.
        if score is None or not math.isfinite(score):
            continue
        for contact in (pair.contact_a, pair.contact_b):
            if contact not in out or score > out[contact]:
                out[contact] = score
    return out
