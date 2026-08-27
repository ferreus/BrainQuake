import io
import json
import math
import os
import shutil
import struct
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import h5py
import mne
import numpy as np
import pytest
from fastapi.testclient import TestClient

# Setup test DB URL before importing app modules. DATA_ROOT must be isolated
# too, not just SUBJECTS_DIR -- the autouse fixture below rmtree's
# {DATA_ROOT}/recv and {DATA_ROOT}/logs before/after every test, and without
# this override that resolves to the real dev server's upload storage.
os.environ["DB_URL"] = "sqlite:///./data/test_brainquake.db"
os.environ["SUBJECTS_DIR"] = "./data/test_subjects"
os.environ["DATA_ROOT"] = "./data/test_data_root"

from app.config import settings
from app.db import Base, SessionLocal, engine, get_db
from app.main import app
from app.models import Artifact, Job, RecordingParams
from app.services.edf import MAX_WINDOW_SECONDS
from app.workers import jobs_worker
from app.workers.jobs_worker import run_job

# Use the app's own engine and SessionLocal for tests so that the worker
# (which imports SessionLocal from app.db) and the API share the same DB
# connection pool.  This avoids the "deleted-file-descriptor" desync that
# happens when a test creates its own engine, deletes the DB file, and
# recreates it while the app's engine still holds stale connections.


def override_get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_and_teardown_db():
    # Dispose all pooled connections so the engine starts fresh
    engine.dispose()

    test_db_path = "./data/test_brainquake.db"
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
    for d in [settings.SUBJECTS_DIR,
              os.path.join(settings.DATA_ROOT, "recv"),
              os.path.join(settings.DATA_ROOT, "logs")]:
        if os.path.exists(d):
            shutil.rmtree(d)
        os.makedirs(d, exist_ok=True)

    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()

    if os.path.exists(test_db_path):
        os.remove(test_db_path)
    for d in [settings.SUBJECTS_DIR,
              os.path.join(settings.DATA_ROOT, "recv"),
              os.path.join(settings.DATA_ROOT, "logs")]:
        if os.path.exists(d):
            shutil.rmtree(d)


# ---------------------------------------------------------------------------
# Mock subprocess.Popen (services/job_control.run_and_track_subprocess uses
# Popen, not subprocess.run, so it can record the real child pid for job
# cancellation) so tests don't need FreeSurfer/FSL installed.
# ---------------------------------------------------------------------------

def _apply_command_side_effects(cmd, stdout_file):
    """Create the expected output files for various commands."""
    if "recon-all" in cmd:
        import nibabel.freesurfer as fsio

        parts = cmd.split()
        subject_name = parts[parts.index("-s") + 1]
        subject_dir = os.path.join(settings.SUBJECTS_DIR, subject_name)
        mri_dir = os.path.join(subject_dir, "mri")
        surf_dir = os.path.join(subject_dir, "surf")
        os.makedirs(mri_dir, exist_ok=True)
        os.makedirs(surf_dir, exist_ok=True)
        with open(os.path.join(mri_dir, "orig.mgz"), "w") as f:
            f.write("mock orig mgz")
        with open(os.path.join(mri_dir, "brainmask.mgz"), "w") as f:
            f.write("mock brainmask mgz")

        # Minimal valid FreeSurfer surface files (a single triangle each) so
        # the post-recon mesh-export step (services/surface.py) has something
        # real to read via nibabel.freesurfer.read_geometry.
        tri_vertices = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
        tri_faces = np.array([[0, 1, 2]], dtype=np.int32)
        fsio.write_geometry(os.path.join(surf_dir, "lh.pial"), tri_vertices, tri_faces)
        fsio.write_geometry(os.path.join(surf_dir, "rh.pial"), tri_vertices, tri_faces)

        if stdout_file:
            stdout_file.write("[Mock] recon-all finished successfully\n")

    elif "mri_convert" in cmd:
        parts = cmd.split()
        dest = parts[-1]
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w") as f:
            f.write("mock orig nii")
        if stdout_file:
            stdout_file.write("[Mock] mri_convert finished successfully\n")

    elif "mri_binarize" in cmd:
        import nibabel as nib

        parts = cmd.split()
        o_idx = parts.index("--o")
        dest = parts[o_idx + 1]
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        data = np.ones((5, 5, 5), dtype=np.int16)
        img = nib.Nifti1Image(data, np.eye(4))
        nib.save(img, dest)
        if stdout_file:
            stdout_file.write("[Mock] mri_binarize finished successfully\n")

    elif "mri_annotation2label" in cmd:
        if stdout_file:
            stdout_file.write("[Mock] mri_annotation2label finished successfully\n")

    elif "zip" in cmd and "-rq" in cmd:
        parts = cmd.split()
        zip_idx = parts.index("-rq") + 1
        zip_file = parts[zip_idx]
        os.makedirs(os.path.dirname(zip_file) or ".", exist_ok=True)
        with open(zip_file, "w") as f:
            f.write("mock zip archive")
        if stdout_file:
            stdout_file.write("[Mock] zip finished successfully\n")

    elif "flirt" in cmd:
        import nibabel as nib

        parts = cmd.split()
        omat_idx = parts.index("-omat")
        out_idx = parts.index("-out")
        mat_path = parts[omat_idx + 1]
        out_path = parts[out_idx + 1]
        os.makedirs(os.path.dirname(mat_path), exist_ok=True)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(mat_path, "w") as f:
            f.write("mock affine transform matrix")
        data = np.ones((5, 5, 5), dtype=np.float32) * 500
        img = nib.Nifti1Image(data, np.eye(4))
        nib.save(img, out_path)
        if stdout_file:
            stdout_file.write("[Mock] flirt finished successfully\n")


