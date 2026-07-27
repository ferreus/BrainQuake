from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app, runner as global_runner

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate_runner():
    global_runner._jobs.clear()
    yield
    global_runner._jobs.clear()


class FakeProc:
    """Stand-in for subprocess.Popen: exposes just the .poll()/.terminate()
    interface app.runner.JobRunner relies on, with returncode settable by
    the test to simulate the process finishing."""

    def __init__(self, cmd, *args, **kwargs):
        self.cmd = cmd
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if self.returncode is None:
            self.returncode = -15


@pytest.fixture
def fake_popen():
    created = []

    def _factory(cmd, *args, **kwargs):
        proc = FakeProc(cmd)
        created.append(proc)
        return proc

    with patch("app.runner.subprocess.Popen", side_effect=_factory):
        yield created


def _payload(job_id="1"):
    return {
        "job_id": job_id,
        "t1_path": "/data/subjects/recv/x/xT1.nii.gz",
        "sid": "x",
        "sd": "/data/subjects",
        "license_path": "/usr/local/freesurfer/license.txt",
        "threads": 4,
        "device": "cpu",
    }


def test_healthz():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_post_creates_job_and_returns_202(fake_popen):
    resp = client.post("/jobs", json=_payload())
    assert resp.status_code == 202
    assert resp.json() == {"job_id": "1", "state": "running"}
    assert len(fake_popen) == 1


def test_get_status_running_then_finished(fake_popen):
    client.post("/jobs", json=_payload())

    resp = client.get("/jobs/1")
    assert resp.status_code == 200
    assert resp.json()["state"] == "running"

    fake_popen[0].returncode = 0
    resp = client.get("/jobs/1")
    assert resp.json()["state"] == "finished"


def test_get_status_failed_on_nonzero_returncode(fake_popen):
    client.post("/jobs", json=_payload())
    fake_popen[0].returncode = 1

    resp = client.get("/jobs/1")
    assert resp.json() == {"state": "failed", "progress_message": None, "returncode": 1}


def test_get_unknown_job_returns_404():
    resp = client.get("/jobs/does-not-exist")
    assert resp.status_code == 404


def test_get_log_unknown_job_returns_404():
    resp = client.get("/jobs/does-not-exist/log")
    assert resp.status_code == 404


def test_delete_terminates_process_and_is_idempotent(fake_popen):
    client.post("/jobs", json=_payload())

    resp = client.delete("/jobs/1")
    assert resp.status_code == 200
    assert fake_popen[0].terminated is True

    # Already terminated -- calling delete again must not error.
    resp = client.delete("/jobs/1")
    assert resp.status_code == 200


def test_delete_unknown_job_returns_404():
    resp = client.delete("/jobs/does-not-exist")
    assert resp.status_code == 404


def test_post_beyond_capacity_returns_429(fake_popen):
    with patch.object(global_runner, "max_concurrent", 1):
        first = client.post("/jobs", json=_payload("1"))
        second = client.post("/jobs", json=_payload("2"))

    assert first.status_code == 202
    assert second.status_code == 429


def test_duplicate_job_id_returns_409(fake_popen):
    payload = _payload("dup")
    first = client.post("/jobs", json=payload)
    second = client.post("/jobs", json=payload)

    assert first.status_code == 202
    assert second.status_code == 409
