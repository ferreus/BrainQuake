import csv
import logging
import math
import os

import numpy as np
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Artifact, Job, Subject
from app.services.job_control import check_cancelled
from app.services.recon import register_artifact

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


def _by_channel(chn_names, values, source):
    """Channel name -> value, refusing the two ways this can quietly go wrong."""
    if len(chn_names) != len(values):
        raise ValueError(
            f"{source}: {len(chn_names)} channel names but {len(values)} values"
        )
    duplicates = {n for n in chn_names if chn_names.count(n) > 1}
    if duplicates:
        # dict() would keep only the last of each, silently discarding results.
        raise ValueError(f"{source}: duplicate channel names {sorted(duplicates)}")
    return dict(zip(chn_names, values, strict=True))


def load_ei_result(ei_result_path):
    data = np.load(ei_result_path, allow_pickle=True)
    chn_names = [str(n) for n in data['chn_names']]
    return _by_channel(chn_names, data['ei'], "EI result")


def load_hi_result(hi_result_path):
    data = np.load(hi_result_path, allow_pickle=True)
    chn_names = [str(n) for n in data['file_chnsNames']]
    return _by_channel(chn_names, data['file_highEventsCount'], "HFO result")


from app.sigproc.fusion import describe_name_overlap, fuse_ei_hfo_scores, rank_pct

build_result_table = fuse_ei_hfo_scores


def save_csv(rows, out_csv):
    with open(out_csv, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _latest_artifact(db: Session, subject_id: int, kind: str):
    return (
        db.query(Artifact)
        .filter(Artifact.subject_id == subject_id, Artifact.kind == kind)
        .order_by(Artifact.created_at.desc())
        .first()
    )


def run_soz_fuse_job(db: Session, job: Job, log_file):
    subject = db.query(Subject).filter(Subject.id == job.subject_id).first()
    if not subject:
        raise ValueError("Subject not found")

    params = job.params_json or {}

    elec_xyz_path = os.path.join(settings.SUBJECTS_DIR, subject.name, "fslresults", "chnXyzDict.npy")
    if not os.path.exists(elec_xyz_path):
        raise FileNotFoundError(f"{elec_xyz_path} not found. Run electrode segment() first.")

    ei_artifact_id = params.get("ei_artifact_id")
    ei_artifact = (
        db.query(Artifact).filter(Artifact.id == ei_artifact_id, Artifact.subject_id == subject.id).first()
        if ei_artifact_id else _latest_artifact(db, subject.id, "ei_npz")
    )
    if not ei_artifact:
        raise FileNotFoundError("No ei_npz artifact found for this subject. Run ictal EI computation first.")

    hi_artifact_id = params.get("hi_artifact_id")
    hi_artifact = (
        db.query(Artifact).filter(Artifact.id == hi_artifact_id, Artifact.subject_id == subject.id).first()
        if hi_artifact_id else _latest_artifact(db, subject.id, "hfo_npz")
    )
    if not hi_artifact:
        raise FileNotFoundError("No hfo_npz artifact found for this subject. Run interictal HFO computation first.")

    job.progress_pct = 30.0
    job.progress_message = "Loading electrode/EI/HI results"
    db.commit()

    contact_xyz = load_contact_xyz(elec_xyz_path)
    ei_by_chan = load_ei_result(os.path.join(settings.DATA_ROOT, ei_artifact.rel_path))
    hi_by_chan = load_hi_result(os.path.join(settings.DATA_ROOT, hi_artifact.rel_path))

    check_cancelled(db, job)
    job.progress_pct = 70.0
    job.progress_message = "Ranking contacts"
    db.commit()

    overlaps = [
        describe_name_overlap(contact_xyz, ei_by_chan, "EI"),
        describe_name_overlap(contact_xyz, hi_by_chan, "HFO"),
    ]
    for o in overlaps:
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
        ei_o, hi_o = overlaps
        raise ValueError(
            "No contact name matched any EEG channel name, so there is nothing to rank. "
            f"Contacts look like {ei_o['unmatched_contacts'][:5]}; "
            f"EI channels look like {ei_o['unused_channels'][:5]}; "
            f"HFO channels look like {hi_o['unused_channels'][:5]}. "
            "The electrode labels and the EDF channel labels need to use the same convention."
        )

    rows = build_result_table(contact_xyz, ei_by_chan, hi_by_chan)

    out_csv = os.path.join(settings.SUBJECTS_DIR, subject.name, "soz_result.csv")
    save_csv(rows, out_csv)
    register_artifact(db, subject.id, job.id, "soz_csv", out_csv)

    job.progress_pct = 95.0
    ranked = sum(1 for r in rows if r["combined_score"] > 0)
    job.progress_message = f"Ranked {ranked}/{len(rows)} contacts (rest had no EI or HFO value)"
    db.commit()


def load_result_rows(csv_path):
    with open(csv_path, newline='') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    for row in rows:
        for k in ('x', 'y', 'z', 'ei', 'hi', 'ei_percentile', 'hi_percentile', 'combined_score'):
            if row.get(k) not in (None, ''):
                val = float(row[k])
                # A contact present in the electrode map but missing from the EI
                # or HI results ranks as NaN (build_result_table). NaN is not
                # JSON-compliant (Starlette renders with allow_nan=False), so
                # emit null instead and let the client show it as "missing".
                row[k] = None if math.isnan(val) else val
        for k in ('suspect_ei', 'suspect_hi'):
            row[k] = row.get(k) == 'True'
    return rows
