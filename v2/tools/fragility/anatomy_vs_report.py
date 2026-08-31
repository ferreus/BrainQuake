#!/usr/bin/env python3
"""Score the contact-anatomy pipeline against the clinical SEEG report.

parse_mrb -> aparc+aseg has never been validated on real data: its tests use
synthetic volumes, so they prove the arithmetic, not that this subject's
contacts land where the clinicians put them. The SEEG report names electrodes
by anatomy ~30 times, which is an independent contact-level ground truth.

    python anatomy_vs_report.py "datasets/Bella Seeg.mrb" \
        --truth datasets/BellaNew/report_anatomy.csv \
        -o data/fragility/anatomy_vs_report.csv

Each claim is scored as a distance, not a boolean: how far is the contact from
the nearest voxel carrying any label the clinician's phrase admits. Inside the
named structure, a few mm (segmentation noise), or a real disagreement.

--sweep re-scores under reversed and offset contact numbering. If the pipeline
had a systematic indexing error, one of those would beat the identity mapping.
"""

import argparse
import csv
import os
import statistics
import sys
from collections import defaultdict
from types import SimpleNamespace

import numpy as np

# Clinical phrase -> the Desikan-Killiany / aseg labels it admits. Desikan has
# no sulcal parcels, so a named sulcus maps to the gyri it separates.
REGION_LABELS = {
    "amygdala": {"Amygdala"},
    "hippocampus": {"Hippocampus"},
    "temporal-pole": {"temporalpole"},
    "parahippocampal": {"parahippocampal"},
    "collateral-sulcus": {"parahippocampal", "fusiform"},
    "SFG": {"superiorfrontal"},
    "SFS": {"superiorfrontal", "rostralmiddlefrontal", "caudalmiddlefrontal"},
    "MFG": {"rostralmiddlefrontal", "caudalmiddlefrontal"},
    "IFG": {"parsopercularis", "parstriangularis", "parsorbitalis"},
    "cingulate": {"rostralanteriorcingulate", "caudalanteriorcingulate",
                  "posteriorcingulate", "isthmuscingulate"},
    "cingulate-sulcus": {"rostralanteriorcingulate", "caudalanteriorcingulate",
                         "superiorfrontal"},
    "genu-cinguli": {"rostralanteriorcingulate", "caudalanteriorcingulate"},
    "posterior-cingulate": {"posteriorcingulate", "isthmuscingulate"},
    "precuneus": {"precuneus"},
    "SPL": {"superiorparietal"},
    "supramarginal": {"supramarginal"},
    "central-sulcus": {"precentral", "postcentral"},
    "postcentral": {"postcentral"},
    "central-operculum": {"postcentral", "precentral", "supramarginal"},
    "circular-sulcus": {"insula", "parsopercularis", "parstriangularis"},
    "premotor": {"precentral", "caudalmiddlefrontal", "superiorfrontal"},
    "insula": {"insula"},
}

INF = float("inf")


def verdict(inside, d):
    if inside:
        return "in"
    if d is None:
        return "miss"
    if d <= 2.0:
        return "<=2mm"
    if d <= 5.0:
        return "<=5mm"
    return "miss"


