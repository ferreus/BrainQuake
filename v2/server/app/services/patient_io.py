"""Whole-patient export / import.

Export bundles everything a subject owns on disk into a single portable zip:
the FreeSurfer subject directory (``SUBJECTS_DIR/<name>``) and the raw-upload +
derived-results tree (``DATA_ROOT/recv/<name>`` -- T1/CT/EDF uploads plus the
EIdets/HFOdets/fslresults output the ictal/interictal/ct_register services
write there). A ``manifest.json`` at the archive root records the subject's
metadata and its Artifact rows so import can re-create them.

Import is the inverse: it unpacks those two trees back under the *current*
server's ``SUBJECTS_DIR`` / ``DATA_ROOT`` and re-registers every artifact whose
backing file made it into the archive. The subject name is preserved verbatim
-- every on-disk path and every FreeSurfer-internal reference is keyed by name,
so importing under a different name would silently break those references. A
name collision is therefore rejected (delete the existing patient first).

The archive layout mirrors the on-disk layout so paths stay valid across
servers even when SUBJECTS_DIR and DATA_ROOT are configured differently on each:

    manifest.json
    subjects/<name>/...      (from SUBJECTS_DIR/<name>)
    recv/<name>/...          (from DATA_ROOT/recv/<name>)
"""

import os
import json
import time
import zipfile
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.config import settings
from app.models import Artifact, Job, Subject
from app.services.job_control import check_cancelled

MANIFEST_NAME = "manifest.json"
FORMAT_VERSION = 1

# Archive-internal top-level prefixes. Anything else (including absolute paths
# or ".." traversal) is rejected on import as untrusted.
SUBJECTS_PREFIX = "subjects/"
RECV_PREFIX = "recv/"


def _subjects_root(name: str) -> str:
    return os.path.join(settings.SUBJECTS_DIR, name)


def _recv_root(name: str) -> str:
    return os.path.join(settings.DATA_ROOT, "recv", name)


def _arcname_for_abs(abs_path: str, name: str):
    """Map an on-disk absolute path to its archive arcname, or None if the
    file lives outside the two captured trees (e.g. the standalone recon_zip
    at SUBJECTS_DIR/<name>.zip, which the full export makes redundant)."""
    abs_path = os.path.abspath(abs_path)
    for root, prefix in ((_subjects_root(name), SUBJECTS_PREFIX),
                         (_recv_root(name), RECV_PREFIX)):
        root_abs = os.path.abspath(root)
        if abs_path == root_abs or abs_path.startswith(root_abs + os.sep):
            rel = os.path.relpath(abs_path, os.path.dirname(root_abs))  # "<name>/<sub...>"
            return _join_arc(prefix, rel)
    return None


def _join_arc(prefix: str, rel: str) -> str:
    # prefix already ends with "/"; rel is "<name>/<sub...>" with OS separators
    return prefix + rel.replace(os.sep, "/")


def _abs_for_arcname(arcname: str, name: str):
    """Inverse of _arcname_for_abs: map an archive member back to the absolute
    path it should extract to on *this* server. Returns None for members that
    are neither under subjects/ nor recv/ (e.g. the manifest)."""
    if arcname.startswith(SUBJECTS_PREFIX):
        rel = arcname[len(SUBJECTS_PREFIX):]  # "<name>/<sub...>"
        return os.path.join(settings.SUBJECTS_DIR, *rel.split("/"))
    if arcname.startswith(RECV_PREFIX):
        rel = arcname[len(RECV_PREFIX):]
        return os.path.join(settings.DATA_ROOT, "recv", *rel.split("/"))
    return None


