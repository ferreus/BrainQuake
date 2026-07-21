import os
import struct
import numpy as np
import nibabel.freesurfer as fsio
from sqlalchemy.orm import Session
from app.config import settings
from app.models import Artifact, Job, Subject
from app.services.recon import register_artifact

# Browsers can't parse FreeSurfer's binary surface format, and there's no
# gltf-writer dependency in this codebase to re-encode it -- this is a
# minimal custom binary format instead: 8-byte magic, vertex/face counts,
# then raw little-endian float32 vertex + uint32 face buffers, directly
# consumable as typed arrays by a THREE.BufferGeometry (see
# v2/web/src/lib/parseSurfaceBinary.ts for the matching reader).
MAGIC = b"BQSURF01"

HEMISPHERES = ("lh", "rh")


def surface_to_binary(pial_path: str) -> bytes:
    vertices, faces = fsio.read_geometry(pial_path)  # (N,3) float64, (M,3) int32
    v = np.ascontiguousarray(vertices, dtype="<f4")
    f = np.ascontiguousarray(faces, dtype="<u4")
    header = MAGIC + struct.pack("<II", v.shape[0], f.shape[0])
    return header + v.tobytes() + f.tobytes()


def _mesh_paths(subject: Subject):
    mesh_dir = os.path.join(settings.SUBJECTS_DIR, subject.name, "meshes")
    return mesh_dir, {hemi: os.path.join(mesh_dir, f"{hemi}_pial.bin") for hemi in HEMISPHERES}


def export_and_cache_surfaces(db: Session, subject: Subject, job: Job):
    """Reads surf/{lh,rh}.pial and writes cached binary mesh artifacts. Called
    once at the end of a successful recon job, and also exposed standalone as
    the `surface_export` job type (POST .../surface/rebuild) to backfill
    subjects that were reconned before this cache existed."""
    surf_dir = os.path.join(settings.SUBJECTS_DIR, subject.name, "surf")
    mesh_dir, out_paths = _mesh_paths(subject)
    os.makedirs(mesh_dir, exist_ok=True)

    for hemi in HEMISPHERES:
        pial_path = os.path.join(surf_dir, f"{hemi}.pial")
        if not os.path.exists(pial_path):
            raise FileNotFoundError(f"{pial_path} not found -- run reconstruction first.")
        data = surface_to_binary(pial_path)
        with open(out_paths[hemi], "wb") as f:
            f.write(data)
        register_artifact(db, subject.id, job.id, f"{hemi}_mesh_bin", out_paths[hemi])


def run_surface_export_job(db: Session, job: Job, log_file):
    subject = db.query(Subject).filter(Subject.id == job.subject_id).first()
    if not subject:
        raise ValueError("Subject not found")

    job.progress_pct = 20.0
    job.progress_message = "Exporting lh/rh pial surfaces to binary mesh cache"
    db.commit()

    export_and_cache_surfaces(db, subject, job)

    job.progress_pct = 95.0
    job.progress_message = "Surface export complete"
    db.commit()
    log_file.write("Exported lh/rh mesh binaries.\n")


def latest_mesh_artifact_path(db: Session, subject: Subject, hemi: str):
    if hemi not in HEMISPHERES:
        raise ValueError(f"hemi must be one of {HEMISPHERES}, got {hemi!r}")
    artifact = (
        db.query(Artifact)
        .filter(Artifact.subject_id == subject.id, Artifact.kind == f"{hemi}_mesh_bin")
        .order_by(Artifact.created_at.desc())
        .first()
    )
    if not artifact:
        return None
    abs_path = os.path.join(settings.DATA_ROOT, artifact.rel_path)
    return abs_path if os.path.exists(abs_path) else None
