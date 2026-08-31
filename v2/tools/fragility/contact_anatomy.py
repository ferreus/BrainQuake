#!/usr/bin/env python3
"""Anatomical label for every contact in a 3D Slicer .mrb, plus per-shaft geometry.

Answers two questions the fragility result raised: what is shaft D actually in,
and where does it sit relative to the clinical onset shafts A and I. The second
matters because an anterior temporal lobectomy is defined by how far back it
goes, so a shaft's y (anterior-posterior) position decides whether a standard
resection would have reached it.

    python contact_anatomy.py "datasets/Bella Seeg.mrb" --subject Bella \
        --subjects-dir data/subjects -o data/fragility/contact_anatomy.csv

Coordinates go through the server's own parse_mrb, so the LPS/RAS flip, the ITK
transform direction and the scanner->tkreg conversion stay in one place.
"""

import argparse
import csv
import os
import sys
from collections import defaultdict
from types import SimpleNamespace

import numpy as np


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mrb")
    p.add_argument("--subject", default="Bella")
    p.add_argument("--subjects-dir", default="data/subjects")
    p.add_argument("--radius-mm", type=float, default=3.0)
    p.add_argument("-o", "--out")
    a = p.parse_args()

    # app.config reads this at import time.
    os.environ["SUBJECTS_DIR"] = os.path.abspath(a.subjects_dir)
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "server")))

    from app.services.anatomy import find_segmentation, label_points
    from app.services.electrodes import parse_mrb

    subject = SimpleNamespace(name=a.subject)
    contacts, diag = parse_mrb(a.mrb, subject)
    print(f"parsed {diag['n_points']} contacts / {diag['n_electrodes']} electrodes "
          f"from node {diag['node_name']!r}")
    print(f"  coordinate system {diag['coordinate_system']}, transform "
          f"{diag['transform_used']}, in-brain {diag['in_brain_fraction']:.0%}")
    for w in diag["warnings"]:
        print(f"  WARNING: {w}")

    seg_path, seg_rel = find_segmentation(subject)
    print(f"  segmentation: {seg_rel}\n")

    labelled = label_points(
        [(c["electrode"], c["contact_index"], (c["x"], c["y"], c["z"])) for c in contacts],
        seg_path, radius_mm=a.radius_mm)

    rows = []
    for e in labelled:
        near = e["nearest_structure"]
        rows.append({
            "contact": e["name"], "shaft": e["electrode"], "index": e["contact_index"],
            "x": e["x"], "y": e["y"], "z": e["z"],
            "label": "OUT-OF-VOLUME" if e.get("out_of_volume") else e["label_name"],
            "nearest_grey": near["label_name"] if near else None,
            "dist_mm": near["distance_mm"] if near else None,
        })

    # Per-shaft summary. y is anterior(+)/posterior(-) in tkreg RAS.
    by_shaft = defaultdict(list)
    for r in rows:
        by_shaft[r["shaft"]].append(r)

    print(f"{'shaft':>6} {'n':>3} {'y (A/P)':>16} {'x (L/R)':>9}   dominant grey labels")
    print("-" * 96)
    for sh in sorted(by_shaft, key=lambda s: -np.mean([r["y"] for r in by_shaft[s]])):
        rs = by_shaft[sh]
        ys = [r["y"] for r in rs]
        xs = [r["x"] for r in rs]
        tally = defaultdict(int)
        for r in rs:
            if r["nearest_grey"]:
                tally[r["nearest_grey"]] += 1
        top = sorted(tally.items(), key=lambda kv: -kv[1])[:3]
        lab = ", ".join(f"{k.replace('ctx-', '')}({v})" for k, v in top) or "-"
        print(f"{sh:>6} {len(rs):3d} {np.mean(ys):7.1f} [{min(ys):5.0f},{max(ys):4.0f}] "
              f"{np.mean(xs):8.1f}   {lab}")

    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