def _is_safe_member(arcname: str, name: str) -> bool:
    """Zip-slip guard: a member is safe only if it is the manifest or resolves
    (via _abs_for_arcname) to a path genuinely contained in this subject's
    SUBJECTS_DIR/<name> or recv/<name> tree."""
    if arcname == MANIFEST_NAME:
        return True
    if arcname.endswith("/"):
        # directory entry -- validate its non-trailing form
        arcname = arcname.rstrip("/")
        if not arcname:
            return False
    if not (arcname.startswith(SUBJECTS_PREFIX) or arcname.startswith(RECV_PREFIX)):
        return False
    dest = _abs_for_arcname(arcname, name)
    if dest is None:
        return False
    dest_abs = os.path.abspath(dest)
    for root in (_subjects_root(name), _recv_root(name)):
        root_abs = os.path.abspath(root)
        if dest_abs == root_abs or dest_abs.startswith(root_abs + os.sep):
            return True
    return False


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _collect_files(name: str):
    """Yield (abs_path, arcname) for every file under the subject's two trees."""
    for root_dir, prefix in ((_subjects_root(name), SUBJECTS_PREFIX),
                             (_recv_root(name), RECV_PREFIX)):
        if not os.path.isdir(root_dir):
            continue
        parent = os.path.dirname(os.path.abspath(root_dir))
        for dirpath, _dirnames, filenames in os.walk(root_dir):
            for fn in filenames:
                abs_path = os.path.join(dirpath, fn)
                rel = os.path.relpath(abs_path, parent)  # "<name>/<sub...>"
                yield abs_path, _join_arc(prefix, rel)


def _build_manifest(db: Session, subject: Subject) -> dict:
    artifacts = db.query(Artifact).filter(Artifact.subject_id == subject.id).all()
    entries = []
    for a in artifacts:
        abs_path = os.path.join(settings.DATA_ROOT, a.rel_path)
        arcname = _arcname_for_abs(abs_path, subject.name)
        if arcname is None:
            # File lives outside the captured trees (e.g. recon_zip); it is not
            # in the archive, so don't promise import a file it can't restore.
            continue
        entries.append({"kind": a.kind, "arcname": arcname, "meta_json": a.meta_json})
    return {
        "format_version": FORMAT_VERSION,
        "name": subject.name,
        "recon_type": subject.recon_type,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": entries,
    }


