import os
import shutil
from unittest.mock import patch

import pytest

# Same isolation as test_api.py -- harmless no-ops if test_api.py already set
# these (both files agree on the same values), but keeps this file runnable
# on its own regardless of pytest's collection order.
os.environ.setdefault("DB_URL", "sqlite:///./data/test_brainquake.db")
os.environ.setdefault("SUBJECTS_DIR", "./data/test_subjects")
os.environ.setdefault("DATA_ROOT", "./data/test_data_root")

from app.db import Base, engine, SessionLocal
from app.config import settings
from app.models import Job
from app.workers.jobs_worker import run_job

# Reuse test_api.py's already-configured TestClient (dependency_overrides is
# set on the shared `app` object at import time) and its subprocess-mocking
# helpers, instead of re-registering everything here.
from test_api import client, MockPopen, _apply_command_side_effects


@pytest.fixture(autouse=True)
def setup_and_teardown_db():
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


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _create_subject_with_t1(name):
    r = client.post("/subjects", json={"name": name})
    sid = r.json()["id"]
    client.post(
        f"/subjects/{sid}/upload?file_type=t1",
        files={"file": ("t1.nii.gz", b"fake T1", "application/octet-stream")},
    )
    return sid


def test_fastsurfer_disabled_fails_job_with_clear_message():
    sid = _create_subject_with_t1("FSDisabled")
    r = client.post(f"/subjects/{sid}/recon", json={"recon_type": "fast-surfer"})
    jid = r.json()["id"]

    with patch("app.services.fastsurfer_client.httpx.post") as mock_post:
        run_job(jid)

    assert mock_post.call_count == 0

    r = client.get(f"/jobs/{jid}")
    assert r.json()["state"] == "failed"
    assert "not enabled" in r.json()["progress_message"]


def test_fastsurfer_unreachable_fails_job_with_clear_message():
    import httpx

    sid = _create_subject_with_t1("FSUnreachable")
    r = client.post(f"/subjects/{sid}/recon", json={"recon_type": "fast-surfer"})
    jid = r.json()["id"]

    with patch.object(settings, "FASTSURFER_ENABLED", True), \
         patch("app.services.fastsurfer_client.httpx.post", side_effect=httpx.ConnectError("refused")):
        run_job(jid)

    r = client.get(f"/jobs/{jid}")
    assert r.json()["state"] == "failed"
    assert "not reachable" in r.json()["progress_message"]


@patch("subprocess.Popen", side_effect=MockPopen)
def test_fastsurfer_happy_path_polls_to_completion_and_runs_postprocessing(mock_run):
    name = "FSHappy"
    sid = _create_subject_with_t1(name)
    r = client.post(f"/subjects/{sid}/recon", json={"recon_type": "fast-surfer"})
    jid = r.json()["id"]

    posted_payloads = []
    get_call_count = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        posted_payloads.append(json)
        # Simulate FastSurfer having written its outputs, by reusing the same
        # fixture-writing logic real recon-all output would go through --
        # what matters for run_recon_job's post-processing is that
        # $SUBJECTS_DIR/<sid>/mri/orig.mgz etc. exist, not which recon
        # flavor produced them.
        _apply_command_side_effects(f"recon-all -s {json['sid']}", None)
        return FakeResponse(202, json_data={"job_id": json["job_id"], "state": "running"})

    def fake_get(url, timeout=None):
        if url.endswith("/log"):
            return FakeResponse(200, text="[Mock] FastSurfer finished\n")
        get_call_count["n"] += 1
        if get_call_count["n"] == 1:
            return FakeResponse(200, json_data={"state": "running", "progress_message": "segmenting"})
        return FakeResponse(200, json_data={"state": "finished"})

    with patch.object(settings, "FASTSURFER_ENABLED", True), \
         patch.object(settings, "FASTSURFER_POLL_INTERVAL_SECONDS", 0), \
         patch("app.services.fastsurfer_client.httpx.post", side_effect=fake_post), \
         patch("app.services.fastsurfer_client.httpx.get", side_effect=fake_get):
        run_job(jid)

    r = client.get(f"/jobs/{jid}")
    assert r.json()["state"] == "finished", r.json().get("progress_message")

    # Regression check for the --sid bug: FastSurfer must be asked to write
    # to the same subject dir recon-all/infant-surfer use ({name}), not
    # {name}fast -- otherwise post-processing silently finds nothing.
    assert posted_payloads[0]["sid"] == name

    log_resp = client.get(f"/jobs/{jid}/log")
    assert "FastSurfer finished" in log_resp.text


@patch("subprocess.Popen", side_effect=MockPopen)
def test_fastsurfer_cancellation_ends_cancelled_not_failed(mock_run):
    name = "FSCancel"
    sid = _create_subject_with_t1(name)
    r = client.post(f"/subjects/{sid}/recon", json={"recon_type": "fast-surfer"})
    jid = r.json()["id"]

    delete_calls = []

    def fake_post(url, json=None, timeout=None):
        return FakeResponse(202, json_data={"job_id": json["job_id"], "state": "running"})

    def fake_get(url, timeout=None):
        # First status poll: flip the job to cancelled out-of-band (as
        # POST /jobs/{id}/cancel would), simulating a user cancelling while
        # FastSurfer is still running remotely. The *next* loop iteration's
        # db.refresh(job) is what actually detects this.
        db = SessionLocal()
        job = db.query(Job).filter(Job.id == jid).first()
        job.state = "cancelled"
        db.commit()
        db.close()
        return FakeResponse(200, json_data={"state": "running"})

    def fake_delete(url, timeout=None):
        delete_calls.append(url)
        return FakeResponse(200)

    with patch.object(settings, "FASTSURFER_ENABLED", True), \
         patch.object(settings, "FASTSURFER_POLL_INTERVAL_SECONDS", 0), \
         patch("app.services.fastsurfer_client.httpx.post", side_effect=fake_post), \
         patch("app.services.fastsurfer_client.httpx.get", side_effect=fake_get), \
         patch("app.services.fastsurfer_client.httpx.delete", side_effect=fake_delete):
        run_job(jid)

    assert len(delete_calls) == 1

    r = client.get(f"/jobs/{jid}")
    assert r.json()["state"] == "cancelled"
