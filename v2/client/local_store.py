"""Local on-disk cache for REST-fetched files.

The client has no filesystem access to the server (PLAN.md §2.4) -- it downloads
whatever small/medium artifacts a tab needs and keeps them here, under a
per-subject directory that plays the same role client_ictal.py/client_inter.py's
"subject_dir" convention did in the legacy app (a copy of imported edfs, downloaded
result files), just populated by REST downloads instead of local file moves.
"""
import os
import zipfile

CACHE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_cache")


def subject_dir(subject_name):
    path = os.path.join(CACHE_ROOT, subject_name)
    os.makedirs(path, exist_ok=True)
    return path


def edf_dir(subject_name):
    path = os.path.join(subject_dir(subject_name), "edf")
    os.makedirs(path, exist_ok=True)
    return path


def ensure_recon_unzipped(api, subject_id, subject_name):
    """Download+unzip the subject's recon result if not already cached, so
    surf/lh.pial, surf/rh.pial, mri/orig.mgz are available for local mayavi
    rendering (client_elec.py's vis3D, soz_result.py's plot_3d). Mirrors
    client_surf.py's mayaviplot(): the server only exposes the whole recon
    tree as one zip, not individual file downloads, so previewing anything
    from it means fetching the zip once and reading files out of it locally.
    Returns the path to the unzipped FreeSurfer subject dir (contains mri/, surf/).
    """
    sdir = subject_dir(subject_name)
    unzipped_dir = os.path.join(sdir, subject_name)
    if os.path.exists(os.path.join(unzipped_dir, "surf", "lh.pial")):
        return unzipped_dir
    zip_path = os.path.join(sdir, f"{subject_name}.zip")
    api.download_subject_zip(subject_id, zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(sdir)
    return unzipped_dir
