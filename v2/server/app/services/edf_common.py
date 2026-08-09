import os
import re
import shutil

from app.config import settings
from app.models import Artifact, Subject

# An SEEG contact label is a shaft letter, optionally primed for the
# contralateral shaft, then the contact number -- A1, D6, X'12, G'10. Anything
# else in the file is an auxiliary trace.
#
# Identifying contacts positively rather than blacklisting known auxiliary
# names is what makes this reliable: a blacklist has to anticipate every
# amplifier's naming (REF1, DC01, EKG1, UNUSED248 and a bare E all appear in
# one Nihon Kohden export), while the contact convention is fixed by the
# implantation scheme.
_SEEG_CONTACT_RE = re.compile(r"^[A-Za-z]'?\d+$")


def find_non_seeg_channels(chn_names):
    """Indices of channels that are not SEEG contacts.

    These matter twice over: they are ranked as if they were contacts, and --
    worse -- every module re-references to the mean across the channels it is
    given, so an auxiliary trace leaks into every SEEG channel through the
    common-average reference. Scale makes that decisive rather than cosmetic:
    in a Nihon Kohden export the DC inputs are stored in mV and the mark word
    carries no unit at all, so they arrive 10^3-10^6 times larger than a
    microvolt-scale contact and dominate the average outright.

    Lives here rather than in ictal.py because the ictal, interictal and
    display paths all need the same answer.
    """
    return [i for i, n in enumerate(chn_names) if not _SEEG_CONTACT_RE.match(str(n).strip())]


def resolve_edf_path(subject: Subject, artifact: Artifact) -> str:
    """Copies the uploaded edf into <subject_dir>/edf/ (if not already there)
    so downstream results land next to it under edf/EIdets/ or edf/HFOdets/,
    matching the convention services/soz.py's fusion step expects. Shared by
    ictal.py, interictal.py, and edf.py -- previously duplicated verbatim as
    `_ensure_edf_copy` in both ictal.py and interictal.py."""
    src_path = os.path.join(settings.DATA_ROOT, artifact.rel_path)
    edf_dir = os.path.join(settings.SUBJECTS_DIR, subject.name, "edf")
    os.makedirs(edf_dir, exist_ok=True)
    dest_path = os.path.join(edf_dir, os.path.basename(src_path))
    if not os.path.exists(dest_path):
        shutil.copy2(src_path, dest_path)
    return dest_path
