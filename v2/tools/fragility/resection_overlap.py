#!/usr/bin/env python3
"""Which contacts fell inside the resection cavity.

Rigidly registers the post-op T1 to the pre-op T1 the contacts were localized
on, then reports, per contact, how the tissue there looks after surgery. A
contact whose post-op neighbourhood is CSF-dark where pre-op it was brain was
resected.

    python resection_overlap.py --postop data/fragility/postop/5_sag_t1_mprage_iso.nii.gz \
        --contacts data/fragility/contact_anatomy.csv --subject Bella \
        --subjects-dir data/subjects -o data/fragility/resection

Writes the resampled post-op volume next to the results so the call can be
checked by eye -- intensity thresholds decide nothing on their own here.
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

import nibabel as nib
import numpy as np
import SimpleITK as sitk


def register(fixed_path, moving_path, out_path):
    fixed = sitk.ReadImage(fixed_path, sitk.sitkFloat32)
    moving = sitk.ReadImage(moving_path, sitk.sitkFloat32)

    init = sitk.CenteredTransformInitializer(
        fixed, moving, sitk.Euler3DTransform(),
        sitk.CenteredTransformInitializerFilter.GEOMETRY)

    r = sitk.ImageRegistrationMethod()
    r.SetMetricAsMattesMutualInformation(numberOfHistogramBins=50)
    r.SetMetricSamplingStrategy(r.RANDOM)
    r.SetMetricSamplingPercentage(0.10, seed=1234)
    r.SetInterpolator(sitk.sitkLinear)
    r.SetOptimizerAsRegularStepGradientDescent(
        learningRate=2.0, minStep=1e-4, numberOfIterations=300,
        gradientMagnitudeTolerance=1e-8)
    r.SetOptimizerScalesFromPhysicalShift()
    r.SetShrinkFactorsPerLevel([4, 2, 1])
    r.SetSmoothingSigmasPerLevel([2, 1, 0])
    r.SmoothingSigmasAreSpecifiedInPhysicalUnitsOn()
    r.SetInitialTransform(init, inPlace=False)

    tf = r.Execute(fixed, moving)
    print(f"  registration: {r.GetOptimizerStopConditionDescription()}")
    print(f"  final metric (Mattes MI, lower is better): {r.GetMetricValue():.4f}")
    print(f"  translation {np.round(tf.GetParameters()[3:6], 1)} mm, "
          f"rotation {np.round(np.degrees(tf.GetParameters()[0:3]), 1)} deg")

    resampled = sitk.Resample(moving, fixed, tf, sitk.sitkLinear, 0.0, sitk.sitkFloat32)
    sitk.WriteImage(resampled, out_path)
    return out_path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--postop", required=True)
    p.add_argument("--contacts", required=True, help="contact_anatomy.csv (tkreg RAS)")
    p.add_argument("--subject", default="Bella")
    p.add_argument("--subjects-dir", default="data/subjects")
    p.add_argument("--radius-mm", type=float, default=2.5)
    p.add_argument("-o", "--out", default="data/fragility/resection")
    a = p.parse_args()

    os.environ["SUBJECTS_DIR"] = os.path.abspath(a.subjects_dir)
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "server")))
    from app.services.electrodes import vox2ras_tkr

    os.makedirs(a.out, exist_ok=True)
    mri = os.path.join(a.subjects_dir, a.subject, "mri")

    # SimpleITK does not read .mgz; hand it NIfTI with the identical geometry.
    orig = nib.load(os.path.join(mri, "orig.mgz"))
    fixed_nii = os.path.join(a.out, "preop_orig.nii.gz")
    nib.save(nib.Nifti1Image(np.asanyarray(orig.dataobj).astype(np.float32), orig.affine), fixed_nii)

    print(f"registering {os.path.basename(a.postop)} -> pre-op orig")
    reg_path = register(fixed_nii, a.postop, os.path.join(a.out, "postop_in_preop.nii.gz"))

    post = np.asanyarray(nib.load(reg_path).dataobj).astype(np.float32)
    brainmask = np.asanyarray(nib.load(os.path.join(mri, "brainmask.mgz")).dataobj)
    aseg = np.asanyarray(nib.load(os.path.join(mri, "aparc+aseg.mgz")).dataobj)
    if aseg.ndim > 3:
        aseg = aseg[..., 0]

    # Reference intensities from the post-op scan itself, sampled where the
    # pre-op says white matter and where it says ventricle -- so the CSF/tissue
    # split is calibrated per scan rather than by an absolute threshold.
    wm = post[np.isin(aseg, [2, 41]) & (post > 0)]
    csf = post[np.isin(aseg, [4, 43, 14, 15]) & (post > 0)]
    wm_ref = float(np.median(wm))
    csf_ref = float(np.median(csf)) if csf.size else 0.0
    cut = csf_ref + 0.35 * (wm_ref - csf_ref)
    print(f"  post-op intensity: WM {wm_ref:.1f}, CSF {csf_ref:.1f} -> cavity cut {cut:.1f}\n")

    inv_tkr = np.linalg.inv(vox2ras_tkr(orig))
    zooms = np.array(orig.header.get_zooms()[:3], dtype=float)
    rr = int(np.ceil(a.radius_mm / zooms.min())) + 1
    off = np.stack(np.meshgrid(*[np.arange(-rr, rr + 1)] * 3, indexing="ij"), -1).reshape(-1, 3)
    shape = np.array(post.shape[:3])

    rows = []
    with open(a.contacts) as fh:
        for c in csv.DictReader(fh):
            xyz = np.array([float(c["x"]), float(c["y"]), float(c["z"])])
            vox_f = (inv_tkr @ np.append(xyz, 1.0))[:3]
            base = np.rint(vox_f).astype(int)
            nb = base + off
            d = np.linalg.norm((nb - vox_f) * zooms, axis=1)
            keep = (d <= a.radius_mm) & np.all((nb >= 0) & (nb < shape), axis=1)
            nb = nb[keep]
            if not len(nb):
                continue
            vals = post[nb[:, 0], nb[:, 1], nb[:, 2]]
            inside_pre = brainmask[nb[:, 0], nb[:, 1], nb[:, 2]] > 0
            frac_cavity = float(np.mean(vals < cut))
            rows.append({
                "contact": c["contact"], "shaft": c["shaft"], "index": int(c["index"]),
                "y": round(float(c["y"]), 1), "preop_label": c["nearest_grey"] or c["label"],
                "postop_mean": round(float(vals.mean()), 1),
                "postop_rel_wm": round(float(vals.mean()) / wm_ref, 3),
                "frac_cavity": round(frac_cavity, 3),
                "preop_in_brain": round(float(inside_pre.mean()), 3),
                "resected": bool(frac_cavity >= 0.5 and inside_pre.mean() >= 0.5),
            })

    out_csv = os.path.join(a.out, "resection_overlap.csv")
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    by_shaft = defaultdict(list)
    for r in rows:
        by_shaft[r["shaft"]].append(r)
    print(f"{'shaft':>6} {'n':>3} {'resected':>9} {'mean frac':>10} {'y':>7}   contacts in cavity")
    print("-" * 88)
    for sh in sorted(by_shaft, key=lambda s: -np.mean([r["frac_cavity"] for r in by_shaft[s]])):
        rs = by_shaft[sh]
        nres = sum(r["resected"] for r in rs)
        names = ", ".join(r["contact"] for r in rs if r["resected"]) or "-"
        print(f"{sh:>6} {len(rs):3d} {nres:5d}/{len(rs):<3d} "
              f"{np.mean([r['frac_cavity'] for r in rs]):10.2f} "
              f"{np.mean([r['y'] for r in rs]):7.1f}   {names}")

    print(f"\nwrote {out_csv}")
    print(f"check by eye: {reg_path} over {os.path.join(mri, 'orig.mgz')}")


if __name__ == "__main__":
    main()
