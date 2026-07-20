import os
import shutil
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Setup test DB URL before importing app modules
os.environ["DB_URL"] = "sqlite:///./data/test_brainquake.db"
os.environ["SUBJECTS_DIR"] = "./data/test_subjects"

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
# Mock subprocess.run so tests don't need FreeSurfer/FSL installed
# ---------------------------------------------------------------------------

def mock_subprocess_run(cmd, *args, **kwargs):
    """Create the expected output files for various commands."""
    stdout_file = kwargs.get("stdout")

    if "recon-all" in cmd:
        parts = cmd.split()
        subject_name = parts[parts.index("-s") + 1]
        mri_dir = os.path.join(settings.SUBJECTS_DIR, subject_name, "mri")
        os.makedirs(mri_dir, exist_ok=True)
        with open(os.path.join(mri_dir, "orig.mgz"), "w") as f:
            f.write("mock orig mgz")
        with open(os.path.join(mri_dir, "brainmask.mgz"), "w") as f:
            f.write("mock brainmask mgz")
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
        import numpy as np

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
        import numpy as np

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

    mock_res = MagicMock()
    mock_res.returncode = 0
    return mock_res


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@patch("subprocess.run", side_effect=mock_subprocess_run)
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

    assert os.path.exists(os.path.join(settings.SUBJECTS_DIR, "TestPatient"))
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


@patch("subprocess.run", side_effect=mock_subprocess_run)
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


@patch("subprocess.run", side_effect=mock_subprocess_run)
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


@patch("subprocess.run", side_effect=mock_subprocess_run)
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


@patch("subprocess.run", side_effect=mock_subprocess_run)
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
