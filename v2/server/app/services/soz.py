import os
import csv
import numpy as np
from sqlalchemy.orm import Session
from app.config import settings
from app.models import Job, Subject, Artifact
from app.services.recon import register_artifact

# Ported from BrainQuake/soz_result.py's pure fusion/ranking logic (the mayavi
# plot_3d call stays client-side per PLAN.md 2.7 -- this module only produces the
# ranked contact table + CSV).


def load_contact_xyz(elec_xyz_path):
    elec_dict = np.load(elec_xyz_path, allow_pickle=True)[()]
    contact_xyz = {}
    for label, xyz in elec_dict.items():
        for i in range(xyz.shape[0]):
            contact_xyz[f"{label}{i + 1}"] = xyz[i]
    return contact_xyz


def load_ei_result(ei_result_path):
    data = np.load(ei_result_path, allow_pickle=True)
    chn_names = [str(n) for n in data['chn_names']]
    return dict(zip(chn_names, data['ei']))


def load_hi_result(hi_result_path):
    data = np.load(hi_result_path, allow_pickle=True)
    chn_names = [str(n) for n in data['file_chnsNames']]
    return dict(zip(chn_names, data['file_highEventsCount']))


def rank_pct(values):
    values = np.asarray(values, dtype=float)
    order = np.argsort(values)
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(len(values))
    return ranks / max(len(values) - 1, 1)


def build_result_table(contact_xyz, ei_by_chan, hi_by_chan):
    names = sorted(contact_xyz.keys())
    ei_vals = np.array([ei_by_chan.get(n, np.nan) for n in names])
    hi_vals = np.array([hi_by_chan.get(n, np.nan) for n in names])

    ei_mask = ~np.isnan(ei_vals)
    hi_mask = ~np.isnan(hi_vals)
    ei_pct = np.full(len(names), np.nan)
    hi_pct = np.full(len(names), np.nan)
    if ei_mask.any():
        ei_pct[ei_mask] = rank_pct(ei_vals[ei_mask])
    if hi_mask.any():
        hi_pct[hi_mask] = rank_pct(hi_vals[hi_mask])

    stacked = np.vstack([ei_pct, hi_pct])
    valid_counts = np.sum(~np.isnan(stacked), axis=0)
    sums = np.nansum(stacked, axis=0)
    combined = np.divide(sums, valid_counts, out=np.zeros_like(sums), where=valid_counts > 0)

    ei_thresh = np.nanmean(ei_vals) + np.nanstd(ei_vals) if ei_mask.any() else np.inf
    hi_thresh = np.nanmean(hi_vals) + np.nanstd(hi_vals) if hi_mask.any() else np.inf
    suspect_ei = ei_vals > ei_thresh
    suspect_hi = hi_vals > hi_thresh

    rows = []
    for i, name in enumerate(names):
        rows.append({
            'contact': name,
            'x': contact_xyz[name][0], 'y': contact_xyz[name][1], 'z': contact_xyz[name][2],
            'ei': ei_vals[i], 'hi': hi_vals[i],
            'ei_percentile': ei_pct[i], 'hi_percentile': hi_pct[i],
            'combined_score': combined[i],
            'suspect_ei': bool(suspect_ei[i]), 'suspect_hi': bool(suspect_hi[i]),
        })
    rows.sort(key=lambda r: r['combined_score'], reverse=True)
    return rows


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

    job.progress_pct = 70.0
    job.progress_message = "Ranking contacts"
    db.commit()

    rows = build_result_table(contact_xyz, ei_by_chan, hi_by_chan)

    out_csv = os.path.join(settings.SUBJECTS_DIR, subject.name, "soz_result.csv")
    save_csv(rows, out_csv)
    register_artifact(db, subject.id, job.id, "soz_csv", out_csv)

    job.progress_pct = 95.0
    job.progress_message = f"Ranked {len(rows)} contacts"
    db.commit()


def load_result_rows(csv_path):
    with open(csv_path, newline='') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    for row in rows:
        for k in ('x', 'y', 'z', 'ei', 'hi', 'ei_percentile', 'hi_percentile', 'combined_score'):
            if row.get(k) not in (None, ''):
                row[k] = float(row[k])
        for k in ('suspect_ei', 'suspect_hi'):
            row[k] = row.get(k) == 'True'
    return rows