class MockPopen:
    """Stand-in for subprocess.Popen: applies the same command side-effects as
    the real subprocess would produce (writing output files), then exposes the
    minimal Popen interface run_and_track_subprocess() relies on -- .pid,
    .communicate(), .returncode."""

    def __init__(self, cmd, *args, **kwargs):
        self.pid = 999999
        self.returncode = 0

        stdout_kw = kwargs.get("stdout")
        stderr_kw = kwargs.get("stderr")
        text_mode = kwargs.get("text", False)

        # Only apply side-effects against a real file-like stdout (as recon.py/
        # ct_register.py pass), not when the caller asked to capture via PIPE
        # (as electrodes.py's hough3dlines call does).
        stdout_file = stdout_kw if stdout_kw is not None and stdout_kw is not subprocess.PIPE else None
        _apply_command_side_effects(cmd, stdout_file)

        empty = "" if text_mode else b""
        self._stdout_data = empty if stdout_kw is subprocess.PIPE else None
        self._stderr_data = empty if stderr_kw is subprocess.PIPE else None

    def communicate(self):
        return self._stdout_data, self._stderr_data


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@patch("subprocess.Popen", side_effect=MockPopen)
def test_full_e2e_flow(mock_run):
    # 1. Create a subject
    response = client.post(
        "/subjects",
        json={"name": "TestSubject", "recon_type": "recon-all"},
    )
    assert response.status_code == 200
    subject_data = response.json()
    assert subject_data["name"] == "TestSubject"
    subject_id = subject_data["id"]

    # SUBJECTS_DIR/<name> must NOT exist yet -- recon-all/fast-surfer/infant_recon_all
    # treat that directory merely existing (regardless of contents) as "this subject
    # already has a prior run" when given -i, and refuse. The recon job itself
    # creates it (see services/recon.py's run_recon_job) immediately before invoking
    # the recon tool, not subject creation.
    assert not os.path.exists(os.path.join(settings.SUBJECTS_DIR, "TestSubject"))
    assert os.path.exists(
        os.path.join(settings.DATA_ROOT, "recv", "TestSubject"))

    # 2. Upload dummy T1 and CT files
    response = client.post(
        f"/subjects/{subject_id}/upload?file_type=t1",
        files={"file": ("t1.nii.gz", b"fake T1", "application/octet-stream")},
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "raw_t1"

    response = client.post(
        f"/subjects/{subject_id}/upload?file_type=ct",
        files={"file": ("ct.nii.gz", b"fake CT", "application/octet-stream")},
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "raw_ct"

    # 3. Queue a reconstruction job
    response = client.post(
        f"/subjects/{subject_id}/recon",
        json={"recon_type": "recon-all"},
    )
    assert response.status_code == 200
    job_data = response.json()
    assert job_data["state"] == "queued"
    job_id = job_data["id"]

    # 4. Execute the job directly (simulating the worker)
    run_job(job_id)

    # 5. Verify the job finished
    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 200
    job_status = response.json()
    assert job_status["state"] == "finished", (
        f"Expected 'finished', got '{job_status['state']}': "
        f"{job_status.get('progress_message')}"
    )
    assert job_status["progress_pct"] == 100.0

    # 6. Verify log was created
    response = client.get(f"/jobs/{job_id}/log")
    assert response.status_code == 200
    log_text = response.text
    assert "Started" in log_text
    assert "Completed successfully" in log_text

    # 7. Verify artifacts were registered
    db = SessionLocal()
    artifacts = (
        db.query(Artifact)
        .filter(Artifact.subject_id == subject_id, Artifact.job_id == job_id)
        .all()
    )
    kinds = {a.kind for a in artifacts}
    assert "orig_nii" in kinds
    assert "mask_mgz" in kinds
    assert "recon_zip" in kinds
    db.close()

    # 8. Queue and run a CT registration job
    response = client.post(
        f"/subjects/{subject_id}/electrodes/register-ct")
    assert response.status_code == 200
    ct_job = response.json()
    assert ct_job["state"] == "queued"
    ct_job_id = ct_job["id"]

    run_job(ct_job_id)

    response = client.get(f"/jobs/{ct_job_id}")
    assert response.status_code == 200
    ct_status = response.json()
    assert ct_status["state"] == "finished", (
        f"Expected 'finished', got '{ct_status['state']}': "
        f"{ct_status.get('progress_message')}"
    )

    db = SessionLocal()
    ct_artifacts = (
        db.query(Artifact)
        .filter(Artifact.subject_id == subject_id,
                Artifact.job_id == ct_job_id)
        .all()
    )
    ct_kinds = {a.kind for a in ct_artifacts}
    assert "ct_reg_mat" in ct_kinds
    assert "ct_reg_nii" in ct_kinds
    assert "ct_intracranial_nii" in ct_kinds

    intracranial = os.path.join(
        settings.DATA_ROOT, "recv", "TestSubject", "fslresults",
        "TestSubjectintracranial.nii.gz",
    )
    assert os.path.exists(intracranial)

    legacy_ct = os.path.join(
        settings.SUBJECTS_DIR, "TestSubject", "fslresults",
        "TestSubjectCT_Reg.nii.gz",
    )
    assert os.path.exists(legacy_ct)
    db.close()


@patch("subprocess.Popen", side_effect=MockPopen)
def test_subject_crud(mock_run):
    # Create
    r = client.post("/subjects",
                    json={"name": "S1"})
    assert r.status_code == 200
    sid = r.json()["id"]

    # List
    r = client.get("/subjects")
    assert r.status_code == 200
    assert len(r.json()) == 1

    # Get
    r = client.get(f"/subjects/{sid}")
    assert r.status_code == 200
    assert r.json()["name"] == "S1"

    # Duplicate
    r = client.post("/subjects",
                    json={"name": "S1"})
    assert r.status_code == 400

    # Delete
    r = client.delete(f"/subjects/{sid}")
    assert r.status_code == 200
    r = client.get("/subjects")
    assert len(r.json()) == 0


@patch("subprocess.Popen", side_effect=MockPopen)
def test_job_cancel(mock_run):
    r = client.post("/subjects",
                    json={"name": "Cancel"})
    sid = r.json()["id"]
    r = client.post(f"/subjects/{sid}/recon",
                    json={"recon_type": "recon-all"})
    jid = r.json()["id"]

    r = client.post(f"/jobs/{jid}/cancel")
    assert r.status_code == 200

    r = client.get(f"/jobs/{jid}")
    assert r.json()["state"] == "cancelled"


@patch("subprocess.Popen", side_effect=MockPopen)
def test_job_delete(mock_run):
    r = client.post("/subjects",
                    json={"name": "Delete"})
    sid = r.json()["id"]
    r = client.post(f"/subjects/{sid}/recon",
                    json={"recon_type": "recon-all"})
    jid = r.json()["id"]

    # Still queued -- delete must be refused so a live job can't vanish
    r = client.delete(f"/jobs/{jid}")
    assert r.status_code == 409

    client.post(f"/jobs/{jid}/cancel")
    r = client.delete(f"/jobs/{jid}")
    assert r.status_code == 200

    r = client.get(f"/jobs/{jid}")
    assert r.status_code == 404

    r = client.delete(f"/jobs/{jid}")
    assert r.status_code == 404


@patch("subprocess.Popen", side_effect=MockPopen)
def test_recon_job_tracks_subprocess_pid_not_worker_pid(mock_run):
    # Regression test: jobs_worker.run_job() used to set job.pid = os.getpid()
    # (the worker process's own pid), so POST /jobs/{id}/cancel's SIGTERM would
    # kill the entire worker -- every other queued/running job with it -- not
    # just the targeted job's subprocess. services/job_control.py now tracks the
    # real child pid per subprocess step instead; assert that's what actually
    # gets recorded, and that no pid lingers once the job finishes.
    from app.services import job_control

    r = client.post("/subjects", json={"name": "PidTrack"})
    sid = r.json()["id"]
    client.post(
        f"/subjects/{sid}/upload?file_type=t1",
        files={"file": ("t1.nii.gz", b"fake T1", "application/octet-stream")},
    )
    r = client.post(f"/subjects/{sid}/recon", json={"recon_type": "recon-all"})
    jid = r.json()["id"]

    seen_pids = []
    original_set_pid = job_control.set_running_pid

    def spy_set_running_pid(db, job, pid):
        seen_pids.append(pid)
        return original_set_pid(db, job, pid)

    with patch("app.services.job_control.set_running_pid", side_effect=spy_set_running_pid):
        run_job(jid)

    assert seen_pids, "expected at least one subprocess step to track a pid"
    assert os.getpid() not in seen_pids
    assert all(pid == 999999 for pid in seen_pids)  # MockPopen's fixed fake pid

    r = client.get(f"/jobs/{jid}")
    assert r.json()["state"] == "finished"
    assert r.json()["pid"] is None


def test_job_cancelled_error_ends_in_cancelled_not_failed():
    # Regression test for the other half of the cancel-bug fix: an in-process
    # job step (no subprocess to SIGTERM -- e.g. elec_segment/ei_compute/
    # hfo_compute/soz_fuse) calls services/job_control.check_cancelled() at its
    # existing progress checkpoints, which raises JobCancelledError once
    # POST /jobs/{id}/cancel has flipped the job's state out-of-band.
    # jobs_worker.run_job() must catch that distinctly and leave state as
    # "cancelled", not clobber it with "failed" via the generic except Exception
    # branch.
    from app.services.job_control import JobCancelledError

    r = client.post("/subjects", json={"name": "CooperativeCancel"})
    sid = r.json()["id"]
    r = client.post(f"/subjects/{sid}/recon", json={"recon_type": "recon-all"})
    jid = r.json()["id"]

    with patch.dict(jobs_worker.JOB_HANDLERS, {"recon": MagicMock(side_effect=JobCancelledError("cancelled mid-run"))}):
        run_job(jid)

    r = client.get(f"/jobs/{jid}")
    assert r.json()["state"] == "cancelled"
    assert r.json()["progress_message"] == "Job cancelled by user"


@patch("subprocess.Popen", side_effect=MockPopen)
def test_artifacts_and_recon_result(mock_run):
    # Setup: create subject, upload T1, run recon job
    r = client.post("/subjects", json={"name": "ArtTest"})
    sid = r.json()["id"]

    client.post(
        f"/subjects/{sid}/upload?file_type=t1",
        files={"file": ("t1.nii.gz", b"fake T1", "application/octet-stream")},
    )

    r = client.post(f"/subjects/{sid}/recon", json={"recon_type": "recon-all"})
    jid = r.json()["id"]
    run_job(jid)

    # GET /subjects/{id}/artifacts — should return at least the recon artifacts
    r = client.get(f"/subjects/{sid}/artifacts")
    assert r.status_code == 200
    kinds = {a["kind"] for a in r.json()}
    assert "orig_nii" in kinds
    assert "recon_zip" in kinds

    # GET /subjects/{id}/artifacts?kind=orig_nii — filter by kind
    r = client.get(f"/subjects/{sid}/artifacts?kind=orig_nii")
    assert r.status_code == 200
    assert all(a["kind"] == "orig_nii" for a in r.json())
    assert len(r.json()) >= 1

    # GET /subjects/{id}/recon/result — should list the recon job artifacts
    r = client.get(f"/subjects/{sid}/recon/result")
    assert r.status_code == 200
    result_kinds = {a["kind"] for a in r.json()}
    assert "orig_nii" in result_kinds

    # 404 when no finished recon job exists for a fresh subject
    r2 = client.post("/subjects", json={"name": "Fresh"})
    sid2 = r2.json()["id"]
    r = client.get(f"/subjects/{sid2}/recon/result")
    assert r.status_code == 404


@patch("subprocess.Popen", side_effect=MockPopen)
def test_artifact_download_and_subject_zip(mock_run):
    # Setup: create subject, upload T1, run recon job
    r = client.post("/subjects", json={"name": "DlTest"})
    sid = r.json()["id"]

    client.post(
        f"/subjects/{sid}/upload?file_type=t1",
        files={"file": ("t1.nii.gz", b"fake T1", "application/octet-stream")},
    )

    r = client.post(f"/subjects/{sid}/recon", json={"recon_type": "recon-all"})
    run_job(r.json()["id"])

    # GET /artifacts/{id}/download
    r = client.get(f"/subjects/{sid}/artifacts?kind=recon_zip")
    assert r.status_code == 200
    assert len(r.json()) == 1
    artifact_id = r.json()[0]["id"]

    r = client.get(f"/artifacts/{artifact_id}/download")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")

    # GET /subjects/{id}/download.zip
    r = client.get(f"/subjects/{sid}/download.zip")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")

    # 404 when no recon has been run
    r2 = client.post("/subjects", json={"name": "NoRecon"})
    sid2 = r2.json()["id"]
    r = client.get(f"/subjects/{sid2}/download.zip")
    assert r.status_code == 404


def test_delete_artifact():
    sid, artifact_id, _, _ = _create_subject_with_edf("DeleteArtifactTest")

    # sanity: the backing file exists and the artifact is listed
    r = client.get(f"/subjects/{sid}/artifacts")
    assert any(a["id"] == artifact_id for a in r.json())

    r = client.delete(f"/artifacts/{artifact_id}")
    assert r.status_code == 200

    r = client.get(f"/subjects/{sid}/artifacts")
    assert not any(a["id"] == artifact_id for a in r.json())

    # deleting again (or an id that never existed) 404s rather than erroring
    r = client.delete(f"/artifacts/{artifact_id}")
    assert r.status_code == 404

    # a DB row whose backing file is already gone from disk (the actual bug
    # this exists for) deletes cleanly too, instead of erroring on os.remove
    sid2, artifact_id2, _, _ = _create_subject_with_edf("DeleteMissingFileTest")
    r = client.get(f"/subjects/{sid2}/artifacts")
    rel_path = next(a["rel_path"] for a in r.json() if a["id"] == artifact_id2)
    os.remove(os.path.join(settings.DATA_ROOT, rel_path))

    r = client.delete(f"/artifacts/{artifact_id2}")
    assert r.status_code == 200


@patch("subprocess.Popen", side_effect=MockPopen)
def test_surface_mesh_export_and_download(mock_run):
    # recon.py's run_recon_job now caches lh/rh.pial as binary mesh artifacts
    # right after the FreeSurfer steps -- the mock recon-all command above
    # writes a real (single-triangle) FreeSurfer surface file, so this
    # exercises the actual nibabel read + binary encode, not just plumbing.
    from app.services.surface import MAGIC

    r = client.post("/subjects", json={"name": "MeshTest"})
    sid = r.json()["id"]
    client.post(
        f"/subjects/{sid}/upload?file_type=t1",
        files={"file": ("t1.nii.gz", b"fake T1", "application/octet-stream")},
    )
    r = client.post(f"/subjects/{sid}/recon", json={"recon_type": "recon-all"})
    run_job(r.json()["id"])

    r = client.get(f"/subjects/{sid}/artifacts")
    kinds = {a["kind"] for a in r.json()}
    assert "lh_mesh_bin" in kinds
    assert "rh_mesh_bin" in kinds

    for hemi in ("lh", "rh"):
        r = client.get(f"/subjects/{sid}/surface/{hemi}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/octet-stream"
        body = r.content
        assert body[:8] == MAGIC
        vertex_count, face_count = struct.unpack("<II", body[8:16])
        assert vertex_count == 3  # the mock writes one triangle
        assert face_count == 1
        expected_len = 16 + vertex_count * 3 * 4 + face_count * 3 * 4
        assert len(body) == expected_len

    # invalid hemi
    r = client.get(f"/subjects/{sid}/surface/mid")
    assert r.status_code == 400

    # a subject that never reconned has no cached mesh yet
    r2 = client.post("/subjects", json={"name": "NoMesh"})
    sid2 = r2.json()["id"]
    r = client.get(f"/subjects/{sid2}/surface/lh")
    assert r.status_code == 404


@patch("subprocess.Popen", side_effect=MockPopen)
def test_surface_rebuild_job(mock_run):
    r = client.post("/subjects", json={"name": "RebuildTest"})
    sid = r.json()["id"]
    client.post(
        f"/subjects/{sid}/upload?file_type=t1",
        files={"file": ("t1.nii.gz", b"fake T1", "application/octet-stream")},
    )
    r = client.post(f"/subjects/{sid}/recon", json={"recon_type": "recon-all"})
    run_job(r.json()["id"])

    r = client.post(f"/subjects/{sid}/surface/rebuild")
    assert r.status_code == 200
    job = r.json()
    assert job["job_type"] == "surface_export"
    run_job(job["id"])

    r = client.get(f"/jobs/{job['id']}")
    assert r.json()["state"] == "finished"

    r = client.get(f"/subjects/{sid}/surface/lh")
    assert r.status_code == 200


def test_labels_summary():
    # detect()'s real pipeline (hough3dlines + GMM) is heavy to mock
    # realistically -- write a small synthetic labels volume directly at the
    # path detect() would have produced, and hit the summary endpoint against
    # that, which is all summarize_labels() actually reads.
    r = client.post("/subjects", json={"name": "LabelsTest"})
    sid = r.json()["id"]

    ct_dir = os.path.join(settings.SUBJECTS_DIR, "LabelsTest", "fslresults")
    os.makedirs(ct_dir, exist_ok=True)
    labels = np.zeros((4, 4, 4))
    labels[0, 0, 0] = 1
    labels[0, 0, 1] = 1
    labels[3, 3, 3] = 2
    np.save(os.path.join(ct_dir, "LabelsTest_labels.npy"), labels)

    r = client.get(f"/subjects/{sid}/electrodes/labels-summary")
    assert r.status_code == 200
    data = r.json()
    assert data["K"] == 2
    by_label = {c["label"]: c for c in data["clusters"]}
    assert by_label[1]["voxel_count"] == 2
    # voxel centroid (0, 0, 0.5) -> display space (128-vx, vz-128, 128-vy),
    # matching ElectrodeSeg.resulting()'s transform for final contacts.
    assert by_label[1]["centroid"] == [128.0, -127.5, 128.0]
    assert by_label[2]["voxel_count"] == 1
    assert by_label[2]["centroid"] == [125.0, -125.0, 125.0]

    # 404 before detect() has ever run
    r2 = client.post("/subjects", json={"name": "NoLabels"})
    sid2 = r2.json()["id"]
    r = client.get(f"/subjects/{sid2}/electrodes/labels-summary")
    assert r.status_code == 404


def test_electrodes_import_contacts():
    # elec_import bypasses ct_register/detect/segment entirely -- no CT_Reg.nii.gz
    # or labels_npy needed, just a subject to hang fslresults/ off of.
    r = client.post("/subjects", json={"name": "ImportTest"})
    sid = r.json()["id"]

    contacts = [
        {"electrode": "G'", "contact_index": i, "x": float(i), "y": float(i) + 0.5, "z": float(i) - 0.5}
        for i in (1, 2, 3)
    ] + [
        {"electrode": "X", "contact_index": i, "x": float(i) * 2, "y": 0.0, "z": 1.0}
        for i in (1, 2)
    ]

    r = client.post(f"/subjects/{sid}/electrodes/import", json={"contacts": contacts})
    assert r.status_code == 200
    job_id = r.json()["id"]

    run_job(job_id)

    r = client.get(f"/jobs/{job_id}")
    job_status = r.json()
    assert job_status["state"] == "finished", job_status.get("progress_message")

    db = SessionLocal()
    kinds = [
        a.kind for a in
        db.query(Artifact).filter(Artifact.subject_id == sid, Artifact.job_id == job_id).all()
    ]
    db.close()
    assert kinds.count("chnXyzDict") == 1
    assert kinds.count("contact_txt") == 2  # one per electrode: G', X

    r = client.get(f"/subjects/{sid}/electrodes/chn-xyz")
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"G'", "X"}
    assert data["G'"] == [[1.0, 1.5, 0.5], [2.0, 2.5, 1.5], [3.0, 3.5, 2.5]]
    assert data["X"] == [[2.0, 0.0, 1.0], [4.0, 0.0, 1.0]]

    r = client.get(f"/subjects/{sid}/electrodes/contacts/G'")
    assert r.status_code == 200
    assert r.json() == data["G'"]


def test_electrodes_import_rejects_non_contiguous_indices():
    r = client.post("/subjects", json={"name": "ImportGapTest"})
    sid = r.json()["id"]

    contacts = [
        {"electrode": "A", "contact_index": 1, "x": 0.0, "y": 0.0, "z": 0.0},
        {"electrode": "A", "contact_index": 3, "x": 1.0, "y": 1.0, "z": 1.0},  # gap at 2
    ]
    r = client.post(f"/subjects/{sid}/electrodes/import", json={"contacts": contacts})
    job_id = r.json()["id"]

    run_job(job_id)

    r = client.get(f"/jobs/{job_id}")
    job_status = r.json()
    assert job_status["state"] == "failed"
    assert "not contiguous" in job_status["progress_message"]


def test_electrodes_import_csv_text():
    r = client.post("/subjects", json={"name": "ImportCsvTest"})
    sid = r.json()["id"]

    csv_text = (
        "label,electrode,contact_index,R,A,S,surfR,surfA,surfS\n"
        "G'1,G',1,0,0,0,1.0,1.5,0.5\n"
        "G'2,G',2,0,0,0,2.0,2.5,1.5\n"
    )
    r = client.post(f"/subjects/{sid}/electrodes/import", json={"csv_text": csv_text})
    assert r.status_code == 200
    job_id = r.json()["id"]

    run_job(job_id)

    r = client.get(f"/jobs/{job_id}")
    job_status = r.json()
    assert job_status["state"] == "finished", job_status.get("progress_message")

    r = client.get(f"/subjects/{sid}/electrodes/chn-xyz")
    assert r.status_code == 200
    assert r.json() == {"G'": [[1.0, 1.5, 0.5], [2.0, 2.5, 1.5]]}


def test_electrodes_import_csv_text_missing_columns_fails_as_job():
    # A bad CSV must still create a job (visible in the Jobs panel like every
    # other pipeline step) that ends up 'failed' with a clear reason --
    # never a 400 with no job at all, which is what the web client relies on.
    r = client.post("/subjects", json={"name": "ImportCsvBadTest"})
    sid = r.json()["id"]

    csv_text = "electrode,contact_index,x,y,z\nG',1,0,0,0\n"  # wrong column names
    r = client.post(f"/subjects/{sid}/electrodes/import", json={"csv_text": csv_text})
    assert r.status_code == 200
    job_id = r.json()["id"]
    assert r.json()["state"] == "queued"

    run_job(job_id)

    r = client.get(f"/jobs/{job_id}")
    job_status = r.json()
    assert job_status["state"] == "failed"
    assert "missing column" in job_status["progress_message"]


def test_electrodes_import_requires_contacts_or_csv_text():
    r = client.post("/subjects", json={"name": "ImportEmptyTest"})
    sid = r.json()["id"]
    r = client.post(f"/subjects/{sid}/electrodes/import", json={})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Anatomical labeling of contacts (services/anatomy.py)
# ---------------------------------------------------------------------------

def _write_synthetic_segmentation(name, shape=(60, 60, 60)):
    """An aparc+aseg.mgz with hand-placed structures, in the same LIA conformed
    geometry as _make_synthetic_recon_subject -- so tkreg RAS and voxel indices
    are related by exactly (i, j, k) = (30 - x, 30 - z, y + 30) for a 60^3
    volume, and every expected label below is checkable by hand.

    Layout: a 20:40 cube of white matter (2), a 28:32 cube of hippocampus (17)
    inside it, and a 38:40 slab of entorhinal cortex (1006). Everything else is
    background (0)."""
    import nibabel as nib
    import nibabel.freesurfer.mghformat as mghf

    nx, ny, nz = shape
    affine = np.array([
        [-1, 0, 0, nx / 2],
        [0, 0, 1, -nz / 2],
        [0, -1, 0, ny / 2],
        [0, 0, 0, 1],
    ], dtype=float)

    data = np.zeros(shape, dtype=np.int32)
    data[20:40, 20:40, 20:40] = 2      # Left-Cerebral-White-Matter
    data[28:32, 28:32, 28:32] = 17     # Left-Hippocampus
    data[38:40, 28:32, 28:32] = 1006   # ctx-lh-entorhinal

    mri_dir = os.path.join(settings.SUBJECTS_DIR, name, "mri")
    os.makedirs(mri_dir, exist_ok=True)
    seg_path = os.path.join(mri_dir, "aparc+aseg.mgz")
    nib.save(mghf.MGHImage(data, affine), seg_path)
    return seg_path


def _subject_with_contacts(name, contacts):
    r = client.post("/subjects", json={"name": name})
    sid = r.json()["id"]
    r = client.post(f"/subjects/{sid}/electrodes/import", json={"contacts": contacts})
    job_id = r.json()["id"]
    run_job(job_id)
    assert client.get(f"/jobs/{job_id}").json()["state"] == "finished"
    return sid


def test_contact_anatomy_labels_each_contact():
    # Voxel -> tkreg RAS for this geometry: x = 30 - i, y = k - 30, z = 30 - j.
    contacts = [
        # A1: voxel (30,30,30), dead centre of the hippocampus cube.
        {"electrode": "A", "contact_index": 1, "x": 0.0, "y": 0.0, "z": 0.0},
        # A2: voxel (33,30,30) -- white matter, 2 mm from the hippocampus cube's
        # i=31 face. The case the whole nearest_structure field exists for.
        {"electrode": "A", "contact_index": 2, "x": -3.0, "y": 0.0, "z": 0.0},
        # A3: voxel (22,22,22) -- deep white matter, >10 mm from any structure.
        {"electrode": "A", "contact_index": 3, "x": 8.0, "y": -8.0, "z": 8.0},
        # A4: voxel (38,30,30) -- entorhinal cortex, i.e. an aparc parcel and
        # not just an aseg one.
        {"electrode": "A", "contact_index": 4, "x": -8.0, "y": 0.0, "z": 0.0},
        # A5: voxel (-70,30,30) -- outside the segmentation entirely.
        {"electrode": "A", "contact_index": 5, "x": 100.0, "y": 0.0, "z": 0.0},
    ]
    sid = _subject_with_contacts("AnatomyTest", contacts)
    _write_synthetic_segmentation("AnatomyTest")

    r = client.get(f"/subjects/{sid}/electrodes/anatomy")
    assert r.status_code == 200
    data = r.json()
    assert data["segmentation"] == "mri/aparc+aseg.mgz"
    assert data["radius_mm"] == 3.0

    by_name = {c["name"]: c for c in data["contacts"]}
    assert list(by_name) == ["A1", "A2", "A3", "A4", "A5"]

    assert by_name["A1"]["voxel"] == [30, 30, 30]
    assert by_name["A1"]["label_name"] == "Left-Hippocampus"
    assert by_name["A1"]["nearest_structure"]["label_name"] == "Left-Hippocampus"
    assert by_name["A1"]["nearest_structure"]["distance_mm"] == 0.0

    assert by_name["A2"]["label_name"] == "Left-Cerebral-White-Matter"
    assert by_name["A2"]["nearest_structure"]["label_name"] == "Left-Hippocampus"
    assert by_name["A2"]["nearest_structure"]["distance_mm"] == 2.0

    assert by_name["A3"]["label_name"] == "Left-Cerebral-White-Matter"
    assert by_name["A3"]["nearest_structure"] is None  # nothing grey within 3 mm

    assert by_name["A4"]["label_name"] == "ctx-lh-entorhinal"

    assert by_name["A5"]["out_of_volume"] is True
    assert by_name["A5"]["label_id"] is None


def test_contact_anatomy_neighborhood_reports_boundary_contacts():
    # Voxel (31,30,30) is the last hippocampus voxel before white matter, so a
    # 3 mm sphere around it straddles both -- the case a single exact-voxel
    # label silently resolves one way and hides.
    contacts = [{"electrode": "A", "contact_index": 1, "x": -1.0, "y": 0.0, "z": 0.0}]
    sid = _subject_with_contacts("AnatomyBoundaryTest", contacts)
    _write_synthetic_segmentation("AnatomyBoundaryTest")

    r = client.get(f"/subjects/{sid}/electrodes/anatomy")
    contact = r.json()["contacts"][0]
    assert contact["label_name"] == "Left-Hippocampus"

    fractions = {n["label_name"]: n["fraction"] for n in contact["neighborhood"]}
    assert set(fractions) == {"Left-Hippocampus", "Left-Cerebral-White-Matter"}
    assert 0 < fractions["Left-Hippocampus"] < 1
    assert abs(sum(fractions.values()) - 1.0) < 1e-6

    # A larger radius reaches entorhinal cortex (i=38, 7 mm away) too.
    r = client.get(f"/subjects/{sid}/electrodes/anatomy", params={"radius_mm": 8})
    contact = r.json()["contacts"][0]
    assert "ctx-lh-entorhinal" in {n["label_name"] for n in contact["neighborhood"]}


def test_contact_anatomy_prefers_installed_freesurfer_lut():
    # The built-in table is a fallback for machines without FreeSurfer; a real
    # install's LUT must win, since it is the one that matches the version that
    # produced the segmentation.
    contacts = [{"electrode": "A", "contact_index": 1, "x": 0.0, "y": 0.0, "z": 0.0}]
    sid = _subject_with_contacts("AnatomyLutTest", contacts)
    _write_synthetic_segmentation("AnatomyLutTest")

    with tempfile.TemporaryDirectory() as fs_home:
        with open(os.path.join(fs_home, "FreeSurferColorLUT.txt"), "w") as f:
            f.write("# id name R G B A\n")
            f.write("17  Left-Hippocampus-Renamed  220 216 20 0\n")
        with patch.object(settings, "FREESURFER_HOME", fs_home):
            r = client.get(f"/subjects/{sid}/electrodes/anatomy")
    assert r.json()["contacts"][0]["label_name"] == "Left-Hippocampus-Renamed"


def test_contact_anatomy_404s_without_contacts_or_segmentation():
    # No contacts at all.
    sid = client.post("/subjects", json={"name": "AnatomyNoContacts"}).json()["id"]
    _write_synthetic_segmentation("AnatomyNoContacts")
    r = client.get(f"/subjects/{sid}/electrodes/anatomy")
    assert r.status_code == 404

    # Contacts, but no recon output to label them against.
    contacts = [{"electrode": "A", "contact_index": 1, "x": 0.0, "y": 0.0, "z": 0.0}]
    sid = _subject_with_contacts("AnatomyNoSeg", contacts)
    r = client.get(f"/subjects/{sid}/electrodes/anatomy")
    assert r.status_code == 404
    assert "segmentation" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 3D Slicer .mrb import (preview/approve/reject) -- see
# docs/seeg_slicer_contact_import_plan.md and services/electrodes.parse_mrb.
# ---------------------------------------------------------------------------

def _make_synthetic_recon_subject(name, brain_radius=20.0, shape=(60, 60, 60)):
    """Writes a real, loadable orig.mgz + brainmask.mgz (not the plain-text
    mocks _apply_command_side_effects uses for recon-all, which parse_mrb's
    nib.load calls can't read).

    Uses FreeSurfer's LIA conformed orientation, the same one recon-all and
    infant_recon_all emit, sized so Pxyz_c is [0,0,0]. That makes scanner RAS
    and surface (tkreg) RAS exactly coincide, so every coordinate in these
    tests stays hand-checkable -- while still exercising _surface_ras through a
    realistic volume. An identity-direction affine would also give Pxyz_c
    [0,0,0], but it is not a geometry FreeSurfer ever produces and it made the
    scanner->tkr conversion vacuous.

    brainmask is 1 inside a `brain_radius`-mm sphere around the origin."""
    import nibabel as nib
    import nibabel.freesurfer.mghformat as mghf

    mri_dir = os.path.join(settings.SUBJECTS_DIR, name, "mri")
    os.makedirs(mri_dir, exist_ok=True)

    dx, dy, dz = 1.0, 1.0, 1.0
    nx, ny, nz = shape
    affine = np.array([
        [-dx, 0, 0, dx * nx / 2],
        [0, 0, dz, -dz * nz / 2],
        [0, -dy, 0, dy * ny / 2],
        [0, 0, 0, 1],
    ], dtype=float)

    orig = mghf.MGHImage(np.zeros(shape, dtype=np.float32), affine)
    nib.save(orig, os.path.join(mri_dir, "orig.mgz"))
    assert tuple(orig.header.get("Pxyz_c")) == (0.0, 0.0, 0.0)

    ii, jj, kk = np.meshgrid(*[np.arange(s) for s in shape], indexing="ij")
    center = np.array(shape) / 2
    dist = np.sqrt((ii - center[0]) ** 2 + (jj - center[1]) ** 2 + (kk - center[2]) ** 2)
    brainmask_data = (dist <= brain_radius).astype(np.float32)
    nib.save(mghf.MGHImage(brainmask_data, affine), os.path.join(mri_dir, "brainmask.mgz"))


def _write_itk_h5_transform(path, A, t, c, transform_type="AffineTransform_double_3_3"):
    with h5py.File(path, "w") as f:
        f["TransformGroup/0/TransformType"] = [transform_type.encode()]
        f["TransformGroup/0/TransformParameters"] = np.concatenate([A.flatten(), t])
        f["TransformGroup/0/TransformFixedParameters"] = c


def _make_synthetic_mrb(mrb_path, nodes):
    """nodes: list of {"name": str, "points": [(label, [x,y,z]), ...],
    "coordinate_system": "LPS"|"RAS", "transform": None | (A, t, c)}. Builds a
    minimal but structurally real .mrb (zip of a .mrml + Data/*.mrk.json [+
    Data/*.h5]) -- enough for parse_mrb()'s XML/JSON/HDF5 parsing, not a
    faithful full Slicer scene."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = os.path.join(tmp, "Data")
        os.makedirs(data_dir)
        markup_xml, storage_xml, transform_xml, transform_storage_xml = [], [], [], []

        for i, node in enumerate(nodes):
            coord_system = node.get("coordinate_system", "LPS")
            markup_id = f"vtkMRMLMarkupsFiducialNode{i + 1}"
            storage_id = f"vtkMRMLMarkupsJsonStorageNode{i + 1}"
            mrk_name = f"{node['name']}.mrk.json"
            with open(os.path.join(data_dir, mrk_name), "w") as f:
                json.dump({
                    "markups": [{
                        "coordinateSystem": coord_system,
                        "controlPoints": [
                            {"label": label, "position": list(pos)} for label, pos in node["points"]
                        ],
                    }],
                }, f)

            refs = f"storage:{storage_id};"
            if node.get("transform"):
                transform_id = f"vtkMRMLTransformNode{i + 1}"
                transform_storage_id = f"vtkMRMLTransformStorageNode{i + 1}"
                h5_name = f"{node['name']}_transform.h5"
                _write_itk_h5_transform(os.path.join(data_dir, h5_name), *node["transform"])
                transform_xml.append(
                    f'<Transform id="{transform_id}" name="{node["name"]} transform" '
                    f'references="storage:{transform_storage_id};"></Transform>')
                transform_storage_xml.append(
                    f'<TransformStorage id="{transform_storage_id}" fileName="Data/{h5_name}"></TransformStorage>')
                refs += f"transform:{transform_id};"

            markup_xml.append(
                f'<MarkupsFiducial id="{markup_id}" name="{node["name"]}" references="{refs}"></MarkupsFiducial>')
            storage_xml.append(
                f'<MarkupsJsonStorage id="{storage_id}" fileName="Data/{mrk_name}" '
                f'coordinateSystem="{coord_system}"></MarkupsJsonStorage>')

        mrml = "<MRML>\n" + "\n".join(markup_xml + storage_xml + transform_xml + transform_storage_xml) + "\n</MRML>"
        mrml_path = os.path.join(tmp, "scene.mrml")
        with open(mrml_path, "w") as f:
            f.write(mrml)

        with zipfile.ZipFile(mrb_path, "w") as zf:
            zf.write(mrml_path, "scene.mrml")
            for fn in os.listdir(data_dir):
                zf.write(os.path.join(data_dir, fn), os.path.join("Data", fn))


class _NamedSubject:
    """parse_mrb() only reads subject.name -- avoids a DB round-trip for the
    direct-call unit tests below."""
    def __init__(self, name):
        self.name = name


def test_parse_mrb_picks_contact_like_node_over_others():
    from app.services.electrodes import parse_mrb

    _make_synthetic_recon_subject("SlicerNodePick")
    mrb_path = os.path.join(tempfile.gettempdir(), "node_pick_test.mrb")
    _make_synthetic_mrb(mrb_path, [
        {"name": "TargetPoints", "coordinate_system": "RAS",
         "points": [("Entry point", [0, 0, 0]), ("Target point", [0, 0, 5])]},  # labels don't match electrode+int
        {"name": "Contacts", "coordinate_system": "RAS",
         "points": [("A1", [0, 0, 5]), ("A2", [0, 0, 6]), ("B1", [3, 0, 5])]},
    ])
    try:
        contacts, diagnostics = parse_mrb(mrb_path, _NamedSubject("SlicerNodePick"))
    finally:
        os.remove(mrb_path)

    assert diagnostics["node_name"] == "Contacts"
    assert diagnostics["candidate_node_names"] == ["Contacts"]
    assert diagnostics["transform_used"] == "none"
    assert diagnostics["in_brain_fraction"] == 1.0
    assert len(contacts) == 3
    by_electrode = {(c["electrode"], c["contact_index"]): (c["x"], c["y"], c["z"]) for c in contacts}
    assert by_electrode[("A", 1)] == (0.0, 0.0, 5.0)
    assert by_electrode[("B", 1)] == (3.0, 0.0, 5.0)


def test_parse_mrb_picks_correct_transform_direction():
    from app.services.electrodes import parse_mrb

    _make_synthetic_recon_subject("SlicerDirection")
    # Identity rotation, translation chosen so the INVERSE direction lands
    # near the brain center and the forward direction lands far outside it --
    # mirrors what was found by hand against data/bella_3dslicer.mrb (see
    # docs/seeg_slicer_contact_import_plan.md), generalized into a check
    # instead of a hardcoded assumption about which node/direction is right.
    A = np.eye(3)
    t = np.array([0.0, 0.0, -50.0])
    c = np.zeros(3)
    mrb_path = os.path.join(tempfile.gettempdir(), "direction_test.mrb")
    _make_synthetic_mrb(mrb_path, [
        {"name": "Contacts", "coordinate_system": "RAS",
         "points": [("A1", [0, 0, -50]), ("A2", [3, 0, -50])],
         "transform": (A, t, c)},
    ])
    try:
        contacts, diagnostics = parse_mrb(mrb_path, _NamedSubject("SlicerDirection"))
    finally:
        os.remove(mrb_path)

    assert diagnostics["transform_used"] == "inverse"
    assert diagnostics["in_brain_fraction"] == 1.0
    by_index = {c["contact_index"]: (c["x"], c["y"], c["z"]) for c in contacts}
    assert by_index[1] == pytest.approx((0.0, 0.0, 0.0))
    assert by_index[2] == pytest.approx((3.0, 0.0, 0.0))


def _parse_single_contact_mrb(subject_name, coordinate_system, position, transform):
    """Runs parse_mrb over one contact and returns its (x, y, z)."""
    from app.services.electrodes import parse_mrb

    mrb_path = os.path.join(tempfile.gettempdir(), f"{subject_name}.mrb")
    _make_synthetic_mrb(mrb_path, [
        {"name": "Contacts", "coordinate_system": coordinate_system,
         "points": [("A1", list(position))], "transform": transform},
    ])
    try:
        contacts, diagnostics = parse_mrb(mrb_path, _NamedSubject(subject_name))
    finally:
        os.remove(mrb_path)
    c = contacts[0]
    return (c["x"], c["y"], c["z"]), diagnostics


def test_itk_transform_is_applied_in_lps_not_in_the_declared_system():
    """An ITK .h5 transform is always defined in LPS, whatever the markups node
    declares. A translation along x therefore has to move a RAS point the
    opposite way -- applying it directly to RAS coordinates flips the sign of
    the correction.
    """
    _make_synthetic_recon_subject("SlicerLpsRas")
    # +20 mm along LPS x == -20 mm along RAS x, so a contact at RAS x=+10
    # must end up at RAS x=-10 (still inside the 20 mm brain sphere, so the
    # forward direction wins the in-brain heuristic).
    transform = (np.eye(3), np.array([20.0, 0.0, 0.0]), np.zeros(3))

    xyz, diagnostics = _parse_single_contact_mrb(
        "SlicerLpsRas", "RAS", [10.0, 0.0, 0.0], transform)

    assert diagnostics["transform_used"] == "forward"
    assert xyz == pytest.approx((-10.0, 0.0, 0.0)), (
        "applying the LPS-defined transform straight to RAS coordinates would "
        "give +30 here"
    )


def test_rotation_is_interpreted_in_lps():
    """A rotation about x is the case the direction heuristic cannot rescue.

    For a pure translation, trying both directions happens to recover the right
    coordinates even if the transform is applied in the wrong convention. A
    rotation is not symmetric that way -- LPS and RAS interpretations differ by
    the sign of the rotation angle, and both land inside the brain, so nothing
    downstream flags it. Real CT-to-MRI registrations rotate.
    """
    _make_synthetic_recon_subject("SlicerLpsRot")
    rx90 = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])
    transform = (rx90, np.zeros(3), np.zeros(3))

    xyz, _ = _parse_single_contact_mrb(
        "SlicerLpsRot", "RAS", [0.0, 10.0, 0.0], transform)

    assert xyz == pytest.approx((0.0, 0.0, -10.0)), (
        "interpreting the rotation in RAS would give (0, 0, +10); both are "
        "inside the brain mask, so only this assertion catches it"
    )


def test_lps_and_ras_declarations_of_the_same_point_agree():
    """The same physical contact, expressed in either convention, must land in
    the same place after the same transform."""
    _make_synthetic_recon_subject("SlicerLpsRasPair")
    transform = (np.eye(3), np.array([20.0, 0.0, 0.0]), np.zeros(3))

    as_ras, _ = _parse_single_contact_mrb(
        "SlicerLpsRasPair", "RAS", [10.0, 0.0, 0.0], transform)
    # The same point written in LPS has both x and y negated.
    as_lps, _ = _parse_single_contact_mrb(
        "SlicerLpsRasPair", "LPS", [-10.0, 0.0, 0.0], transform)

    assert as_ras == pytest.approx(as_lps)


def test_slicer_import_preview_approve_flow():
    _make_synthetic_recon_subject("SlicerPreviewTest")
    r = client.post("/subjects", json={"name": "SlicerPreviewTest"})
    sid = r.json()["id"]

    mrb_path = os.path.join(tempfile.gettempdir(), "preview_flow_test.mrb")
    _make_synthetic_mrb(mrb_path, [
        {"name": "Contacts", "coordinate_system": "RAS",
         "points": [("A1", [0, 0, 5]), ("A2", [0, 0, 6])]},
    ])
    try:
        with open(mrb_path, "rb") as f:
            r = client.post(
                f"/subjects/{sid}/upload?file_type=mrb",
                files={"file": ("scene.mrb", f.read(), "application/zip")},
            )
    finally:
        os.remove(mrb_path)
    assert r.status_code == 200
    assert r.json()["kind"] == "raw_mrb"
    mrb_artifact_id = r.json()["id"]

    r = client.post(f"/subjects/{sid}/electrodes/import/preview", json={"mrb_artifact_id": mrb_artifact_id})
    assert r.status_code == 200
    job_id = r.json()["id"]
    run_job(job_id)

    r = client.get(f"/jobs/{job_id}")
    job_status = r.json()
    assert job_status["state"] == "finished", job_status.get("progress_message")

    r = client.get(f"/subjects/{sid}/electrodes/import/preview")
    assert r.status_code == 200
    preview = r.json()
    assert preview["diagnostics"]["node_name"] == "Contacts"
    assert len(preview["contacts"]) == 2

    # not yet written to the real contacts artifacts
    assert client.get(f"/subjects/{sid}/electrodes/chn-xyz").status_code == 404

    r = client.post(f"/subjects/{sid}/electrodes/import/preview/approve")
    assert r.status_code == 200

    r = client.get(f"/subjects/{sid}/electrodes/chn-xyz")
    assert r.status_code == 200
    assert r.json() == {"A": [[0.0, 0.0, 5.0], [0.0, 0.0, 6.0]]}

    # preview consumed
    assert client.get(f"/subjects/{sid}/electrodes/import/preview").status_code == 404


def test_slicer_import_preview_reject():
    _make_synthetic_recon_subject("SlicerRejectTest")
    r = client.post("/subjects", json={"name": "SlicerRejectTest"})
    sid = r.json()["id"]

    mrb_path = os.path.join(tempfile.gettempdir(), "preview_reject_test.mrb")
    _make_synthetic_mrb(mrb_path, [
        {"name": "Contacts", "coordinate_system": "RAS", "points": [("A1", [0, 0, 5])]},
    ])
    try:
        with open(mrb_path, "rb") as f:
            r = client.post(
                f"/subjects/{sid}/upload?file_type=mrb",
                files={"file": ("scene.mrb", f.read(), "application/zip")},
            )
    finally:
        os.remove(mrb_path)
    mrb_artifact_id = r.json()["id"]

    r = client.post(f"/subjects/{sid}/electrodes/import/preview", json={"mrb_artifact_id": mrb_artifact_id})
    run_job(r.json()["id"])
    assert client.get(f"/subjects/{sid}/electrodes/import/preview").status_code == 200

    r = client.post(f"/subjects/{sid}/electrodes/import/preview/reject")
    assert r.status_code == 200

    assert client.get(f"/subjects/{sid}/electrodes/import/preview").status_code == 404
    assert client.get(f"/subjects/{sid}/electrodes/chn-xyz").status_code == 404


def test_slicer_import_preview_requires_recon():
    # No _make_synthetic_recon_subject call -- this subject has no orig.mgz/
    # brainmask.mgz, so the job should fail with a clear reason rather than a
    # raw traceback from nib.load on a missing file.
    r = client.post("/subjects", json={"name": "SlicerNoReconTest"})
    sid = r.json()["id"]

    mrb_path = os.path.join(tempfile.gettempdir(), "no_recon_test.mrb")
    _make_synthetic_mrb(mrb_path, [
        {"name": "Contacts", "coordinate_system": "RAS", "points": [("A1", [0, 0, 5])]},
    ])
    try:
        with open(mrb_path, "rb") as f:
            r = client.post(
                f"/subjects/{sid}/upload?file_type=mrb",
                files={"file": ("scene.mrb", f.read(), "application/zip")},
            )
    finally:
        os.remove(mrb_path)
    mrb_artifact_id = r.json()["id"]

    r = client.post(f"/subjects/{sid}/electrodes/import/preview", json={"mrb_artifact_id": mrb_artifact_id})
    job_id = r.json()["id"]
    run_job(job_id)

    r = client.get(f"/jobs/{job_id}")
    job_status = r.json()
    assert job_status["state"] == "failed"
    assert "reconstruction" in job_status["progress_message"]


def test_delete_electrode_contacts():
    from app.services.recon import register_artifact

    r = client.post("/subjects", json={"name": "ClearTest"})
    sid = r.json()["id"]

    # simulate detect()'s cluster result
    ct_dir = os.path.join(settings.SUBJECTS_DIR, "ClearTest", "fslresults")
    os.makedirs(ct_dir, exist_ok=True)
    labels_path = os.path.join(ct_dir, "ClearTest_labels.npy")
    np.save(labels_path, np.zeros((4, 4, 4)))
    db = SessionLocal()
    register_artifact(db, sid, None, "labels_npy", labels_path)
    db.close()

    # simulate segment()'s (or import's) contact result
    contacts = [{"electrode": "A", "contact_index": 1, "x": 0.0, "y": 0.0, "z": 0.0}]
    r = client.post(f"/subjects/{sid}/electrodes/import", json={"contacts": contacts})
    run_job(r.json()["id"])

    result_dir = os.path.join(ct_dir, "ClearTest_result")
    chn_xyz_path = os.path.join(ct_dir, "chnXyzDict.npy")
    assert os.path.exists(labels_path)
    assert os.path.exists(chn_xyz_path)
    assert os.path.exists(result_dir)

    r = client.delete(f"/subjects/{sid}/electrodes/contacts")
    assert r.status_code == 200

    assert not os.path.exists(labels_path)
    assert not os.path.exists(chn_xyz_path)
    assert not os.path.exists(result_dir)

    db = SessionLocal()
    remaining = (
        db.query(Artifact)
        .filter(Artifact.subject_id == sid, Artifact.kind.in_(["labels_npy", "chnXyzDict", "contact_txt"]))
        .count()
    )
    db.close()
    assert remaining == 0

    assert client.get(f"/subjects/{sid}/electrodes/chn-xyz").status_code == 404
    assert client.get(f"/subjects/{sid}/electrodes/labels-summary").status_code == 404


def _make_synthetic_edf(path, n_channels=4, sfreq=1000.0, duration_sec=10.0, ch_names=None):
    """Writes a real, re-readable EDF file (via mne + edfio) with a
    deterministic per-channel sine wave (distinct frequency per channel), so
    tests can assert on actual sample values, not just response shapes.
    sfreq=1000Hz (a realistic iEEG rate) so the 50/100/150Hz display notch
    filter's highest frequency stays safely below Nyquist -- unlike real
    recordings, an unrealistically low test sample rate (e.g. 200Hz) would
    put 150Hz above Nyquist and make iirnotch reject it."""
    n_samples = int(sfreq * duration_sec)
    t = np.arange(n_samples) / sfreq
    ch_names = list(ch_names) if ch_names else [f"CH{i + 1}" for i in range(n_channels)]
    n_channels = len(ch_names)
    data = np.stack([50e-6 * np.sin(2 * np.pi * (2 + i) * t) for i in range(n_channels)])
    info = mne.create_info(ch_names, sfreq=sfreq, ch_types="eeg")
    raw = mne.io.RawArray(data, info, verbose=False)
    raw.export(path, fmt="edf", overwrite=True, verbose=False)
    return ch_names, sfreq


def _create_subject_with_edf(name, ch_names=None):
    r = client.post("/subjects", json={"name": name})
    sid = r.json()["id"]
    edf_path = f"/tmp/{name}_synth.edf"
    ch_names, sfreq = _make_synthetic_edf(edf_path, ch_names=ch_names)
    with open(edf_path, "rb") as f:
        r = client.post(
            f"/subjects/{sid}/upload?file_type=edf",
            files={"file": (f"{name}.edf", f.read(), "application/octet-stream")},
        )
    os.remove(edf_path)
    artifact_id = r.json()["id"]
    return sid, artifact_id, ch_names, sfreq


# ---------------------------------------------------------------------------
# FreeBrowse tab -- .nvd document generation + whitelisted file serving.
# ---------------------------------------------------------------------------

def _write_freebrowse_surface(name, hemi_label):
    import nibabel.freesurfer as fsio

    surf_dir = os.path.join(settings.SUBJECTS_DIR, name, "surf")
    os.makedirs(surf_dir, exist_ok=True)
    tri_vertices = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    tri_faces = np.array([[0, 1, 2]], dtype=np.int32)
    fsio.write_geometry(os.path.join(surf_dir, hemi_label), tri_vertices, tri_faces)


def test_freebrowse_document_lists_only_existing_files():
    _make_synthetic_recon_subject("FreeBrowseDocTest")  # writes mri/orig.mgz + mri/brainmask.mgz
    mri_dir = os.path.join(settings.SUBJECTS_DIR, "FreeBrowseDocTest", "mri")
    with open(os.path.join(mri_dir, "aseg.mgz"), "wb") as f:
        f.write(b"not a real mgz -- existence is all build_nvd_document checks")
    _write_freebrowse_surface("FreeBrowseDocTest", "lh.pial")
    _write_freebrowse_surface("FreeBrowseDocTest", "rh.pial")
    # deliberately no lh.white/rh.white/CT_Reg -- those keys must be absent below

    r = client.post("/subjects", json={"name": "FreeBrowseDocTest"})
    sid = r.json()["id"]

    r = client.get(f"/subjects/{sid}/freebrowse.nvd")
    assert r.status_code == 200
    doc = r.json()

    volume_keys = {v["url"].rsplit("/", 1)[-1] for v in doc["imageOptionsArray"]}
    assert volume_keys == {"orig", "brainmask", "aseg"}
    mesh_keys = {m["url"].rsplit("/", 1)[-1] for m in doc["meshes"]}
    assert mesh_keys == {"lh_pial", "rh_pial"}

    orig_entry = next(v for v in doc["imageOptionsArray"] if v["url"].endswith("/orig"))
    # /api/ prefix is load-bearing, not cosmetic: these urls are fetched
    # directly by the browser once the .nvd loads, so they must match
    # nginx's `location /api/` proxy prefix (v2/docker/nginx.conf) the same
    # way v2/web/src/api/client.ts's API_BASE does -- omitting it doesn't
    # 404, it silently falls through to nginx's SPA route instead.
    assert orig_entry["url"] == f"/api/subjects/{sid}/freebrowse/files/orig"
    assert orig_entry["name"] == "orig.mgz"
    assert orig_entry["colormap"] == "gray"
    lh_pial_entry = next(m for m in doc["meshes"] if m["url"].endswith("/lh_pial"))
    assert lh_pial_entry["url"] == f"/api/subjects/{sid}/freebrowse/files/lh_pial"


def test_freebrowse_document_empty_for_subject_with_no_files():
    r = client.post("/subjects", json={"name": "FreeBrowseEmptyTest"})
    sid = r.json()["id"]

    r = client.get(f"/subjects/{sid}/freebrowse.nvd")
    assert r.status_code == 200
    doc = r.json()
    assert doc["imageOptionsArray"] == []
    assert doc["meshes"] == []


def test_freebrowse_file_serves_whitelisted_key():
    _make_synthetic_recon_subject("FreeBrowseFileTest")
    r = client.post("/subjects", json={"name": "FreeBrowseFileTest"})
    sid = r.json()["id"]

    r = client.get(f"/subjects/{sid}/freebrowse/files/orig")
    assert r.status_code == 200
    with open(os.path.join(settings.SUBJECTS_DIR, "FreeBrowseFileTest", "mri", "orig.mgz"), "rb") as f:
        assert r.content == f.read()


def test_freebrowse_file_rejects_unknown_key():
    r = client.post("/subjects", json={"name": "FreeBrowseUnknownKeyTest"})
    sid = r.json()["id"]
    r = client.get(f"/subjects/{sid}/freebrowse/files/bogus")
    assert r.status_code == 404


def test_freebrowse_file_404_when_file_missing():
    # Recon exists (orig.mgz/brainmask.mgz present) but this subject never had
    # a CT registered -- ct_reg is a known key, just not present on disk.
    _make_synthetic_recon_subject("FreeBrowseMissingFileTest")
    r = client.post("/subjects", json={"name": "FreeBrowseMissingFileTest"})
    sid = r.json()["id"]
    r = client.get(f"/subjects/{sid}/freebrowse/files/ct_reg")
    assert r.status_code == 404


def test_edf_meta():
    sid, artifact_id, ch_names, sfreq = _create_subject_with_edf("EdfMetaTest")

    r = client.get(f"/subjects/{sid}/edf/{artifact_id}/meta")
    assert r.status_code == 200
    meta = r.json()
    assert meta["fs"] == sfreq
    assert meta["channels"] == ch_names
    assert meta["n_samples"] == int(sfreq * 10.0)
    assert meta["duration_sec"] == pytest.approx(10.0, abs=0.01)
    assert meta["amplitude_range"]["min"] < 0 < meta["amplitude_range"]["max"]

    # cached into Artifact.meta_json on first call -- second call should
    # return identical values, not recompute or error.
    r2 = client.get(f"/subjects/{sid}/edf/{artifact_id}/meta")
    assert r2.json() == meta

    r = client.get(f"/subjects/{sid}/edf/999999/meta")
    assert r.status_code == 404


def test_edf_meta_flags_auxiliary_channels():
    # The web client excludes these from the working set on load: every module
    # re-references to the mean of the channels it is given, and a Nihon Kohden
    # DC input (mV) or mark word (unitless, so read raw) arrives orders of
    # magnitude above a microvolt contact and swamps the average.
    names = ["A1", "A2", "X'12", "REF1", "DC01", "EKG1", "UNUSED248", "MARK", "E"]
    sid, artifact_id, _, _ = _create_subject_with_edf("EdfMetaAuxTest", ch_names=names)

    meta = client.get(f"/subjects/{sid}/edf/{artifact_id}/meta").json()
    assert meta["aux_channels"] == ["REF1", "DC01", "EKG1", "UNUSED248", "MARK", "E"]

    # Served from Artifact.meta_json on the second call, where the field is
    # derived rather than stored -- so it must survive the cache hit.
    assert client.get(f"/subjects/{sid}/edf/{artifact_id}/meta").json() == meta


def test_edf_meta_reports_no_aux_when_no_channel_follows_the_convention():
    # CH1..CH4 matches no contact name, so the naming rule does not apply to
    # this recording. Reporting all four would have the client exclude every
    # channel and analyse nothing.
    sid, artifact_id, _, _ = _create_subject_with_edf("EdfMetaNoConventionTest")
    meta = client.get(f"/subjects/{sid}/edf/{artifact_id}/meta").json()
    assert meta["channels"] == ["CH1", "CH2", "CH3", "CH4"]
    assert meta["aux_channels"] == []


def _parse_edf_window_binary(content: bytes) -> dict:
    """Independent decode of GET .../window's binary body (see
    app/services/edf.py's pack_edf_window / WINDOW_MAGIC and
    v2/web/src/lib/parseEdfWindowBinary.ts for the format), so these tests
    verify the actual wire contract rather than just round-tripping through
    the same packer they're testing."""
    assert content[:8] == b"BQEDFW01"
    fs, start, end, filtered, band_low, band_high, n_channels, n_samples, channels_len = struct.unpack_from(
        "<dddBffIII", content, 8
    )
    offset = 8 + struct.calcsize("<dddBffIII")
    channels = json.loads(content[offset : offset + channels_len].decode("utf-8"))
    offset += channels_len
    flat = np.frombuffer(content, dtype="<f4", count=n_channels * n_samples, offset=offset)
    data = flat.reshape(n_channels, n_samples)
    return {
        "fs": fs,
        "start": start,
        "end": end,
        "filtered": bool(filtered),
        "band_low": band_low if filtered else None,
        "band_high": band_high if filtered else None,
        "channels": channels,
        "data": data,
    }


def test_edf_window_unfiltered_matches_raw_samples():
    sid, artifact_id, ch_names, sfreq = _create_subject_with_edf("EdfWindowTest")

    r = client.get(f"/subjects/{sid}/edf/{artifact_id}/window?start=1.0&end=2.0")
    assert r.status_code == 200
    body = _parse_edf_window_binary(r.content)
    assert body["channels"] == ch_names
    assert body["filtered"] is False
    assert body["fs"] == sfreq
    data = body["data"]
    assert data.shape == (len(ch_names), int(round((2.0 - 1.0) * sfreq)))

    # Cross-check against directly re-reading the same resolved file -- proves
    # the endpoint is really slicing that window, not returning arbitrary data.
    edf_dir = os.path.join(settings.SUBJECTS_DIR, "EdfWindowTest", "edf")
    resolved_path = os.path.join(edf_dir, os.listdir(edf_dir)[0])
    raw = mne.io.read_raw_edf(resolved_path, preload=True, stim_channel=None)
    i0, i1 = raw.time_as_index([1.0, 2.0])
    expected = raw.get_data()[:, i0:i1]
    np.testing.assert_allclose(data, expected, atol=1e-6)


def test_edf_window_filtered_matches_filter_for_display():
    from app.sigproc.filters import filter_for_display

    sid, artifact_id, ch_names, sfreq = _create_subject_with_edf("EdfFilterTest")

    band_low, band_high = 1.0, 40.0
    r = client.get(
        f"/subjects/{sid}/edf/{artifact_id}/window"
        f"?start=3.0&end=5.0&band_low={band_low}&band_high={band_high}"
    )
    assert r.status_code == 200
    body = _parse_edf_window_binary(r.content)
    assert body["filtered"] is True
    assert body["band_low"] == band_low
    assert body["band_high"] == band_high
    data = body["data"]

    # Reproduce the pad-then-filter-then-trim behavior directly against the
    # same resolved file and confirm the endpoint matches exactly -- the
    # regression test for "filtering must happen on a padded range" (unpadded
    # filtering would show edge artifacts at every window boundary).
    edf_dir = os.path.join(settings.SUBJECTS_DIR, "EdfFilterTest", "edf")
    resolved_path = os.path.join(edf_dir, os.listdir(edf_dir)[0])
    raw = mne.io.read_raw_edf(resolved_path, preload=True, stim_channel=None)
    pad = 2.0
    duration = raw.times[-1]
    pad_start = max(0.0, 3.0 - pad)
    pad_end = min(duration, 5.0 + pad)
    i0, i1 = raw.time_as_index([pad_start, pad_end])
    padded = raw.get_data()[:, i0:i1]
    filtered = filter_for_display(padded, sfreq, band_low, band_high)
    trim0 = int(round((3.0 - pad_start) * sfreq))
    trim1 = trim0 + int(round((5.0 - 3.0) * sfreq))
    expected = filtered[:, trim0:trim1]

    np.testing.assert_allclose(data, expected, atol=1e-8)


def test_edf_window_channel_filter_and_limits():
    sid, artifact_id, ch_names, sfreq = _create_subject_with_edf("EdfLimitsTest")

    # channel subsetting preserves file order regardless of request order
    r = client.get(f"/subjects/{sid}/edf/{artifact_id}/window?start=0&end=1&channels=CH3,CH1")
    assert r.status_code == 200
    body = _parse_edf_window_binary(r.content)
    assert body["channels"] == ["CH1", "CH3"]
    assert len(body["data"]) == 2

    # Derived, not a literal: this used to hardcode a span that silently became
    # legal when the cap was raised.
    over_cap = MAX_WINDOW_SECONDS + 1
    r = client.get(f"/subjects/{sid}/edf/{artifact_id}/window?start=0&end={over_cap}")
    assert r.status_code == 400

    r = client.get(f"/subjects/{sid}/edf/{artifact_id}/window?start=5&end=5")
    assert r.status_code == 400

    r = client.get(f"/subjects/{sid}/edf/999999/window?start=0&end=1")
    assert r.status_code == 404


def test_edf_window_rejects_unknown_channel_names():
    """A stale channel list used to silently return fewer traces than requested."""
    sid, artifact_id, _, _ = _create_subject_with_edf("EdfUnknownChan")
    r = client.get(f"/subjects/{sid}/edf/{artifact_id}/window?start=0&end=1&channels=CH1,NOPE")
    assert r.status_code == 400
    assert "NOPE" in r.json()["detail"]


def test_edf_window_mains_freq_notches_the_frequency_it_is_given():
    """The displayed traces must be notched at the recording's mains frequency.

    The endpoint previously had no mains parameter at all, so every trace the
    operator reviewed was notched at 50/100/150 Hz regardless of where the data
    was recorded -- removing clean signal and leaving the real interference.
    """
    name = "EdfMains"
    r = client.post("/subjects", json={"name": name})
    sid = r.json()["id"]

    # A recording that carries real 60 Hz interference on one channel, so the
    # assertion is about the notch working -- not merely about the output
    # changing. The other channels stay quiet so the common-average reference
    # does not cancel it.
    sfreq, duration = 1000.0, 10.0
    t = np.arange(int(sfreq * duration)) / sfreq
    data = np.zeros((4, t.size))
    data[0] = 50e-6 * np.sin(2 * np.pi * 60 * t) + 50e-6 * np.sin(2 * np.pi * 10 * t)
    info = mne.create_info([f"CH{i + 1}" for i in range(4)], sfreq=sfreq, ch_types="eeg")
    edf_path = f"/tmp/{name}_mains.edf"
    mne.io.RawArray(data, info, verbose=False).export(edf_path, fmt="edf", overwrite=True, verbose=False)
    with open(edf_path, "rb") as f:
        artifact_id = client.post(
            f"/subjects/{sid}/upload?file_type=edf",
            files={"file": (f"{name}.edf", f.read(), "application/octet-stream")},
        ).json()["id"]
    os.remove(edf_path)

    base = f"/subjects/{sid}/edf/{artifact_id}/window?start=2&end=8&band_low=1&band_high=200"

    def power_at(hz, mains):
        body = _parse_edf_window_binary(client.get(f"{base}&mains_freq={mains}").content)
        sig = np.asarray(body["data"][0])
        spec = np.abs(np.fft.rfft(sig))
        freqs = np.fft.rfftfreq(sig.size, 1.0 / body["fs"])
        sel = (freqs >= hz - 2) & (freqs <= hz + 2)
        return float(np.sum(spec[sel] ** 2))

    assert power_at(60, 60) < power_at(60, 50) * 0.05, (
        "notching at 60Hz must remove the 60Hz interference that notching at 50Hz leaves"
    )
    assert power_at(10, 60) > power_at(60, 60) * 100, "the 10Hz signal of interest must survive"


def test_edf_meta_cache_is_invalidated_when_the_file_changes():
    """meta_json was cached forever, so a replaced recording kept serving the
    previous file's channel list and amplitude range."""
    sid, artifact_id, ch_names, _ = _create_subject_with_edf("EdfMetaCache")

    first = client.get(f"/subjects/{sid}/edf/{artifact_id}/meta").json()
    assert first["channels"] == ch_names

    # Overwrite the resolved copy with a recording that has different channels.
    from app.models import Artifact, Subject
    from app.services.edf_common import resolve_edf_path
    with SessionLocal() as db:
        subject = db.query(Subject).filter(Subject.id == sid).first()
        artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
        resolved = resolve_edf_path(subject, artifact)
    _make_synthetic_edf(resolved, n_channels=6)

    second = client.get(f"/subjects/{sid}/edf/{artifact_id}/meta").json()
    assert len(second["channels"]) == 6, "stale cached metadata was served"


@patch("subprocess.Popen", side_effect=MockPopen)
def test_export_import_subject_roundtrip(mock_run):
    # Build a subject with a real on-disk footprint: recon (writes the
    # SUBJECTS_DIR/<name> tree + recon artifacts) plus an EDF upload under
    # recv/<name>.
    name = "ExportImport"
    r = client.post("/subjects", json={"name": name})
    sid = r.json()["id"]

    client.post(
        f"/subjects/{sid}/upload?file_type=t1",
        files={"file": ("t1.nii.gz", b"fake T1", "application/octet-stream")},
    )
    edf_path = f"/tmp/{name}_synth.edf"
    _make_synthetic_edf(edf_path)
    with open(edf_path, "rb") as f:
        client.post(
            f"/subjects/{sid}/upload?file_type=edf",
            files={"file": (f"{name}.edf", f.read(), "application/octet-stream")},
        )
    os.remove(edf_path)

    r = client.post(f"/subjects/{sid}/recon", json={"recon_type": "recon-all"})
    run_job(r.json()["id"])

    artifacts_before = client.get(f"/subjects/{sid}/artifacts").json()
    kinds_before = sorted(a["kind"] for a in artifacts_before)

    # Export -> job produces a subject_export artifact, download streams a zip.
    r = client.post(f"/subjects/{sid}/export")
    assert r.status_code == 200
    export_job_id = r.json()["id"]
    run_job(export_job_id)

    r = client.get(f"/subjects/{sid}/artifacts?kind=subject_export")
    assert len(r.json()) == 1

    r = client.get(f"/subjects/{sid}/export/download")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")
    zip_bytes = r.content
    assert zipfile.is_zipfile(io.BytesIO(zip_bytes))

    # Wipe the subject entirely, as the user would before moving servers.
    r = client.delete(f"/subjects/{sid}")
    assert r.status_code == 200
    assert not os.path.isdir(os.path.join(settings.SUBJECTS_DIR, name))

    # Import the same archive back.
    r = client.post(
        "/subjects/import",
        files={"file": (f"{name}.zip", zip_bytes, "application/zip")},
    )
    assert r.status_code == 200
    body = r.json()
    new_sid = body["subject"]["id"]
    assert body["subject"]["name"] == name
    assert body["subject"]["recon_type"] == "recon-all"
    run_job(body["job"]["id"])

    # Subject is fully restored: files back on disk, subject_dir set, and every
    # exportable artifact re-registered (subject_export itself lives under
    # DATA_ROOT/exports, not the captured trees, so it is not re-registered).
    r = client.get(f"/subjects/{new_sid}")
    assert r.json()["subject_dir"] == os.path.join(settings.SUBJECTS_DIR, name)
    assert os.path.isdir(os.path.join(settings.SUBJECTS_DIR, name))

    artifacts_after = client.get(f"/subjects/{new_sid}/artifacts").json()
    kinds_after = sorted(a["kind"] for a in artifacts_after)
    # recon_zip lives at SUBJECTS_DIR/<name>.zip (a sibling, not inside the
    # captured tree) so it is intentionally dropped; everything else returns.
    expected = sorted(k for k in kinds_before if k != "recon_zip")
    assert kinds_after == expected

    # A re-import while the name is still taken is rejected.
    r = client.post(
        "/subjects/import",
        files={"file": (f"{name}.zip", zip_bytes, "application/zip")},
    )
    assert r.status_code == 409

    # A non-export zip is rejected with a helpful 400.
    bogus = io.BytesIO()
    with zipfile.ZipFile(bogus, "w") as zf:
        zf.writestr("hello.txt", "not a subject")
    r = client.post(
        "/subjects/import",
        files={"file": ("bogus.zip", bogus.getvalue(), "application/zip")},
    )
    assert r.status_code == 400


# --- mains-frequency + Nyquist regression tests -------------------------------
#
# The legacy code hardcoded a 50/100/150 Hz notch and let band_high default to
# 500. On a 1 kHz North-American recording that combination both crashed
# (band_high == Nyquist) and, once past the crash, notched clean signal while
# leaving the real 60 Hz harmonics in place. For HFO detection that matters
# most: 180 and 240 Hz fall inside the 80-250 Hz ripple band, so an un-notched
# harmonic is counted as an HFO.


def test_mains_harmonics_50hz_matches_legacy_series():
    """The 50 Hz default must reproduce the legacy notch exactly, so the
    already-verified S1 HFO/EI output does not drift."""
    from app.sigproc.filters import mains_harmonics

    # filter_for_display's legacy series was np.arange(50, 151, 50)
    np.testing.assert_array_equal(
        mains_harmonics(50.0, 1000.0, up_to=50.0 * 3.5), np.arange(50, 151, 50)
    )
    # interictal's legacy series was np.arange(50, band_high + 10, 50)
    np.testing.assert_array_equal(
        mains_harmonics(50.0, 1000.0, up_to=250.0 + 10.0), np.arange(50, 260, 50)
    )


def test_mains_harmonics_60hz_covers_ripple_band():
    from app.sigproc.filters import mains_harmonics

    h = mains_harmonics(60.0, 1000.0, up_to=250.0 + 12.0)
    np.testing.assert_array_equal(h, [60, 120, 180, 240])


def test_mains_harmonics_never_reaches_nyquist():
    """iirnotch needs 0 < w < 1; the legacy 250 Hz harmonic on a 500 Hz
    recording would have been exactly Nyquist and raised."""
    from app.sigproc.filters import mains_harmonics

    h = mains_harmonics(50.0, 500.0, up_to=260.0)
    assert len(h) > 0
    assert h.max() < 250.0


def test_clamp_band_allows_band_high_at_nyquist():
    """band_high=500 on a 1 kHz recording means 'everything', not an error."""
    from scipy.signal import butter

    from app.sigproc.filters import clamp_band

    low, high = clamp_band(1.0, 500.0, 1000.0)
    assert high < 500.0
    butter(5, [low / 500.0, high / 500.0], btype="bandpass")  # must not raise

    with pytest.raises(ValueError):
        clamp_band(0.0, 300.0, 1000.0)
    with pytest.raises(ValueError):
        clamp_band(300.0, 100.0, 1000.0)


def test_filter_for_display_band_high_at_nyquist_does_not_raise():
    from app.sigproc.filters import filter_for_display

    fs = 1000.0
    data = np.random.RandomState(0).randn(4, 4000)
    out = filter_for_display(data, fs, 1.0, 500.0)  # the old default
    assert out.shape == data.shape
    assert np.isfinite(out).all()


def test_notch_removes_the_selected_mains_harmonics_only():
    """A 60 Hz recording filtered with mains=50 keeps its 180 Hz interference;
    with mains=60 it loses it. 180 Hz sits inside the HFO ripple band."""
    from app.services.interictal import notch_filt
    from app.sigproc.filters import mains_harmonics

    fs = 1000.0
    t = np.arange(0, 10, 1 / fs)
    rng = np.random.RandomState(0)
    sig = rng.randn(2, len(t)) + 20 * np.sin(2 * np.pi * 180 * t)

    def power_at(x, f0):
        spec = np.abs(np.fft.rfft(x, axis=-1)) ** 2
        fr = np.fft.rfftfreq(x.shape[-1], 1 / fs)
        return spec[:, (fr > f0 - 1) & (fr < f0 + 1)].max()

    before = power_at(sig, 180)
    kept = notch_filt(sig, fs, mains_harmonics(50.0, fs, up_to=260.0))
    removed = notch_filt(sig, fs, mains_harmonics(60.0, fs, up_to=262.0))

    assert power_at(kept, 180) > before * 0.5  # 50 Hz series never touches 180
    assert power_at(removed, 180) < before * 0.01  # 60 Hz series kills it


def test_filter_for_display_keeps_a_single_channel_intact():
    """The common average of one channel is that channel, so subtracting it
    returned exactly zero -- which is what the EI chart's per-channel
    drill-down (one channel per request) plotted, for every channel."""
    from app.sigproc.filters import filter_for_display

    fs = 1000.0
    t = np.arange(0, 2, 1 / fs)
    sig = (50e-6 * np.sin(2 * np.pi * 10 * t))[None, :]

    out = filter_for_display(sig, fs, 1.0, 300.0, mains_freq=60.0)
    assert np.abs(out).max() > 1e-6, "a single-channel window must not be zeroed"

    # Two channels still get the common-average reference.
    pair = np.vstack([sig[0], -sig[0]])
    out2 = filter_for_display(pair, fs, 1.0, 300.0, mains_freq=60.0)
    assert np.abs(out2).max() > 1e-6


def test_edf_window_single_channel_is_not_zeroed():
    sid, artifact_id, ch_names, _ = _create_subject_with_edf("EdfWindowSingleChn")
    r = client.get(
        f"/subjects/{sid}/edf/{artifact_id}/window",
        params={"start": 1, "end": 3, "channels": ch_names[0], "band_low": 1, "band_high": 300},
    )
    assert r.status_code == 200
    parsed = _parse_edf_window_binary(r.content)
    assert np.abs(parsed["data"]).max() > 0


def test_edf_meta_amplitude_range_ignores_auxiliary_channels():
    """A mark word or DC input reads orders of magnitude above a contact (5.4e4
    vs 1e-4 on a real Nihon Kohden export). The viewer derives its row pitch
    from this range, so including them drew every trace as a flat line."""
    r = client.post("/subjects", json={"name": "EdfMetaRangeAux"})
    sid = r.json()["id"]

    fs = 1000.0
    t = np.arange(int(fs * 5.0)) / fs
    data = np.vstack([
        50e-6 * np.sin(2 * np.pi * 10 * t),   # A1 -- a contact, microvolts
        50e-6 * np.sin(2 * np.pi * 12 * t),   # A2
        5.0 * np.sin(2 * np.pi * 1 * t),      # MARK -- 1e5x either contact
    ])
    info = mne.create_info(["A1", "A2", "MARK"], sfreq=fs, ch_types="eeg")
    path = "/tmp/EdfMetaRangeAux.edf"
    mne.io.RawArray(data, info, verbose=False).export(path, fmt="edf", overwrite=True, verbose=False)
    with open(path, "rb") as f:
        r = client.post(
            f"/subjects/{sid}/upload?file_type=edf",
            files={"file": ("EdfMetaRangeAux.edf", f.read(), "application/octet-stream")},
        )
    os.remove(path)

    meta = client.get(f"/subjects/{sid}/edf/{r.json()['id']}/meta").json()
    assert meta["aux_channels"] == ["MARK"]
    assert meta["amplitude_range"]["max"] < 1e-3, "MARK must not set the display range"


def test_ei_request_rejects_invalid_windows():
    sid, artifact_id, _, _ = _create_subject_with_edf("EiValidateTest")
    base = {
        "baseline_start": 0.0, "baseline_end": 1.0,
        "target_start": 2.0, "target_end": 3.0,
    }
    r = client.post(f"/subjects/{sid}/ictal/{artifact_id}/ei", json=base)
    assert r.status_code == 200

    for bad in (
        {"baseline_end": 0.0},          # end == start
        {"target_end": 1.0},            # end before start
        {"baseline_start": -1.0},       # negative
        {"band_low": 0.0},              # non-positive band
        {"band_low": 400.0, "band_high": 100.0},  # inverted band
        {"mains_freq": -1.0},
    ):
        r = client.post(f"/subjects/{sid}/ictal/{artifact_id}/ei", json={**base, **bad})
        assert r.status_code == 422, f"expected 422 for {bad}, got {r.status_code}"


def _run_ei_and_load(sid, artifact_id, **overrides):
    body = {
        "baseline_start": 0.0, "baseline_end": 3.0,
        "target_start": 4.0, "target_end": 9.0,
        **overrides,
    }
    r = client.post(f"/subjects/{sid}/ictal/{artifact_id}/ei", json=body)
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    run_job(job_id)
    state = client.get(f"/jobs/{job_id}").json()["state"]
    return job_id, state


def test_ei_honours_remain_chns():
    """Channels deleted in the trace viewer must leave the computation.

    Before this was wired up, the ictal page's channel deletions only filtered
    the plot: every channel in the file stayed in the EI ranking AND in the
    common-average reference, so a dropped REF/EKG trace still leaked into
    every other channel.
    """
    sid, artifact_id, ch_names, _ = _create_subject_with_edf("EiRemainChns")

    _, state = _run_ei_and_load(sid, artifact_id, reference="car")
    assert state == "finished"
    full = client.get(f"/subjects/{sid}/ictal/{artifact_id}/ei-result").json()
    assert full["chn_names"] == ch_names

    keep = ch_names[:2]
    _, state = _run_ei_and_load(sid, artifact_id, reference="car", remain_chns=keep)
    assert state == "finished"
    subset = client.get(f"/subjects/{sid}/ictal/{artifact_id}/ei-result").json()
    assert subset["chn_names"] == keep, "the result must only contain the kept channels"


def test_ei_result_reports_the_windows_it_was_computed_over():
    """The web drill-down (raw trace + spectrogram per channel) needs the target
    window; reading it from the page's live selection meant the chart's bars
    went dead after a reload."""
    sid, artifact_id, _, _ = _create_subject_with_edf("EiResultParams")
    _, state = _run_ei_and_load(sid, artifact_id, band_low=2.0, band_high=200.0)
    assert state == "finished"

    params = client.get(f"/subjects/{sid}/ictal/{artifact_id}/ei-result").json()["params"]
    assert (params["target_start"], params["target_end"]) == (4.0, 9.0)
    assert (params["baseline_start"], params["baseline_end"]) == (0.0, 3.0)
    assert (params["band_low"], params["band_high"]) == (2.0, 200.0)


def test_ei_rejects_unknown_remain_chns():
    sid, artifact_id, ch_names, _ = _create_subject_with_edf("EiRemainChnsBad")
    _, state = _run_ei_and_load(sid, artifact_id, remain_chns=[ch_names[0], "NOT_A_CHANNEL"])
    assert state == "failed", "a typo'd channel name must fail loudly, not be silently ignored"


def test_edf_meta_cache_keeps_the_uploaded_filename():
    """The meta cache used to overwrite meta_json wholesale, erasing the
    filename recorded at upload -- the web recording picker then had nothing to
    label the entry with but its artifact id."""
    sid, artifact_id, _, _ = _create_subject_with_edf("EdfNameTest")
    assert client.get(f"/subjects/{sid}/edf/{artifact_id}/meta").status_code == 200

    artifact = next(a for a in client.get(f"/subjects/{sid}/artifacts").json() if a["id"] == artifact_id)
    assert artifact["meta_json"]["original_filename"] == "EdfNameTest.edf"


def test_delete_edf_recording_removes_derived_results():
    sid, artifact_id, _, _ = _create_subject_with_edf("EdfDeleteTest")
    job_id, state = _run_ei_and_load(sid, artifact_id)
    assert state == "finished"

    db = SessionLocal()
    try:
        ei_path = os.path.join(settings.DATA_ROOT, db.query(Artifact).filter(
            Artifact.job_id == job_id, Artifact.kind == "ei_npz").first().rel_path)
        raw_path = os.path.join(settings.DATA_ROOT, db.query(Artifact).filter(
            Artifact.id == artifact_id).first().rel_path)
    finally:
        db.close()
    working_copy = os.path.join(settings.SUBJECTS_DIR, "EdfDeleteTest", "edf", os.path.basename(raw_path))
    assert os.path.exists(ei_path) and os.path.exists(raw_path) and os.path.exists(working_copy)

    r = client.delete(f"/subjects/{sid}/edf/{artifact_id}")
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted_artifacts": 2, "deleted_jobs": 1}

    for path in (ei_path, raw_path, working_copy):
        assert not os.path.exists(path), f"{path} survived the delete"
    assert client.get(f"/subjects/{sid}/artifacts").json() == []
    assert client.get(f"/jobs/{job_id}").status_code == 404
    assert client.get(f"/subjects/{sid}/edf/{artifact_id}/meta").status_code == 404


def test_delete_edf_refused_while_a_job_is_active():
    sid, artifact_id, _, _ = _create_subject_with_edf("EdfDeleteBusy")
    r = client.post(f"/subjects/{sid}/ictal/{artifact_id}/ei", json={
        "baseline_start": 0.0, "baseline_end": 3.0, "target_start": 4.0, "target_end": 9.0,
    })
    assert r.status_code == 200  # queued, deliberately never run

    r = client.delete(f"/subjects/{sid}/edf/{artifact_id}")
    assert r.status_code == 409
    assert client.get(f"/subjects/{sid}/edf/{artifact_id}/meta").status_code == 200


def test_upload_edf_rejects_duplicate_name_unless_overwrite():
    """Same name == same path on disk, so a second artifact row would share the
    first one's file."""
    sid, artifact_id, _, _ = _create_subject_with_edf("EdfDupTest")
    edf_path = "/tmp/EdfDupTest_second.edf"
    _make_synthetic_edf(edf_path)
    with open(edf_path, "rb") as f:
        payload = f.read()
    os.remove(edf_path)
    upload = {"files": {"file": ("EdfDupTest.edf", payload, "application/octet-stream")}}

    r = client.post(f"/subjects/{sid}/upload?file_type=edf", **upload)
    assert r.status_code == 409
    assert [a["id"] for a in client.get(f"/subjects/{sid}/artifacts").json()] == [artifact_id]

    r = client.post(f"/subjects/{sid}/upload?file_type=edf&overwrite=true", **upload)
    assert r.status_code == 200, r.text
    # Exactly one recording, the replacement -- not two rows sharing one file.
    artifacts = client.get(f"/subjects/{sid}/artifacts").json()
    assert [a["id"] for a in artifacts] == [r.json()["id"]]


def test_hfo_request_rejects_invalid_window_and_accepts_none():
    sid, artifact_id, _, _ = _create_subject_with_edf("HfoValidateTest")

    # blank window == whole recording, the legacy behavior
    r = client.post(f"/subjects/{sid}/interictal/{artifact_id}/hfo", json={})
    assert r.status_code == 200
    job_id = r.json()["id"]
    db = SessionLocal()
    try:
        params = db.query(Job).filter(Job.id == job_id).first().params_json
        assert params["start_time"] is None and params["end_time"] is None
        assert params["mains_freq"] == 50.0
        db.query(Job).filter(Job.id == job_id).delete()
        db.commit()
    finally:
        db.close()

    for bad in (
        {"start_time": -1.0},
        {"start_time": 10.0, "end_time": 5.0},
        {"mains_freq": -1.0},
        {"band_low": 250.0, "band_high": 80.0},
    ):
        r = client.post(f"/subjects/{sid}/interictal/{artifact_id}/hfo", json=bad)
        assert r.status_code == 422, f"expected 422 for {bad}, got {r.status_code}"


# ---------------------------------------------------------------------------
# Per-recording compute params + annotation surfacing
# ---------------------------------------------------------------------------

def test_upload_edf_extracts_annotations():
    """EDF+ TAL annotations (seizure markings, clinical events) must be parsed
    at upload time and exposed via the params endpoint -- see
    docs/bella_ictal_ei_vs_annotation_discrepancy.md for why the raw
    annotations, not an auto-picked "the" onset, are what gets surfaced."""
    name = "EdfAnnotations"
    r = client.post("/subjects", json={"name": name})
    sid = r.json()["id"]

    sfreq, duration = 1000.0, 20.0
    t = np.arange(int(sfreq * duration)) / sfreq
    data = np.stack([50e-6 * np.sin(2 * np.pi * 5 * t), 50e-6 * np.sin(2 * np.pi * 7 * t)])
    info = mne.create_info(["CH1", "CH2"], sfreq=sfreq, ch_types="eeg")
    raw = mne.io.RawArray(data, info, verbose=False)
    raw.set_annotations(mne.Annotations(
        onset=[5.0, 8.5, 12.0],
        duration=[0.0, 1.0, 0.0],
        description=["EEG onset", "SZ 1P", "clinical onset"],
    ))
    edf_path = f"/tmp/{name}.edf"
    raw.export(edf_path, fmt="edf", overwrite=True, verbose=False)
    with open(edf_path, "rb") as f:
        r = client.post(
            f"/subjects/{sid}/upload?file_type=edf",
            files={"file": (f"{name}.edf", f.read(), "application/octet-stream")},
        )
    os.remove(edf_path)
    artifact_id = r.json()["id"]

    params = client.get(f"/subjects/{sid}/edf/{artifact_id}/params").json()
    assert params["edf_artifact_id"] == artifact_id
    assert params["ictal_params"] is None
    assert params["interictal_params"] is None
    assert [a["description"] for a in params["annotations"]] == ["EEG onset", "SZ 1P", "clinical onset"]
    assert [a["onset"] for a in params["annotations"]] == pytest.approx([5.0, 8.5, 12.0], abs=0.01)


def test_recording_params_empty_when_nothing_saved():
    """No 404 -- a recording with no compute history yet and no annotations
    channel must still return a well-formed (empty) shape."""
    sid, artifact_id, _, _ = _create_subject_with_edf("RecParamsEmpty")
    params = client.get(f"/subjects/{sid}/edf/{artifact_id}/params").json()
    assert params["ictal_params"] is None
    assert params["interictal_params"] is None
    assert params["annotations"] == []


def test_ei_compute_saves_recording_params():
    sid, artifact_id, _, _ = _create_subject_with_edf("EiSavesParams")
    body = {
        "baseline_start": 0.0, "baseline_end": 3.0,
        "target_start": 4.0, "target_end": 9.0,
        "band_low": 2.0, "band_high": 200.0, "mains_freq": 60.0,
    }
    r = client.post(f"/subjects/{sid}/ictal/{artifact_id}/ei", json=body)
    assert r.status_code == 200

    params = client.get(f"/subjects/{sid}/edf/{artifact_id}/params").json()
    saved = params["ictal_params"]
    for key, value in body.items():
        assert saved[key] == value
    assert params["interictal_params"] is None


def test_hfo_compute_saves_recording_params():
    sid, artifact_id, _, _ = _create_subject_with_edf("HfoSavesParams")
    body = {"band_low": 80.0, "band_high": 250.0, "mains_freq": 60.0, "rel_thresh": 3.0}
    r = client.post(f"/subjects/{sid}/interictal/{artifact_id}/hfo", json=body)
    assert r.status_code == 200

    params = client.get(f"/subjects/{sid}/edf/{artifact_id}/params").json()
    saved = params["interictal_params"]
    for key, value in body.items():
        assert saved[key] == value
    assert params["ictal_params"] is None


def test_delete_edf_recording_removes_recording_params():
    sid, artifact_id, _, _ = _create_subject_with_edf("RecParamsDeleteTest")
    r = client.post(f"/subjects/{sid}/ictal/{artifact_id}/ei", json={
        "baseline_start": 0.0, "baseline_end": 3.0, "target_start": 4.0, "target_end": 9.0,
    })
    assert r.status_code == 200
    run_job(r.json()["id"])

    db = SessionLocal()
    try:
        assert db.query(RecordingParams).filter(RecordingParams.edf_artifact_id == artifact_id).count() == 1
    finally:
        db.close()

    r = client.delete(f"/subjects/{sid}/edf/{artifact_id}")
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        assert db.query(RecordingParams).filter(RecordingParams.edf_artifact_id == artifact_id).count() == 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# EI reference montage (CAR vs bipolar)
# ---------------------------------------------------------------------------

def test_ei_defaults_to_bipolar_and_reports_pair_names():
    """Bipolar beats CAR on SEEG SOZ localization, so it is the default for new
    jobs -- see docs/ei_reference_montage_ds004100.md."""
    sid, artifact_id, ch_names, _ = _create_subject_with_edf("EiBipolarDefault")

    job_id, state = _run_ei_and_load(sid, artifact_id)
    assert state == "finished"

    params = client.get(f"/jobs/{job_id}").json()["params_json"]
    assert params["reference"] == "bipolar"

    result = client.get(f"/subjects/{sid}/ictal/{artifact_id}/ei-result").json()
    assert result["diagnostics"]["reference"] == "bipolar"
    # CH1..CH4 -> three derivations, and the analysed names are pairs.
    assert result["chn_names"] == ["CH1-CH2", "CH2-CH3", "CH3-CH4"]
    assert len(result["ei"]) == len(result["chn_names"])
    # ... but the archive still carries a contact-keyed projection for fusion.
    assert result["contact_names"] == ch_names
    assert len(result["ei_by_contact"]) == len(ch_names)


def test_ei_job_saved_before_reference_existed_replays_as_car():
    """Retrying a legacy job must reproduce its original result, not silently
    switch method."""
    sid, artifact_id, ch_names, _ = _create_subject_with_edf("EiLegacyReplay")

    job = Job(
        subject_id=sid,
        job_type="ei_compute",
        state="queued",
        params_json={
            "edf_artifact_id": artifact_id,
            "baseline_start": 0.0, "baseline_end": 3.0,
            "target_start": 4.0, "target_end": 9.0,
        },  # no "reference" key, as written before this field existed
        progress_pct=0.0,
        progress_message="Job queued",
    )
    with SessionLocal() as db:
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id

    run_job(job_id)
    assert client.get(f"/jobs/{job_id}").json()["state"] == "finished"
    result = client.get(f"/subjects/{sid}/ictal/{artifact_id}/ei-result").json()
    assert result["diagnostics"]["reference"] == "car"
    assert result["chn_names"] == ch_names


def test_bipolar_preview_lists_the_derivations_that_would_be_built():
    sid, artifact_id, ch_names, _ = _create_subject_with_edf("EiBipolarPreview")

    r = client.get(f"/subjects/{sid}/ictal/{artifact_id}/bipolar-preview")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n_contacts"] == len(ch_names)
    assert body["pairs"] == ["CH1-CH2", "CH2-CH3", "CH3-CH4"]
    assert body["n_pairs"] == 3
    assert body["unpairable"] == []
    assert body["skipped_gaps"] == []


def test_bipolar_preview_reflects_excluded_channels():
    """Dropping a contact legitimately opens a numbering gap, and the preview
    must show the montage that would actually result."""
    sid, artifact_id, ch_names, _ = _create_subject_with_edf("EiPreviewExclusions")

    keep = [ch_names[0], ch_names[1], ch_names[3]]  # CH1, CH2, CH4 -- CH3 dropped
    r = client.get(
        f"/subjects/{sid}/ictal/{artifact_id}/bipolar-preview",
        params=[("remain_chns", c) for c in keep],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pairs"] == ["CH1-CH2"], "CH2-CH4 spans two contact spacings"
    assert body["skipped_gaps"] == [{"shaft": "CH", "between": ["CH2", "CH4"]}]


def test_edf_window_serves_bipolar_derivations():
    """The result panel's drill-down asks for the channel it charted, which under
    bipolar is a pair name -- the window endpoint has to resolve it."""
    sid, artifact_id, _, _ = _create_subject_with_edf("EdfWindowBipolar")

    r = client.get(
        f"/subjects/{sid}/edf/{artifact_id}/window",
        params={"start": 0.0, "end": 2.0, "channels": "CH1-CH2", "reference": "bipolar"},
    )
    assert r.status_code == 200, r.text
    window = _parse_edf_window_binary(r.content)
    assert window["channels"] == ["CH1-CH2"]
    assert window["data"].shape[0] == 1

    bad = client.get(
        f"/subjects/{sid}/edf/{artifact_id}/window",
        params={"start": 0.0, "end": 2.0, "channels": "CH1", "reference": "bipolar"},
    )
    assert bad.status_code == 400
    assert "unknown derivation" in bad.json()["detail"]


# --- clinical review filtering (the Clinical EEG view) -----------------------


def test_edf_window_review_filter_matches_the_nk_recipe():
    """Pins the review path to show_edf.py's exact recipe: reference, mains
    notch, causal one-pole TC high-pass, then a butter(4) high cut."""
    from app.sigproc.filters import filter_for_review

    sid, artifact_id, _, sfreq = _create_subject_with_edf("EdfReviewRecipe")

    tc, hicut = 0.1, 70.0
    r = client.get(
        f"/subjects/{sid}/edf/{artifact_id}/window"
        f"?start=3.0&end=5.0&tc={tc}&band_high={hicut}"
    )
    assert r.status_code == 200
    body = _parse_edf_window_binary(r.content)
    assert body["filtered"] is True
    # band_low carries the equivalent corner in Hz, so the wire format is unchanged.
    assert body["band_low"] == pytest.approx(1.0 / (2 * math.pi * tc), rel=1e-5)
    assert body["band_high"] == hicut

    edf_dir = os.path.join(settings.SUBJECTS_DIR, "EdfReviewRecipe", "edf")
    resolved_path = os.path.join(edf_dir, os.listdir(edf_dir)[0])
    raw = mne.io.read_raw_edf(resolved_path, preload=True, stim_channel=None)
    duration = raw.times[-1]
    pad_start = max(0.0, 3.0 - 2.0)
    pad_end = min(duration, 5.0 + 2.0)
    i0, i1 = raw.time_as_index([pad_start, pad_end])
    filtered = filter_for_review(raw.get_data()[:, i0:i1], sfreq, tc=tc, hicut=hicut)
    trim0 = int(round((3.0 - pad_start) * sfreq))
    expected = filtered[:, trim0:trim0 + int(round(2.0 * sfreq))]

    np.testing.assert_allclose(body["data"], expected, atol=1e-8)


def test_edf_window_tc_keeps_slow_activity_the_bandpass_eats():
    """The reason the review filter exists: a zero-phase butter(5) high-pass at
    the same corner flattens slow activity that NK's one-pole TC preserves."""
    from app.sigproc.filters import filter_for_display, filter_for_review

    fs = 1000.0
    t = np.arange(0, 12.0, 1 / fs)
    # 0.5Hz slow wave, well below a 1.6Hz corner, on two channels so CAR is a no-op
    # only if they differ -- use 'none' to isolate the high-pass behaviour.
    x = np.vstack([np.sin(2 * np.pi * 0.5 * t), np.zeros_like(t)]) * 100e-6

    review = filter_for_review(x, fs, tc=0.1, hicut=70.0, mains_freq=0, reference="none")
    display = filter_for_display(x, fs, 1.0 / (2 * math.pi * 0.1), 70.0,
                                 mains_freq=0, reference="none")

    mid = slice(int(4 * fs), int(8 * fs))
    assert np.ptp(review[0, mid]) > 3 * np.ptp(display[0, mid])


def test_edf_window_review_seam_is_continuous_for_a_long_tc():
    """Regression test for the pad rule: the causal high-pass transient decays
    as exp(-t/tc), so a long TC needs a left pad scaled to it. Without that,
    panning shows a DC step at every window boundary."""
    sid, artifact_id, _, _ = _create_subject_with_edf("EdfReviewSeam")

    a = client.get(f"/subjects/{sid}/edf/{artifact_id}/window?start=4.0&end=6.0&tc=2.0")
    b = client.get(f"/subjects/{sid}/edf/{artifact_id}/window?start=5.0&end=6.0&tc=2.0")
    assert a.status_code == 200 and b.status_code == 200
    overlap = _parse_edf_window_binary(a.content)["data"][:, -1000:]
    np.testing.assert_allclose(overlap, _parse_edf_window_binary(b.content)["data"],
                               atol=1e-9)


def test_edf_window_review_accepts_a_high_cut_alone_and_both_filters_off():
    sid, artifact_id, _, _ = _create_subject_with_edf("EdfReviewOff")

    # tc=0 means "low cut off", not "not review mode": the traces are still
    # referenced and notched, so switching every filter off cannot make them jump.
    r = client.get(f"/subjects/{sid}/edf/{artifact_id}/window?start=0&end=2&tc=0&band_high=30")
    assert r.status_code == 200
    body = _parse_edf_window_binary(r.content)
    # 0.0 is how the binary header has always spelled "no cut" (pack_edf_window
    # encodes None that way), so review mode needs no new wire field.
    assert body["filtered"] is True and body["band_low"] == 0.0
    assert body["band_high"] == 30.0

    r = client.get(f"/subjects/{sid}/edf/{artifact_id}/window?start=0&end=2&tc=0")
    assert r.status_code == 200
    body = _parse_edf_window_binary(r.content)
    assert body["filtered"] is True
    assert body["band_low"] == 0.0 and body["band_high"] == 0.0


def test_edf_window_review_high_cut_above_nyquist_is_ignored():
    sid, artifact_id, _, _ = _create_subject_with_edf("EdfReviewNyq")
    r = client.get(f"/subjects/{sid}/edf/{artifact_id}/window?start=0&end=2&tc=0.1&band_high=9000")
    assert r.status_code == 200
    assert _parse_edf_window_binary(r.content)["filtered"] is True


def test_edf_window_rejects_band_low_with_tc_and_a_lone_band_low():
    sid, artifact_id, _, _ = _create_subject_with_edf("EdfReviewReject")

    r = client.get(f"/subjects/{sid}/edf/{artifact_id}/window?start=0&end=2&tc=0.1&band_low=1")
    assert r.status_code == 400
    assert "two ways to ask for a low cut" in r.json()["detail"]

    # band_low without band_high used to silently return unfiltered data.
    r = client.get(f"/subjects/{sid}/edf/{artifact_id}/window?start=0&end=2&band_low=1")
    assert r.status_code == 400


def test_edf_meta_reports_the_recording_start_time():
    """The clinical view labels its axis HH:MM:SS from this."""
    from app.services.edf import _meas_date_iso

    sid, artifact_id, _, _ = _create_subject_with_edf("EdfMeasDate")
    meta = client.get(f"/subjects/{sid}/edf/{artifact_id}/meta").json()
    assert "meas_date" in meta
    assert meta["meas_date"] is None or datetime.fromisoformat(meta["meas_date"])

    # mne's EDF exporter substitutes a default start date, so the None branch
    # cannot be produced by a synthetic file -- exercise it directly.
    assert _meas_date_iso(SimpleNamespace(info={"meas_date": None})) is None
    dt = datetime(2019, 3, 14, 7, 24, 35, tzinfo=timezone.utc)
    assert _meas_date_iso(SimpleNamespace(info={"meas_date": dt})) == dt.isoformat()