def accepted_names(regions, side, known):
    """Full FreeSurfer label names for a claim, on the claim's own side."""
    hemi, full = ("rh", "Right") if side == "R" else ("lh", "Left")
    names = set()
    for token in regions:
        if token not in REGION_LABELS:
            raise SystemExit(f"unknown region token {token!r} -- add it to REGION_LABELS")
        for base in REGION_LABELS[token]:
            names |= {c for c in (f"ctx-{hemi}-{base}", f"{full}-{base}") if c in known}
    return names


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mrb")
    p.add_argument("--truth", default="datasets/BellaNew/report_anatomy.csv")
    p.add_argument("--subject", default="Bella")
    p.add_argument("--subjects-dir", default="data/subjects")
    p.add_argument("--radius-mm", type=float, default=3.0,
                   help="neighbourhood radius for the reported label/nearest_grey")
    p.add_argument("--max-search-mm", type=float, default=15.0,
                   help="how far to look for the clinician's structure before giving up")
    p.add_argument("--sweep", action="store_true",
                   help="also score reversed and offset contact numbering")
    p.add_argument("-o", "--out")
    a = p.parse_args()

    # app.config reads this at import time.
    os.environ["SUBJECTS_DIR"] = os.path.abspath(a.subjects_dir)
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "server")))

    import nibabel as nib
    from app.services.anatomy import (
        _neighbor_offsets, find_segmentation, label_points, load_label_lut)
    from app.services.electrodes import parse_mrb, vox2ras_tkr

    subject = SimpleNamespace(name=a.subject)
    contacts, diag = parse_mrb(a.mrb, subject)
    seg_path, seg_rel = find_segmentation(subject)
    print(f"{diag['n_points']} contacts / {diag['n_electrodes']} electrodes, node "
          f"{diag['node_name']!r}, {diag['coordinate_system']}, transform "
          f"{diag['transform_used']}, in-brain {diag['in_brain_fraction']:.0%}")
    print(f"segmentation: {seg_rel}\n")

    labelled = label_points(
        [(c["electrode"], c["contact_index"], (c["x"], c["y"], c["z"])) for c in contacts],
        seg_path, radius_mm=a.radius_mm)
    by_name = {e["name"]: e for e in labelled}
    n_contacts = defaultdict(int)
    for e in labelled:
        n_contacts[e["electrode"]] += 1

    # Invariant: primed shafts are left, unprimed right, in both the coordinate
    # sign and the label. An LPS/RAS flip shows up here first.
    flips = []
    for e in labelled:
        lab = e["label_name"] or ""
        primed = e["electrode"].endswith("'")
        got = "lh" if ("-lh-" in lab or lab.startswith("Left")) else (
              "rh" if ("-rh-" in lab or lab.startswith("Right")) else None)
        if (e["x"] < 0) != primed or (got is not None and got != ("lh" if primed else "rh")):
            flips.append(f"{e['name']} x={e['x']:.1f} {lab}")
    print(f"hemisphere invariant: {len(labelled) - len(flips)}/{len(labelled)} consistent")
    for f in flips:
        print(f"  MISMATCH {f}")

    img = nib.load(seg_path)
    data = np.asanyarray(img.dataobj)
    if data.ndim > 3:
        data = data[..., 0]
    zooms = np.array(img.header.get_zooms()[:3], dtype=float)
    inv_tkr = np.linalg.inv(vox2ras_tkr(img))
    shape = np.array(data.shape[:3])
    ids_by_name = {v: k for k, v in load_label_lut().items()}
    search = _neighbor_offsets(zooms, a.max_search_mm)

    def probe(entry, wanted_ids):
        """(is the contact's own voxel an accepted label, mm to the nearest one)."""
        xyz = np.array([entry["x"], entry["y"], entry["z"]], dtype=float)
        vox_f = (inv_tkr @ np.append(xyz, 1.0))[:3]
        inside = entry["label_id"] in wanted_ids
        nb = np.rint(vox_f).astype(int) + search
        dist = np.linalg.norm((nb - vox_f) * zooms, axis=1)
        keep = (dist <= a.max_search_mm) & np.all((nb >= 0) & (nb < shape), axis=1)
        nb, dist = nb[keep], dist[keep]
        if not len(nb):
            return inside, None
        hit = np.isin(data[nb[:, 0], nb[:, 1], nb[:, 2]].astype(int), list(wanted_ids))
        return inside, (round(float(dist[hit].min()), 2) if hit.any() else None)

    with open(a.truth, newline="", encoding="utf-8") as fh:
        claims = list(csv.DictReader(fh))

    def score(remap):
        """One row per (claim, contact). remap turns a report index into a .mrb one."""
        rows, missing = [], []
        for cl in claims:
            shaft, side = cl["shaft"], cl["side"]
            names = accepted_names(cl["regions"].split("|"), side, ids_by_name)
            wanted = {ids_by_name[n] for n in names}
            for i in range(int(cl["first"]), int(cl["last"]) + 1):
                entry = by_name.get(f"{shaft}{remap(shaft, i)}")
                if entry is None:
                    missing.append(f"{shaft}{i} ({cl['source']})")
                    continue
                inside, d = probe(entry, wanted)
                near = entry["nearest_structure"]
                rows.append({
                    "contact": entry["name"], "shaft": shaft, "index": entry["contact_index"],
                    "side": side, "claim_regions": cl["regions"], "claim_source": cl["source"],
                    "label": entry["label_name"],
                    "nearest_grey": near["label_name"] if near else None,
                    "dist_mm": near["distance_mm"] if near else None,
                    "accepted_d_mm": d, "verdict": verdict(inside, d),
                })
        return rows, missing

    def agreement(rows):
        t = defaultdict(int)
        for r in rows:
            t[r["verdict"]] += 1
        return t, t["in"] + t["<=2mm"]

    def null_rate():
        """Base rate: how often does a contact on some *other* same-side shaft
        satisfy a claim? Without this, an agreement figure means nothing."""
        hits = total = 0
        for cl in claims:
            names = accepted_names(cl["regions"].split("|"), cl["side"], ids_by_name)
            wanted = {ids_by_name[n] for n in names}
            primed = cl["shaft"].endswith("'")
            for e in labelled:
                if e["electrode"] == cl["shaft"] or e["electrode"].endswith("'") != primed:
                    continue
                inside, d = probe(e, wanted)
                total += 1
                hits += verdict(inside, d) in ("in", "<=2mm")
        return hits, total

    rows, missing = score(lambda s, i: i)

    print(f"\n{'claim':<36} {'n':>2}  {'in':>3} {'<=2':>3} {'<=5':>3} {'miss':>4}   median   worst")
    print("-" * 88)
    for cl in claims:
        sel = [r for r in rows
               if r["claim_source"] == cl["source"] and r["shaft"] == cl["shaft"]
               and r["claim_regions"] == cl["regions"]]
        t, _ = agreement(sel)
        ds = [r["accepted_d_mm"] if r["accepted_d_mm"] is not None else INF for r in sel]
        med = f"{statistics.median(ds):7.2f}" if ds and INF not in ds else "  >search"
        worst = "  >search" if INF in ds else (f"{max(ds):7.2f}" if ds else "        -")
        label = f"{cl['shaft']}{cl['first']}-{cl['last']} {cl['regions']}"
        print(f"{label:<36} {len(sel):2d}  {t['in']:3d} {t['<=2mm']:3d} "
              f"{t['<=5mm']:3d} {t['miss']:4d}  {med} {worst}")

    for m in missing:
        print(f"\n  named by the report but absent from the .mrb: {m}")

    claimed = {cl["shaft"] for cl in claims}
    print(f"\nshafts with no claim in the report: "
          f"{', '.join(sorted(set(n_contacts) - claimed)) or '(none)'}")

    t, within = agreement(rows)
    n = len(rows)
    print(f"\n{n} claim-covered contacts: {t['in']} inside the named structure, "
          f"{t['<=2mm']} within 2 mm, {t['<=5mm']} within 5 mm, {t['miss']} miss")
    print(f"agreement (inside or within 2 mm): {within}/{n} = {within / n:.0%}")
    nh, nt = null_rate()
    print(f"same-side contacts on other shafts:  {nh}/{nt} = {nh / nt:.0%}  (base rate)")

    if a.sweep:
        # A systematic indexing error would make one of these beat identity.
        print("\nindex-mapping sweep (agreement inside-or-2mm):")
        mappings = [("identity", lambda s, i: i),
                    ("reversed", lambda s, i: n_contacts[s] + 1 - i)]
        for k in (-2, -1, 1, 2):
            mappings.append((f"offset {k:+d}", (lambda k: lambda s, i: i + k)(k)))
        for name, fn in mappings:
            alt, _ = score(fn)
            _, w = agreement(alt)
            print(f"  {name:<12} {w:3d}/{len(alt):3d} = {w / len(alt):4.0%}")

    outliers = [r for r in rows if r["verdict"] in ("miss", "<=5mm")]
    if outliers:
        print("\ndisagreements (>2 mm from the clinician's structure):")
        for r in sorted(outliers, key=lambda r: -(r["accepted_d_mm"] or 1e9)):
            d = r["accepted_d_mm"]
            print(f"  {r['contact']:<6} report {r['claim_regions']:<28} "
                  f"got {r['label']}"
                  f"{' / ' + r['nearest_grey'] if r['nearest_grey'] else ''}"
                  f"  d={d if d is not None else f'>{a.max_search_mm:g}'} mm"
                  f"   [{r['claim_source']}]")

    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
