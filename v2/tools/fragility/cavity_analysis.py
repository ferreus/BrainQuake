#!/usr/bin/env python3
"""Segment the resection cavity and measure every contact's distance to it.

The per-contact intensity test in resection_overlap.py flags any contact sitting
near sulcal CSF, which is why a precuneus shaft can score as "resected". Working
from the cavity as a single connected object instead removes that: a contact is
either inside it, or a measurable distance away from it.

    python cavity_analysis.py --reg data/fragility/resection/postop_in_preop.nii.gz \
        --contacts data/fragility/contact_anatomy.csv --subject Bella \
        --subjects-dir data/subjects --highlight D,A,I -o data/fragility/resection
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

import nibabel as nib
import numpy as np
from scipy import ndimage


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--reg", required=True, help="post-op resampled into pre-op grid")
    p.add_argument("--contacts", required=True)
    p.add_argument("--subject", default="Bella")
    p.add_argument("--subjects-dir", default="data/subjects")
    p.add_argument("--highlight", default="D,A,I")
    p.add_argument("--min-cc-ml", type=float, default=1.0, help="ignore cavities below this volume")
    p.add_argument("-o", "--out", default="data/fragility/resection")
    a = p.parse_args()

    os.environ["SUBJECTS_DIR"] = os.path.abspath(a.subjects_dir)
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "server")))
    from app.services.electrodes import vox2ras_tkr

    mri = os.path.join(a.subjects_dir, a.subject, "mri")
    orig = nib.load(os.path.join(mri, "orig.mgz"))
    post = np.asanyarray(nib.load(a.reg).dataobj).astype(np.float32)
    brain = np.asanyarray(nib.load(os.path.join(mri, "brainmask.mgz")).dataobj) > 0
    aseg = np.asanyarray(nib.load(os.path.join(mri, "aparc+aseg.mgz")).dataobj)
    if aseg.ndim > 3:
        aseg = aseg[..., 0]

    wm_ref = float(np.median(post[np.isin(aseg, [2, 41]) & (post > 0)]))
    csf_sel = post[np.isin(aseg, [4, 43, 14, 15]) & (post > 0)]
    csf_ref = float(np.median(csf_sel)) if csf_sel.size else 0.0
    cut = csf_ref + 0.35 * (wm_ref - csf_ref)

    # Cavity = parenchyma before, CSF-dark after. Keying on the pre-op *tissue*
    # labels rather than the brainmask is what stops the component leaking into
    # the ventricles and subarachnoid space, which are CSF-dark in both scans.
    NON_TISSUE = [0, 4, 5, 43, 44, 14, 15, 24, 30, 62, 31, 63, 72]
    tissue = brain & (aseg > 0) & ~np.isin(aseg, NON_TISSUE)
    raw = tissue & (post < cut) & (post > 0)
    raw = ndimage.binary_opening(raw, ndimage.generate_binary_structure(3, 1), iterations=2)
    lab, n = ndimage.label(raw)
    vox_ml = float(np.prod(orig.header.get_zooms()[:3])) / 1000.0
    sizes = ndimage.sum(raw, lab, range(1, n + 1)) * vox_ml
    keep = [i + 1 for i, s in enumerate(sizes) if s >= a.min_cc_ml]
    if not keep:
        raise SystemExit("no cavity above the size threshold -- check the registration")
    main_lab = keep[int(np.argmax([sizes[i - 1] for i in keep]))]
    cavity = lab == main_lab

    tkr_pre = vox2ras_tkr(orig)
    print("components above threshold (volume, centroid RAS):")
    for i in sorted(keep, key=lambda i: -sizes[i - 1])[:6]:
        ctr = (tkr_pre @ np.append(np.argwhere(lab == i).mean(0), 1.0))[:3]
        star = " <-- taken as the cavity" if i == main_lab else ""
        print(f"  {sizes[i-1]:6.1f} mL  x {ctr[0]:6.1f}  y {ctr[1]:6.1f}  z {ctr[2]:6.1f}{star}")
    print()

    tkr = vox2ras_tkr(orig)
    inv_tkr = np.linalg.inv(tkr)
    idx = np.argwhere(cavity)
    ras = (tkr @ np.column_stack([idx, np.ones(len(idx))]).T).T[:, :3]

    print(f"post-op WM {wm_ref:.0f}, CSF {csf_ref:.0f}, cut {cut:.0f}")
    print(f"{n} candidate components, {len(keep)} above {a.min_cc_ml} mL")
    print(f"cavity volume {sizes[main_lab - 1]:.1f} mL")
    print(f"  centroid  RAS  x {ras[:,0].mean():6.1f}  y {ras[:,1].mean():6.1f}  z {ras[:,2].mean():6.1f}")
    print(f"  extent    x [{ras[:,0].min():.0f}, {ras[:,0].max():.0f}]  "
          f"y [{ras[:,1].min():.0f}, {ras[:,1].max():.0f}]  "
          f"z [{ras[:,2].min():.0f}, {ras[:,2].max():.0f}]")
    print(f"  posterior margin: y = {ras[:,1].min():.0f} mm\n")

    nib.save(nib.Nifti1Image(cavity.astype(np.uint8), orig.affine),
             os.path.join(a.out, "cavity_mask.nii.gz"))

    # Distance from every voxel to the cavity, in mm.
    dist = ndimage.distance_transform_edt(~cavity, sampling=orig.header.get_zooms()[:3])

    rows, by_shaft = [], defaultdict(list)
    with open(a.contacts) as fh:
        for c in csv.DictReader(fh):
            xyz = np.array([float(c["x"]), float(c["y"]), float(c["z"])])
            v = np.rint((inv_tkr @ np.append(xyz, 1.0))[:3]).astype(int)
            if not np.all((v >= 0) & (v < np.array(cavity.shape))):
                continue
            d = float(dist[tuple(v)])
            r = {"contact": c["contact"], "shaft": c["shaft"], "y": round(float(c["y"]), 1),
                 "preop_label": c["nearest_grey"] or c["label"],
                 "dist_to_cavity_mm": round(d, 1), "in_cavity": bool(cavity[tuple(v)])}
            rows.append(r)
            by_shaft[c["shaft"]].append(r)

    with open(os.path.join(a.out, "cavity_distance.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print(f"{'shaft':>6} {'n':>3} {'in cavity':>10} {'min dist':>9} {'median':>8}   verdict")
    print("-" * 78)
    for sh in sorted(by_shaft, key=lambda s: np.median([r["dist_to_cavity_mm"] for r in by_shaft[s]])):
        rs = by_shaft[sh]
        nin = sum(r["in_cavity"] for r in rs)
        dmin = min(r["dist_to_cavity_mm"] for r in rs)
        dmed = float(np.median([r["dist_to_cavity_mm"] for r in rs]))
        verdict = ("RESECTED" if nin >= len(rs) / 2 else
                   "partially resected" if nin else
                   "spared (adjacent)" if dmin < 5 else "spared")
        print(f"{sh:>6} {len(rs):3d} {nin:6d}/{len(rs):<3d} {dmin:9.1f} {dmed:8.1f}   {verdict}")

    hi = [s for s in a.highlight.split(",") if s]
    if hi:
        print("\nper-contact detail for", ", ".join(hi))
        for sh in hi:
            for r in sorted(by_shaft.get(sh, []), key=lambda r: r["contact"]):
                mark = "IN CAVITY" if r["in_cavity"] else f"{r['dist_to_cavity_mm']:.1f} mm away"
                print(f"  {r['contact']:>5}  y={r['y']:6.1f}  {r['preop_label']:<28} {mark}")

    print(f"\nwrote {os.path.join(a.out, 'cavity_distance.csv')} and cavity_mask.nii.gz")


if __name__ == "__main__":
    main()
