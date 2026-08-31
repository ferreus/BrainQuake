"""End-to-end SOZ fusion: real artifacts on disk, the real job, the real CSV.

The unit tests in test_soz_matching.py pin the maths; these pin the wiring --
that runs are found by artifact kind, that a selection narrows what is fused,
and that the CSV round-trips back through the result endpoint with the right
per-process columns.
"""
import os
import shutil

import numpy as np
import pytest

# The same sandbox test_api.py uses: app.config reads these once at import, so
# whichever test module imports first has to win for both.
os.environ.setdefault("DB_URL", "sqlite:///./data/test_brainquake.db")
os.environ.setdefault("SUBJECTS_DIR", "./data/test_subjects")
os.environ.setdefault("DATA_ROOT", "./data/test_data_root")

from app.config import settings
from app.db import Base, SessionLocal, engine
from app.models import Artifact, Job, Subject
from app.services.soz import load_result_rows, run_soz_fuse_job
from app.sigproc.ei import save_ei_result
from app.sigproc.fragility import save_fragility_result

CONTACTS = ["A1", "A2", "A3", "A4"]


@pytest.fixture
def db():
    engine.dispose()
    for d in (settings.SUBJECTS_DIR, os.path.join(settings.DATA_ROOT, "recv")):
        if os.path.exists(d):
            shutil.rmtree(d)
        os.makedirs(d, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _subject_with_electrodes(db):
    subject = Subject(name="fusetest")
    db.add(subject)
    db.commit()
    db.refresh(subject)

    fsl = os.path.join(settings.SUBJECTS_DIR, subject.name, "fslresults")
    os.makedirs(fsl, exist_ok=True)
    # chnXyzDict is {shaft: (n_contacts, 3)}; contacts are named shaft + 1-based index.
    np.save(
        os.path.join(fsl, "chnXyzDict.npy"),
        {"A": np.array([[float(i), 0.0, 0.0] for i in range(len(CONTACTS))])},
        allow_pickle=True,
    )
    return subject


def _finished_job(db, subject, job_type, params, kind, path):
    job = Job(subject_id=subject.id, job_type=job_type, state="finished", params_json=params)
    db.add(job)
    db.commit()
    db.refresh(job)
    artifact = Artifact(
        subject_id=subject.id, job_id=job.id, kind=kind,
        rel_path=os.path.relpath(path, settings.DATA_ROOT), meta_json={},
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    return artifact


def _edf_dir(subject, name):
    d = os.path.join(settings.DATA_ROOT, "recv", subject.name)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    open(path, "wb").close()
    return path


def _add_ei(db, subject, edf_name, values):
    edf = _edf_dir(subject, edf_name)
    arr = np.array(values, dtype=float)
    path = save_ei_result(edf, CONTACTS, arr, arr, arr, arr,
                          diagnostics={"reference": "car"},
                          ei_by_contact=dict(zip(CONTACTS, arr)))
    return _finished_job(db, subject, "ei_compute", {"edf_artifact_id": 1}, "ei_npz", path)


def _add_fragility(db, subject, edf_name, onset_s, values):
    edf = _edf_dir(subject, edf_name)
    result = {
        "channel_scores": dict(zip(CONTACTS, [float(v) for v in values])),
        "fragility_matrix": np.zeros((len(CONTACTS), 2)),
        "r2_per_window": np.array([0.9, 0.9]),
        "start_times": np.array([0.0, 0.5]),
        "median_r2": 0.9, "method": "extended", "highpass_hz": 0.5,
        "fs": 512.0, "win_s": 0.25, "step_s": 0.125,
    }
    path = save_fragility_result(edf, result, suffix="_t%.3f" % onset_s)
    return _finished_job(
        db, subject, "fragility_compute",
        {"edf_artifact_id": 1, "onset_s": onset_s}, "fragility_npz", path,
    )


def _fuse(db, subject, artifact_ids=None):
    job = Job(subject_id=subject.id, job_type="soz_fuse", state="running",
              params_json={"artifact_ids": artifact_ids})
    db.add(job)
    db.commit()
    db.refresh(job)
    run_soz_fuse_job(db, job, None)
    csv_path = os.path.join(settings.SUBJECTS_DIR, subject.name, "soz_result.csv")
    return job, load_result_rows(csv_path)


def test_fusing_everything_carries_every_process(db):
    subject = _subject_with_electrodes(db)
    _add_ei(db, subject, "sz1.edf", [0.9, 0.6, 0.3, 0.1])
    _add_fragility(db, subject, "sz1.edf", 100.0, [0.1, 0.3, 0.6, 0.9])
    _add_fragility(db, subject, "sz1.edf", 340.0, [0.2, 0.4, 0.5, 0.8])

    job, result = _fuse(db, subject)

    assert result["processes"] == ["ei", "fragility"]
    by_name = {r["contact"]: r for r in result["rows"]}
    assert by_name["A1"]["ei_percentile"] == 1.0
    # Both seizures counted, not just the newest.
    assert by_name["A1"]["fragility_n_runs"] == 2
    assert "3 run(s)" in job.progress_message


def test_two_seizures_of_one_recording_do_not_replace_each_other(db):
    """Fragility runs are keyed by onset; keying on the recording alone would
    silently fuse only the last seizure."""
    subject = _subject_with_electrodes(db)
    _add_fragility(db, subject, "sz1.edf", 100.0, [0.9, 0.6, 0.3, 0.1])
    _add_fragility(db, subject, "sz1.edf", 340.0, [0.1, 0.3, 0.6, 0.9])

    _, result = _fuse(db, subject)
    by_name = {r["contact"]: r for r in result["rows"]}
    # Opposite orderings average to a flat 0.5 everywhere -- only possible if
    # both runs were read.
    assert {r["fragility_percentile"] for r in result["rows"]} == {0.5}
    assert by_name["A1"]["fragility_n_runs"] == 2


def test_a_selection_narrows_what_is_fused(db):
    subject = _subject_with_electrodes(db)
    ei = _add_ei(db, subject, "sz1.edf", [0.9, 0.6, 0.3, 0.1])
    _add_fragility(db, subject, "sz1.edf", 100.0, [0.1, 0.3, 0.6, 0.9])

    _, result = _fuse(db, subject, artifact_ids=[ei.id])

    assert result["processes"] == ["ei"]
    assert result["rows"][0]["contact"] == "A1", "ranking is EI's alone"


def test_fusing_one_fragility_run_reproduces_its_own_ranking(db):
    subject = _subject_with_electrodes(db)
    _add_fragility(db, subject, "sz1.edf", 100.0, [0.1, 0.3, 0.6, 0.9])
    frag2 = _add_fragility(db, subject, "sz1.edf", 340.0, [0.9, 0.6, 0.3, 0.1])

    _, result = _fuse(db, subject, artifact_ids=[frag2.id])
    assert [r["contact"] for r in result["rows"]] == ["A1", "A2", "A3", "A4"]


def test_nothing_to_fuse_is_an_error_not_an_empty_ranking(db):
    subject = _subject_with_electrodes(db)
    with pytest.raises(FileNotFoundError, match="No finished analysis results"):
        _fuse(db, subject)


def test_a_label_mismatch_fails_instead_of_ranking_nothing(db):
    subject = _subject_with_electrodes(db)
    edf = _edf_dir(subject, "sz1.edf")
    arr = np.array([0.9, 0.6, 0.3, 0.1])
    names = ["POL A1", "POL A2", "POL A3", "POL A4"]
    path = save_ei_result(edf, names, arr, arr, arr, arr,
                          diagnostics={"reference": "car"},
                          ei_by_contact=dict(zip(names, arr)))
    _finished_job(db, subject, "ei_compute", {"edf_artifact_id": 1}, "ei_npz", path)

    with pytest.raises(ValueError, match="No contact name matched"):
        _fuse(db, subject)


def test_a_missing_artifact_id_is_refused(db):
    subject = _subject_with_electrodes(db)
    _add_ei(db, subject, "sz1.edf", [0.9, 0.6, 0.3, 0.1])
    with pytest.raises(FileNotFoundError, match=r"artifact\(s\) \[999\]"):
        _fuse(db, subject, artifact_ids=[999])