def run_export_patient_job(db: Session, job: Job, log_file):
    subject = db.query(Subject).filter(Subject.id == job.subject_id).first()
    if not subject:
        raise ValueError("Subject not found")
    name = subject.name

    if not os.path.isdir(_subjects_root(name)) and not os.path.isdir(_recv_root(name)):
        raise FileNotFoundError(
            f"No data found on disk for subject '{name}' (neither "
            f"{_subjects_root(name)} nor {_recv_root(name)} exists)."
        )

    job.progress_pct = 5.0
    job.progress_message = "Scanning patient files"
    db.commit()

    files = list(_collect_files(name))
    manifest = _build_manifest(db, subject)
    log_file.write(f"Exporting {len(files)} files for subject '{name}'.\n")
    log_file.flush()

    exports_dir = os.path.join(settings.DATA_ROOT, "exports")
    os.makedirs(exports_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(exports_dir, f"{name}_export_{ts}.zip")

    total = max(len(files), 1)
    t0 = time.time()
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
        for i, (abs_path, arcname) in enumerate(files):
            zf.write(abs_path, arcname)
            if i % 50 == 0:
                check_cancelled(db, job)
                job.progress_pct = 5.0 + 90.0 * (i / total)
                job.progress_message = f"Zipping {i + 1}/{total} files"
                db.commit()

    size = os.path.getsize(out_path)
    log_file.write(f"Wrote {out_path} ({size} bytes) in {time.time() - t0:.1f}s.\n")

    # Supersede any previous export artifact + its file so download always
    # serves the newest and stale zips don't accumulate on disk.
    old_exports = (
        db.query(Artifact)
        .filter(Artifact.subject_id == subject.id, Artifact.kind == "patient_export")
        .all()
    )
    for old in old_exports:
        old_abs = os.path.join(settings.DATA_ROOT, old.rel_path)
        if os.path.abspath(old_abs) != os.path.abspath(out_path) and os.path.exists(old_abs):
            try:
                os.remove(old_abs)
            except OSError:
                pass
        db.delete(old)
    db.commit()

    rel_path = os.path.relpath(out_path, settings.DATA_ROOT)
    artifact = Artifact(
        subject_id=subject.id,
        job_id=job.id,
        kind="patient_export",
        rel_path=rel_path,
        meta_json={"filename": os.path.basename(out_path), "file_count": len(files), "bytes": size},
    )
    db.add(artifact)
    db.commit()

    job.progress_pct = 100.0
    job.progress_message = f"Export ready ({size // (1024 * 1024)} MB)"
    db.commit()


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def read_import_manifest(zip_path: str) -> dict:
    """Read and validate manifest.json out of an uploaded export zip, without
    extracting the (potentially multi-GB) payload. Raises ValueError with a
    user-facing message for anything that isn't a BrainQuake export."""
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("Uploaded file is not a zip archive.")
    with zipfile.ZipFile(zip_path, "r") as zf:
        try:
            raw = zf.read(MANIFEST_NAME)
        except KeyError:
            raise ValueError("Not a BrainQuake patient export (missing manifest.json).")
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("Patient export manifest.json is corrupt.")
    if not manifest.get("name"):
        raise ValueError("Patient export manifest.json has no subject name.")
    return manifest


def run_import_patient_job(db: Session, job: Job, log_file):
    subject = db.query(Subject).filter(Subject.id == job.subject_id).first()
    if not subject:
        raise ValueError("Subject not found")
    name = subject.name

    params = job.params_json or {}
    zip_path = params.get("zip_path")
    if not zip_path or not os.path.exists(zip_path):
        raise FileNotFoundError(f"Uploaded import archive not found at {zip_path}")

    manifest = read_import_manifest(zip_path)
    if manifest.get("name") != name:
        raise ValueError(
            f"Archive is for subject '{manifest.get('name')}', but this import "
            f"was set up for '{name}'."
        )

    job.progress_pct = 10.0
    job.progress_message = "Extracting patient files"
    db.commit()

    os.makedirs(_recv_root(name), exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.infolist()
        total = max(len(members), 1)
        for i, info in enumerate(members):
            arcname = info.filename
            if arcname == MANIFEST_NAME:
                continue
            if not _is_safe_member(arcname, name):
                raise ValueError(f"Unsafe path in archive rejected: {arcname!r}")
            if arcname.endswith("/"):
                continue  # directory entry; parents are created per-file below
            dest = _abs_for_arcname(arcname, name)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                out.write(src.read())
            if i % 50 == 0:
                check_cancelled(db, job)
                job.progress_pct = 10.0 + 80.0 * (i / total)
                job.progress_message = f"Extracting {i + 1}/{total} files"
                db.commit()

    job.progress_pct = 92.0
    job.progress_message = "Re-registering artifacts"
    db.commit()

    registered = 0
    for entry in manifest.get("artifacts", []):
        arcname = entry.get("arcname")
        if not arcname:
            continue
        abs_path = _abs_for_arcname(arcname, name)
        if abs_path is None or not os.path.exists(abs_path):
            continue
        rel_path = os.path.relpath(abs_path, settings.DATA_ROOT)
        db.add(Artifact(
            subject_id=subject.id,
            job_id=job.id,
            kind=entry.get("kind", "unknown"),
            rel_path=rel_path,
            meta_json=entry.get("meta_json") or {},
        ))
        registered += 1

    # Point the subject at its restored FreeSurfer directory so the recon-gated
    # features (electrodes tab, surface view) light up, exactly as a local recon
    # would have. Only claim it if the tree actually came back.
    if os.path.isdir(_subjects_root(name)):
        subject.subject_dir = _subjects_root(name)
        db.add(subject)

    db.commit()
    log_file.write(f"Extracted archive and registered {registered} artifacts.\n")

    # The uploaded archive was a transient staging copy; the payload now lives
    # in its real on-disk home, so reclaim the space.
    try:
        os.remove(zip_path)
    except OSError:
        pass

    job.progress_pct = 100.0
    job.progress_message = f"Import complete ({registered} artifacts restored)"
    db.commit()
