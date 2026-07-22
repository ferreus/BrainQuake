import json
import os
import shutil
import struct
import subprocess
import numpy as np
import mne
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Setup test DB URL before importing app modules. DATA_ROOT must be isolated
# too, not just SUBJECTS_DIR -- the autouse fixture below rmtree's
# {DATA_ROOT}/recv and {DATA_ROOT}/logs before/after every test, and without
# this override that resolves to the real dev server's upload storage.
os.environ["DB_URL"] = "sqlite:///./data/test_brainquake.db"
os.environ["SUBJECTS_DIR"] = "./data/test_subjects"
os.environ["DATA_ROOT"] = "./data/test_data_root"

from app.main import app
from app.db import Base, engine, SessionLocal, get_db
from app.config import settings
from app.models import Subject, Job, Artifact
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
        json={"name": "TestPatient", "recon_type": "recon-all"},
    )
    assert response.status_code == 200
    subject_data = response.json()
    assert subject_data["name"] == "TestPatient"
    subject_id = subject_data["id"]

    # SUBJECTS_DIR/<name> must NOT exist yet -- recon-all/fast-surfer/infant_recon_all
    # treat that directory merely existing (regardless of contents) as "this subject
    # already has a prior run" when given -i, and refuse. The recon job itself
    # creates it (see services/recon.py's run_recon_job) immediately before invoking
    # the recon tool, not subject creation.
    assert not os.path.exists(os.path.join(settings.SUBJECTS_DIR, "TestPatient"))
    assert os.path.exists(
        os.path.join(settings.DATA_ROOT, "recv", "TestPatient"))

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
        settings.DATA_ROOT, "recv", "TestPatient", "fslresults",
        "TestPatientintracranial.nii.gz",
    )
    assert os.path.exists(intracranial)

    legacy_ct = os.path.join(
        settings.SUBJECTS_DIR, "TestPatient", "fslresults",
        "TestPatientCT_Reg.nii.gz",
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

    with patch("app.workers.jobs_worker.run_recon_job", side_effect=JobCancelledError("cancelled mid-run")):
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
    assert by_label[1]["centroid"] == [0.0, 0.0, 0.5]
    assert by_label[2]["voxel_count"] == 1
    assert by_label[2]["centroid"] == [3.0, 3.0, 3.0]

    # 404 before detect() has ever run
    r2 = client.post("/subjects", json={"name": "NoLabels"})
    sid2 = r2.json()["id"]
    r = client.get(f"/subjects/{sid2}/electrodes/labels-summary")
    assert r.status_code == 404


def _make_synthetic_edf(path, n_channels=4, sfreq=1000.0, duration_sec=10.0):
    """Writes a real, re-readable EDF file (via mne + edfio) with a
    deterministic per-channel sine wave (distinct frequency per channel), so
    tests can assert on actual sample values, not just response shapes.
    sfreq=1000Hz (a realistic iEEG rate) so the 50/100/150Hz display notch
    filter's highest frequency stays safely below Nyquist -- unlike real
    recordings, an unrealistically low test sample rate (e.g. 200Hz) would
    put 150Hz above Nyquist and make iirnotch reject it."""
    n_samples = int(sfreq * duration_sec)
    t = np.arange(n_samples) / sfreq
    ch_names = [f"CH{i + 1}" for i in range(n_channels)]
    data = np.stack([50e-6 * np.sin(2 * np.pi * (2 + i) * t) for i in range(n_channels)])
    info = mne.create_info(ch_names, sfreq=sfreq, ch_types="eeg")
    raw = mne.io.RawArray(data, info, verbose=False)
    raw.export(path, fmt="edf", overwrite=True, verbose=False)
    return ch_names, sfreq


def _create_subject_with_edf(name):
    r = client.post("/subjects", json={"name": name})
    sid = r.json()["id"]
    edf_path = f"/tmp/{name}_synth.edf"
    ch_names, sfreq = _make_synthetic_edf(edf_path)
    with open(edf_path, "rb") as f:
        r = client.post(
            f"/subjects/{sid}/upload?file_type=edf",
            files={"file": (f"{name}.edf", f.read(), "application/octet-stream")},
        )
    os.remove(edf_path)
    artifact_id = r.json()["id"]
    return sid, artifact_id, ch_names, sfreq


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
    from app.services.signal_filters import filter_for_display

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

    r = client.get(f"/subjects/{sid}/edf/{artifact_id}/window?start=0&end=100")
    assert r.status_code == 400

    r = client.get(f"/subjects/{sid}/edf/{artifact_id}/window?start=5&end=5")
    assert r.status_code == 400

    r = client.get(f"/subjects/{sid}/edf/999999/window?start=0&end=1")
    assert r.status_code == 404
