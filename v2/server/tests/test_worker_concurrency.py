import os
import shutil
import threading
from unittest.mock import patch

import pytest

os.environ.setdefault("DB_URL", "sqlite:///./data/test_brainquake.db")
os.environ.setdefault("SUBJECTS_DIR", "./data/test_subjects")
os.environ.setdefault("DATA_ROOT", "./data/test_data_root")

from test_api import client

from app.config import settings
from app.db import Base, SessionLocal, engine
from app.models import Job
from app.workers.jobs_worker import _is_fastsurfer_job, run_job


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


def test_is_fastsurfer_job_predicate():
    fs_job = Job(job_type="recon", params_json={"recon_type": "fast-surfer"})
    normal_recon = Job(job_type="recon", params_json={"recon_type": "recon-all"})
    other_job = Job(job_type="ct_register", params_json={})
    no_params = Job(job_type="recon", params_json=None)

    assert _is_fastsurfer_job(fs_job) is True
    assert _is_fastsurfer_job(normal_recon) is False
    assert _is_fastsurfer_job(other_job) is False
    assert _is_fastsurfer_job(no_params) is False


def test_atomic_claim_prevents_double_execution():
    r = client.post("/subjects", json={"name": "RaceSubj"})
    sid = r.json()["id"]
    r = client.post(f"/subjects/{sid}/recon", json={"recon_type": "recon-all"})
    jid = r.json()["id"]

    calls = []
    calls_lock = threading.Lock()

    def fake_run_recon_job(db, job, log_file):
        with calls_lock:
            calls.append(job.id)

    with patch("app.workers.jobs_worker.run_recon_job", side_effect=fake_run_recon_job):
        t1 = threading.Thread(target=run_job, args=(jid,))
        t2 = threading.Thread(target=run_job, args=(jid,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

    # Whichever thread's atomic UPDATE commits first is the only one whose
    # claim.rowcount == 1 -- the other's claim affects 0 rows and returns
    # early without calling any handler, regardless of thread interleaving.
    assert calls == [jid]

    r = client.get(f"/jobs/{jid}")
    assert r.json()["state"] == "finished"


def test_different_subjects_execute_concurrently():
    job_ids = []
    for name in ["ConcA", "ConcB"]:
        r = client.post("/subjects", json={"name": name})
        sid = r.json()["id"]
        rj = client.post(f"/subjects/{sid}/recon", json={"recon_type": "recon-all"})
        job_ids.append(rj.json()["id"])

    # A 2-party barrier is a deterministic proof of real concurrency: if the
    # two run_job calls executed serially, the second thread would never
    # reach the barrier until the first thread's run_job had already
    # returned -- but the first thread is itself blocked at the barrier
    # waiting for the second, so a serial execution deadlocks here and this
    # test fails on the barrier timeout instead of silently passing.
    barrier = threading.Barrier(2, timeout=10)
    seen = set()
    seen_lock = threading.Lock()

    def fake_run_recon_job(db, job, log_file):
        barrier.wait()
        with seen_lock:
            seen.add(job.id)

    with patch("app.workers.jobs_worker.run_recon_job", side_effect=fake_run_recon_job):
        threads = [threading.Thread(target=run_job, args=(jid,)) for jid in job_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

    assert seen == set(job_ids)


def test_claim_skips_job_when_another_job_running_for_same_subject():
    r = client.post("/subjects", json={"name": "ExclusiveSubj"})
    sid = r.json()["id"]

    db = SessionLocal()
    running_job = Job(subject_id=sid, job_type="recon", state="running", params_json={"recon_type": "recon-all"})
    queued_job = Job(subject_id=sid, job_type="ct_register", state="queued", params_json={})
    db.add_all([running_job, queued_job])
    db.commit()
    queued_id = queued_job.id
    db.close()

    run_job(queued_id)  # must no-op: the NOT EXISTS check in the claim fails

    db = SessionLocal()
    refreshed = db.query(Job).filter(Job.id == queued_id).first()
    assert refreshed.state == "queued"
    db.close()


def test_claim_succeeds_once_conflicting_job_no_longer_running():
    r = client.post("/subjects", json={"name": "ExclusiveSubj2"})
    sid = r.json()["id"]

    db = SessionLocal()
    finished_job = Job(subject_id=sid, job_type="recon", state="finished", params_json={"recon_type": "recon-all"})
    queued_job = Job(subject_id=sid, job_type="ct_register", state="queued", params_json={})
    db.add_all([finished_job, queued_job])
    db.commit()
    queued_id = queued_job.id
    db.close()

    with patch("app.workers.jobs_worker.run_ct_register_job") as mock_handler:
        run_job(queued_id)

    mock_handler.assert_called_once()

    db = SessionLocal()
    refreshed = db.query(Job).filter(Job.id == queued_id).first()
    assert refreshed.state == "finished"
    db.close()
