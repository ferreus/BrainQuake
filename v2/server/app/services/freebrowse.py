import os

from app.config import settings
from app.models import Subject

# Backs the FreeBrowse tab (v2/freebrowse, a vendored, unmodified copy of
# https://github.com/freesurfer/freebrowse). FreeBrowse already knows how to
# load a NiiVue document (".nvd", plain JSON) given via a `?nvd=<url>` query
# param (frontend/src/hooks/use-file-loading.ts) and fetch every volume/mesh
# `url` in it directly -- so instead of a manual "open file" step, this
# module builds that document on the fly from whichever of a subject's
# FreeSurfer outputs already exist on disk, and serves them.
#
# Keys below are stable, arbitrary identifiers -- deliberately NOT the real
# on-disk filenames -- passed in the URL as e.g. .../freebrowse/files/orig.
# The file-serving endpoint looks a key up in these dicts and never joins
# unvalidated input onto a filesystem path, which is what makes it safe
# despite taking a string from the URL.

FREEBROWSE_VOLUMES = {
    "orig": {
        "rel_path": "mri/orig.mgz", "name": "orig.mgz",
        "colormap": "gray", "opacity": 1, "visible": True,
    },
    "brainmask": {
        "rel_path": "mri/brainmask.mgz", "name": "brainmask.mgz",
        "colormap": "gray", "opacity": 1, "visible": False,
    },
    "aseg": {
        "rel_path": "mri/aseg.mgz", "name": "aseg.mgz",
        "colormap": "freesurfer", "opacity": 0.7, "visible": False,
    },
    "ct_reg": {
        # ct_register.py's run_ct_register_job writes the registered CT to
        # SUBJECTS_DIR/<patient>/fslresults/<patient>CT_Reg.nii.gz -- the
        # only entry here whose relative path depends on the subject name.
        "rel_path": lambda name: f"fslresults/{name}CT_Reg.nii.gz",
        "name": "CT (registered).nii.gz",
        "colormap": "gray", "opacity": 0.7, "visible": False,
    },
}

FREEBROWSE_MESHES = {
    "lh_pial": {
        "rel_path": "surf/lh.pial", "name": "lh.pial",
        "rgba255": [255, 255, 255, 255], "meshShaderIndex": 14,
    },
    "rh_pial": {
        "rel_path": "surf/rh.pial", "name": "rh.pial",
        "rgba255": [255, 255, 255, 255], "meshShaderIndex": 14,
    },
    "lh_white": {
        "rel_path": "surf/lh.white", "name": "lh.white",
        "rgba255": [255, 255, 0, 255], "meshShaderIndex": 14,
    },
    "rh_white": {
        "rel_path": "surf/rh.white", "name": "rh.white",
        "rgba255": [255, 255, 0, 255], "meshShaderIndex": 14,
    },
}


def _resolve_rel_path(entry, subject_name):
    rel_path = entry["rel_path"]
    return rel_path(subject_name) if callable(rel_path) else rel_path


def _abs_path(subject: Subject, entry):
    rel_path = _resolve_rel_path(entry, subject.name)
    return os.path.join(settings.SUBJECTS_DIR, subject.name, rel_path)


def build_nvd_document(subject: Subject):
    """Only whitelisted files that actually exist on disk are included --
    a subject mid-recon (or one that never had a CT registered) just gets a
    smaller document, not an error.

    Every url below is fetched directly by the browser (FreeBrowse's own
    fetch(), not v2/web's api client) once this document loads, so -- unlike
    every other server-side path in this codebase, which is mounted at
    /subjects/... with no prefix -- these must include the /api/ prefix
    nginx's `location /api/` proxy strips before forwarding to this app (see
    v2/docker/nginx.conf and v2/web/src/api/client.ts's API_BASE). Getting
    this wrong doesn't 404 loudly -- the request instead falls through
    nginx's SPA location and silently returns index.html."""
    base_url = f"/api/subjects/{subject.id}/freebrowse/files"

    image_options_array = []
    for key, entry in FREEBROWSE_VOLUMES.items():
        if not os.path.exists(_abs_path(subject, entry)):
            continue
        image_options_array.append({
            "url": f"{base_url}/{key}",
            "name": entry["name"],
            "colormap": entry["colormap"],
            "opacity": entry["opacity"],
            "visible": entry["visible"],
        })

    meshes = []
    for key, entry in FREEBROWSE_MESHES.items():
        if not os.path.exists(_abs_path(subject, entry)):
            continue
        meshes.append({
            "url": f"{base_url}/{key}",
            "name": entry["name"],
            "rgba255": entry["rgba255"],
            "meshShaderIndex": entry["meshShaderIndex"],
        })

    return {
        "title": f"{subject.name} (BrainQuake)",
        "imageOptionsArray": image_options_array,
        "meshes": meshes,
    }


def resolve_file_path(subject: Subject, key: str):
    """Returns (abs_path, display_name) for a whitelisted key. Raises
    KeyError if `key` isn't a known volume/mesh, FileNotFoundError if it is
    but the file doesn't exist for this subject."""
    entry = FREEBROWSE_VOLUMES.get(key) or FREEBROWSE_MESHES.get(key)
    if entry is None:
        raise KeyError(key)
    abs_path = _abs_path(subject, entry)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(abs_path)
    return abs_path, entry["name"]
