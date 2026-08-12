#!/usr/bin/env python3
"""QC figure: pre-op vs registered post-op, with the cavity and contacts drawn on.

Every number in cavity_analysis.py rests on the registration being right, and a
registration is checked by looking at it.

Volumes are reoriented to canonical RAS first, so axis 0 is left->right, axis 1
posterior->anterior and axis 2 inferior->superior regardless of how the source
volume was stored -- otherwise a FreeSurfer LIA volume gets sliced along the
wrong plane and the "sagittal" panel is nothing of the sort.

    python cavity_qc.py --subject Bella --subjects-dir data/subjects \
        --reg data/fragility/resection/postop_in_preop.nii.gz \
        --cavity data/fragility/resection/cavity_mask.nii.gz \
        --contacts data/fragility/contact_anatomy.csv --shafts D,A,I \
        -o data/fragility/resection/qc.png
"""

import argparse
import csv
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import nibabel as nib
import numpy as np

COLORS = {"D": "#ff3b30", "A": "#00d0ff", "I": "#ffd400"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--subject", default="Bella")
    p.add_argument("--subjects-dir", default="data/subjects")
    p.add_argument("--reg", required=True)
    p.add_argument("--cavity", required=True)
    p.add_argument("--contacts", required=True)
    p.add_argument("--shafts", default="D,A,I")
    p.add_argument("--focus", default="D", help="shaft the third row centres on")
    p.add_argument("--slice-tol", type=float, default=5.0)
    p.add_argument("-o", "--out", required=True)
    a = p.parse_args()

    os.environ["SUBJECTS_DIR"] = os.path.abspath(a.subjects_dir)
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "server")))
    from app.services.electrodes import vox2ras_tkr

    mri = os.path.join(a.subjects_dir, a.subject, "mri")
    orig = nib.load(os.path.join(mri, "orig.mgz"))
    post_img = nib.Nifti1Image(np.asanyarray(nib.load(a.reg).dataobj).astype(np.float32), orig.affine)
    cav_img = nib.Nifti1Image(np.asanyarray(nib.load(a.cavity).dataobj).astype(np.uint8), orig.affine)
    pre_img = nib.Nifti1Image(np.asanyarray(orig.dataobj).astype(np.float32), orig.affine)

    can_pre = nib.as_closest_canonical(pre_img)
    pre = can_pre.get_fdata()
    post = nib.as_closest_canonical(post_img).get_fdata()
    cav = nib.as_closest_canonical(cav_img).get_fdata() > 0
    inv_can = np.linalg.inv(can_pre.affine)

    # tkreg RAS -> scanner RAS -> canonical voxel
    tkr_to_scanner = orig.affine @ np.linalg.inv(vox2ras_tkr(orig))
    shafts = [s for s in a.shafts.split(",") if s]
    pts = []
    with open(a.contacts) as fh:
        for c in csv.DictReader(fh):
            if c["shaft"] not in shafts:
                continue
            tkr = np.array([float(c["x"]), float(c["y"]), float(c["z"]), 1.0])
            vox = (inv_can @ (tkr_to_scanner @ tkr))[:3]
            pts.append((c["contact"], c["shaft"], vox))

    def centre(mask_or_pts):
        if isinstance(mask_or_pts, np.ndarray):
            return np.argwhere(mask_or_pts).mean(0).round().astype(int)
        return np.mean([v for _, _, v in mask_or_pts], axis=0).round().astype(int)

    cav_c = centre(cav)
    foc_pts = [t for t in pts if t[1] == a.focus]
    foc_c = centre(foc_pts) if foc_pts else cav_c

    def draw(ax, vol, axis, sl, title, overlay=None):
        sl = int(np.clip(sl, 0, vol.shape[axis] - 1))
        img = [vol[sl, :, :], vol[:, sl, :], vol[:, :, sl]][axis].T
        ax.imshow(img, cmap="gray", origin="lower", vmin=0, vmax=np.percentile(vol, 99.5))
        if overlay is not None:
            ov = [overlay[sl, :, :], overlay[:, sl, :], overlay[:, :, sl]][axis].T
            m = np.ma.masked_where(~ov, np.ones_like(ov, dtype=float))
            ax.imshow(m, cmap=matplotlib.colors.ListedColormap(["#ff2d55"]), alpha=0.42, origin="lower")
        ip = [(1, 2), (0, 2), (0, 1)][axis]
        for name, sh, v in pts:
            if abs(v[axis] - sl) > a.slice_tol:
                continue
            ax.plot(v[ip[0]], v[ip[1]], "o", ms=5, mfc="none", mew=1.5, color=COLORS.get(sh, "#7cff5a"))
            ax.annotate(name, (v[ip[0]], v[ip[1]]), textcoords="offset points", xytext=(5, 3),
                        color=COLORS.get(sh, "#7cff5a"), fontsize=7)
        ax.set_title(title, fontsize=9, color="white")
        ax.axis("off")

    names = ["sagittal", "coronal", "axial"]
    fig, axes = plt.subplots(3, 3, figsize=(13.5, 13), facecolor="black")
    for col in range(3):
        draw(axes[0][col], pre, col, cav_c[col], f"pre-op {names[col]}")
        draw(axes[1][col], post, col, cav_c[col], f"post-op + cavity, {names[col]}", overlay=cav)
        draw(axes[2][col], post, col, foc_c[col],
             f"post-op + cavity at shaft {a.focus}, {names[col]}", overlay=cav)

    handles = [plt.Line2D([], [], marker="o", ls="", mfc="none", mew=1.6,
                          color=COLORS.get(s, "#7cff5a"), label=f"shaft {s}") for s in shafts]
    handles.append(plt.Line2D([], [], marker="s", ls="", color="#ff2d55", label="resection cavity"))
    fig.legend(handles=handles, loc="lower center", ncol=len(handles), facecolor="black",
               labelcolor="white", fontsize=9, framealpha=0)
    fig.suptitle(f"{a.subject}: resection cavity vs contacts  "
                 f"(rows 1-2 centred on cavity, row 3 on shaft {a.focus})",
                 color="white", fontsize=12)
    fig.tight_layout(rect=[0, 0.035, 1, 0.965])
    fig.savefig(a.out, dpi=130, facecolor="black")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
