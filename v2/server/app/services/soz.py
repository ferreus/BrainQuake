import csv
import logging
import math
import os

import numpy as np
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Artifact, Job, Subject
from app.services.job_control import check_cancelled
from app.services.processes import (
    PROCESS_BY_ARTIFACT_KIND,
    PROCESSES,
    latest_finished_runs,
)
from app.services.recon import register_artifact
from app.sigproc.fusion import describe_name_overlap, fuse_contact_scores, fused_processes

logger = logging.getLogger(__name__)

# Ported from soz_result.py (git tag legacy-final) -- pure fusion/ranking logic
# only; the mayavi plot_3d call was dropped (this module only produces the
# ranked contact table + CSV).


def load_contact_xyz(elec_xyz_path):
    elec_dict = np.load(elec_xyz_path, allow_pickle=True)[()]
    contact_xyz = {}
    for label, xyz in elec_dict.items():
        for i in range(xyz.shape[0]):
            contact_xyz[f"{label}{i + 1}"] = xyz[i]
    return contact_xyz


def save_csv(rows, out_csv):
    with open(out_csv, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _artifact_paths(db: Session, subject_id: int, artifact_ids):
    """{process: [result path, ...]} for the selected runs, or for every finished
    run when nothing was selected."""
    selected = {}
    if artifact_ids:
        found = db.query(Artifact).filter(
            Artifact.id.in_(artifact_ids), Artifact.subject_id == subject_id
        ).all()
        missing = set(artifact_ids) - {a.id for a in found}
        if missing:
            raise FileNotFoundError(f"artifact(s) {sorted(missing)} not found for this subject")
        for artifact in found:
            process = PROCESS_BY_ARTIFACT_KIND.get(artifact.kind)
            if not process:
                raise ValueError(
                    f"artifact {artifact.id} is a {artifact.kind}, not an analysis result"
                )
            selected.setdefault(process, []).append(artifact.rel_path)
    else:
        for process, spec in PROCESSES.items():
            for _job, artifact in latest_finished_runs(db, subject_id, spec).values():
                selected.setdefault(process, []).append(artifact.rel_path)
    return selected


def _load_scores(process, rel_paths):
    """Each run's {channel -> score}, skipping results whose file is gone."""
    spec = PROCESSES[process]
    runs = []
    for rel_path in rel_paths:
        abs_path = os.path.join(settings.DATA_ROOT, rel_path)
        if not os.path.exists(abs_path):
            logger.warning("%s result %s is missing from disk; skipping", process, rel_path)
            continue
        scores = spec.scores(spec.load(abs_path))
        runs.append({
            k: float(v) for k, v in scores.items()
            if v is not None and math.isfinite(float(v))
        })
    return runs


def run_soz_fuse_job(db: Session, job: Job, log_file):
    subject = db.query(Subject).filter(Subject.id == job.subject_id).first()
    if not subject:
        raise ValueError("Subject not found")

    params = job.params_json or {}

    elec_xyz_path = os.path.join(settings.SUBJECTS_DIR, subject.name, "fslresults", "chnXyzDict.npy")
    if not os.path.exists(elec_xyz_path):
        raise FileNotFoundError(f"{elec_xyz_path} not found. Run electrode segment() first.")

    job.progress_pct = 30.0
    job.progress_message = "Loading electrode map and analysis results"
    db.commit()

    contact_xyz = load_contact_xyz(elec_xyz_path)
    runs_by_process = {
        process: runs
        for process, rel_paths in _artifact_paths(db, subject.id, params.get("artifact_ids")).items()
        if (runs := _load_scores(process, rel_paths))
    }
    if not runs_by_process:
        raise FileNotFoundError(
            "No finished analysis results to fuse. Run EI, HFO or fragility first."
        )

    check_cancelled(db, job)
    job.progress_pct = 70.0
    job.progress_message = "Ranking contacts"
    db.commit()

    overlaps = []
    for process, runs in runs_by_process.items():
        merged = {k: v for run in runs for k, v in run.items()}
        o = describe_name_overlap(contact_xyz, merged, process)
        overlaps.append(o)
        if o["matched"] == 0:
            logger.warning(
                "%s: none of the %d contacts matched any of the %d channel names. "
                "Example contacts: %s. Example channels: %s.",
                o["kind"], o["n_contacts"], o["n_channels"],
                o["unmatched_contacts"], o["unused_channels"],
            )
        else:
            logger.info(
                "%s: matched %d/%d contacts against %d channels (unmatched e.g. %s)",
                o["kind"], o["matched"], o["n_contacts"], o["n_channels"],
                o["unmatched_contacts"],
            )

    if all(o["matched"] == 0 for o in overlaps):
        # Every value would be NaN, every combined score 0, and the CSV would
        # look perfectly well-formed while ranking nothing. Fail instead.
        first = overlaps[0]
        raise ValueError(
            "No contact name matched any EEG channel name, so there is nothing to rank. "
            f"Contacts look like {first['unmatched_contacts'][:5]}; "
            + "; ".join(f"{o['kind']} channels look like {o['unused_channels'][:5]}" for o in overlaps)
            + ". The electrode labels and the EDF channel labels need to use the same convention."
        )

    rows = fuse_contact_scores(contact_xyz, runs_by_process)

    out_csv = os.path.join(settings.SUBJECTS_DIR, subject.name, "soz_result.csv")
    save_csv(rows, out_csv)
    register_artifact(db, subject.id, job.id, "soz_csv", out_csv)

    job.progress_pct = 95.0
    ranked = sum(1 for r in rows if r["combined_score"] > 0)
    n_runs = sum(len(r) for r in runs_by_process.values())
    job.progress_message = (
        f"Ranked {ranked}/{len(rows)} contacts from {n_runs} run(s) of "
        f"{', '.join(sorted(runs_by_process))}"
    )
    db.commit()


def load_result_rows(csv_path):
    """Rows plus the processes they carry. Columns are per-process, so they are
    read by shape rather than by a fixed list of names."""
    with open(csv_path, newline='') as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        for k, v in row.items():
            if k == 'contact' or v in (None, ''):
                continue
            if k.startswith('suspect_'):
                row[k] = v == 'True'
                continue
            if k.endswith('_n_runs'):
                row[k] = int(v)
                continue
            val = float(v)
            # A contact present in the electrode map but missing from a process's
            # results ranks as NaN. NaN is not JSON-compliant (Starlette renders
            # with allow_nan=False), so emit null and let the client show it as
            # "missing".
            row[k] = None if math.isnan(val) else val
    return {"processes": fused_processes(rows), "rows": rows}
