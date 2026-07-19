"""Anatomical-label lookup for electrode contacts -- copied near-verbatim from
BrainQuake/utils/elec_utils.py's lookupTable(). Purely local: reads
mri/aparc.a2009s+aseg.mgz + a contact-position .txt file, both already present in
the unzipped recon dir (local_store.ensure_recon_unzipped), plus FreeSurferColorLUT.txt
shipped alongside this file. Nothing here talks to the server -- there's no
job/artifact type for "resolve anatomical label", it's a client-side-only lookup,
same as it always was.
"""
import os
import re
import math
import nibabel as nib
import numpy as np

LUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'FreeSurferColorLUT.txt')


def lookupTable(recon_dir, elecs_xyz):
    """recon_dir: unzipped FreeSurfer subject dir (contains mri/aparc.a2009s+aseg.mgz).
    elecs_xyz: Nx3 array of contact positions in the same voxel convention as
    ElectrodeSeg.resulting()'s saved .txt files (see elec_utils.lookupTable)."""
    annot_dir = os.path.join(recon_dir, 'mri', 'aparc.a2009s+aseg.mgz')
    annot_img = nib.load(annot_dir).get_fdata()

    elecs_xyz = np.atleast_2d(elecs_xyz)[:, [0, 2, 1]].copy()
    elecs_xyz[:, 0] = 128 - elecs_xyz[:, 0]
    elecs_xyz[:, 1] = 128 - elecs_xyz[:, 1]
    elecs_xyz[:, 2] = 128 + elecs_xyz[:, 2]

    labels = []
    for row in range(elecs_xyz.shape[0]):
        x, y, z = elecs_xyz[row, 0], elecs_xyz[row, 1], elecs_xyz[row, 2]
        x1, x2 = int(x), math.ceil(x)
        y1, y2 = int(y), math.ceil(y)
        z1, z2 = int(z), math.ceil(z)
        val = [
            annot_img[x1, y1, z1], annot_img[x1, y1, z2], annot_img[x1, y2, z1], annot_img[x1, y2, z2],
            annot_img[x2, y1, z1], annot_img[x2, y1, z2], annot_img[x2, y2, z1], annot_img[x2, y2, z2],
        ]
        labels.append(max(set(val), key=val.count))

    labels_name = []
    for label in labels:
        with open(LUT_PATH, 'r') as f:
            for line in f.readlines():
                header = line[0:8]
                b = str(int(label))
                if re.match(b, header):
                    labels_name.append(line[len(b):-16].strip())
                    break
    return labels_name
