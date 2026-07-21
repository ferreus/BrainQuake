import os
import shutil
from app.config import settings
from app.models import Artifact, Subject


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
