"""Anatomical labeling of SEEG contacts against a FreeSurfer segmentation.

Answers "which structure is contact K'7 in?" for every contact at once, by
sampling `mri/aparc+aseg.mgz` (or whichever segmentation the subject's recon
produced) at each contact coordinate and resolving the integer label through
FreeSurfer's colour lookup table.

Two things make the naive "read the voxel under the contact" answer misleading
on real SEEG, and both are handled here:

  1. Contacts land in white matter constantly -- a contact 1.5 mm outside
     hippocampus reads as `Left-Cerebral-White-Matter`, which is true and
     useless. `nearest_structure` therefore reports the closest *grey* label
     within `radius_mm`, with its distance, alongside the exact-voxel answer.
  2. A single voxel is a 1 mm sample of a ~2 mm contact sitting on a boundary.
     `neighborhood` reports every label within the same radius by volume
     fraction, so a contact straddling amygdala/hippocampus shows as exactly
     that instead of silently picking one.

Nothing here writes to disk or the DB: contacts can be re-labeled at any time,
against any radius, and the answer is always derived from what is currently on
disk -- so it can never go stale the way a cached table would after a re-import.
"""

import os

import nibabel as nib
import numpy as np

from app.config import settings
from app.models import Subject
from app.services.electrodes import load_chn_xyz, vox2ras_tkr

# Searched in order; the first that exists wins. recon-all writes
# aparc+aseg.mgz; FastSurfer's network writes the DKTatlas variants (see
# recon.py's annotation_name switch for the surface-side equivalent). aseg.mgz
# is the last resort -- it has no cortical parcellation at all, so every
# cortical contact comes back as the undifferentiated `?h-Cerebral-Cortex`.
# Which one was used is always reported back to the caller, since it changes
# how much the answer is worth.
SEGMENTATION_CANDIDATES = (
    "mri/aparc+aseg.mgz",
    "mri/aparc.DKTatlas+aseg.mgz",
    "mri/aparc.DKTatlas+aseg.deep.mgz",
    "mri/aseg.mgz",
)

# Desikan-Killiany cortical parcels, in FreeSurferColorLUT.txt's own order:
# ctx-lh-* = 1000+i, ctx-rh-* = 2000+i, wm-lh-* = 3000+i, wm-rh-* = 4000+i.
_DK_PARCELS = (
    "unknown", "bankssts", "caudalanteriorcingulate", "caudalmiddlefrontal",
    "corpuscallosum", "cuneus", "entorhinal", "fusiform", "inferiorparietal",
    "inferiortemporal", "isthmuscingulate", "lateraloccipital",
    "lateralorbitofrontal", "lingual", "medialorbitofrontal", "middletemporal",
    "parahippocampal", "paracentral", "parsopercularis", "parsorbitalis",
    "parstriangularis", "pericalcarine", "postcentral", "posteriorcingulate",
    "precentral", "precuneus", "rostralanteriorcingulate", "rostralmiddlefrontal",
    "superiorfrontal", "superiorparietal", "superiortemporal", "supramarginal",
    "frontalpole", "temporalpole", "transversetemporal", "insula",
)

_ASEG_NAMES = {
    0: "Unknown",
    2: "Left-Cerebral-White-Matter", 3: "Left-Cerebral-Cortex",
    4: "Left-Lateral-Ventricle", 5: "Left-Inf-Lat-Vent",
    7: "Left-Cerebellum-White-Matter", 8: "Left-Cerebellum-Cortex",
    10: "Left-Thalamus", 11: "Left-Caudate", 12: "Left-Putamen",
    13: "Left-Pallidum", 14: "3rd-Ventricle", 15: "4th-Ventricle",
    16: "Brain-Stem", 17: "Left-Hippocampus", 18: "Left-Amygdala",
    24: "CSF", 26: "Left-Accumbens-area", 28: "Left-VentralDC",
    30: "Left-vessel", 31: "Left-choroid-plexus",
    41: "Right-Cerebral-White-Matter", 42: "Right-Cerebral-Cortex",
    43: "Right-Lateral-Ventricle", 44: "Right-Inf-Lat-Vent",
    46: "Right-Cerebellum-White-Matter", 47: "Right-Cerebellum-Cortex",
    49: "Right-Thalamus", 50: "Right-Caudate", 51: "Right-Putamen",
    52: "Right-Pallidum", 53: "Right-Hippocampus", 54: "Right-Amygdala",
    58: "Right-Accumbens-area", 60: "Right-VentralDC",
    62: "Right-vessel", 63: "Right-choroid-plexus",
    72: "5th-Ventricle", 77: "WM-hypointensities", 80: "non-WM-hypointensities",
    85: "Optic-Chiasm",
    251: "CC_Posterior", 252: "CC_Mid_Posterior", 253: "CC_Central",
    254: "CC_Mid_Anterior", 255: "CC_Anterior",
}


def _builtin_lut():
    """Enough of FreeSurferColorLUT.txt to name anything aparc+aseg.mgz can
    contain, so contact labeling works on a machine that has subject data but
    no FreeSurfer install (dev boxes, tests, an imported patient). The real
    LUT still wins wherever it exists -- see load_label_lut."""
    lut = dict(_ASEG_NAMES)
    for i, parcel in enumerate(_DK_PARCELS):
        lut[1000 + i] = f"ctx-lh-{parcel}"
        lut[2000 + i] = f"ctx-rh-{parcel}"
        lut[3000 + i] = f"wm-lh-{parcel}"
        lut[4000 + i] = f"wm-rh-{parcel}"
    return lut


def _parse_lut_file(path):
    """FreeSurferColorLUT.txt: `<id> <name> <R> <G> <B> <A>`, # comments."""
    lut = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            fields = line.split()
            if len(fields) < 2:
                continue
            try:
                lut[int(fields[0])] = fields[1]
            except ValueError:
                continue  # header junk, not a label row
    return lut


def load_label_lut():
    """Built-in table, overlaid with the installed FreeSurfer LUT when one is
    present -- that copy is authoritative (it matches the exact FreeSurfer
    version that produced the segmentation) and covers atlases the built-in
    table deliberately doesn't, e.g. Destrieux's 11100+ range."""
    lut = _builtin_lut()
    fs_lut_path = os.path.join(settings.FREESURFER_HOME, "FreeSurferColorLUT.txt")
    if os.path.exists(fs_lut_path):
        lut.update(_parse_lut_file(fs_lut_path))
    return lut


def label_name(lut, label_id):
    return lut.get(int(label_id), f"Label-{int(label_id)}")


# Labels that answer "which structure is this contact in?" with "none of them".
# Kept as an explicit id set *and* a name-pattern check: the ids cover the
# standard aseg/aparc ranges exactly, the patterns catch the wm-?h-* parcels
# (3000-4035) and anything an unusual atlas adds via the installed LUT.
_NON_STRUCTURE_IDS = frozenset({
    0,                      # background / unknown
    2, 41,                  # cerebral white matter
    7, 46,                  # cerebellar white matter
    4, 5, 43, 44, 14, 15, 72,  # ventricles
    24,                     # CSF
    30, 62,                 # vessel
    31, 63,                 # choroid plexus
    77, 80,                 # hypointensities
    251, 252, 253, 254, 255,   # corpus callosum
    1000, 2000,             # ctx-?h-unknown
    1004, 2004,             # ctx-?h-corpuscallosum
})

_NON_STRUCTURE_NAME_PARTS = (
    "white-matter", "ventricle", "csf", "vessel", "choroid-plexus",
    "hypointensities", "unknown", "corpuscallosum",
)


def is_structure(label_id, name):
    """True if this label is a grey-matter structure worth reporting as
    "the contact is in X" -- i.e. not white matter, CSF, ventricle or
    background."""
    if int(label_id) in _NON_STRUCTURE_IDS:
        return False
    lowered = name.lower()
    if lowered.startswith(("wm-", "wm_")):
        return False
    return not any(part in lowered for part in _NON_STRUCTURE_NAME_PARTS)


def find_segmentation(subject: Subject):
    """(abs_path, rel_path) of the best available segmentation for a subject.
    Raises FileNotFoundError if the recon produced none of them."""
    subject_dir = os.path.join(settings.SUBJECTS_DIR, subject.name)
    for rel_path in SEGMENTATION_CANDIDATES:
        abs_path = os.path.join(subject_dir, rel_path)
        if os.path.exists(abs_path):
            return abs_path, rel_path
    raise FileNotFoundError(
        f"No FreeSurfer segmentation found for subject {subject.name!r} "
        f"(looked for {', '.join(SEGMENTATION_CANDIDATES)} under {subject_dir}). "
        f"Run brain reconstruction first."
    )


def _neighbor_offsets(zooms, radius_mm):
    """Integer voxel offsets whose centres can fall within radius_mm. One extra
    voxel per axis because the contact sits at a fractional voxel position, so
    the sphere around it is not centred on the base voxel."""
    ranges = [np.arange(-(int(np.ceil(radius_mm / z)) + 1), int(np.ceil(radius_mm / z)) + 2) for z in zooms]
    grid = np.meshgrid(*ranges, indexing="ij")
    return np.stack([g.ravel() for g in grid], axis=1)


def label_points(points, seg_path, radius_mm: float = 3.0, max_neighborhood: int = 5):
    """Anatomical label for each `(electrode, contact_index, xyz)` in tkreg RAS.

    Coordinates are converted with the segmentation's *own* vox2ras_tkr, so this
    stays correct for a non-256^3 volume instead of assuming the conformed
    geometry. Takes points rather than a Subject so the offline tools can label
    coordinates straight out of parse_mrb, without a chnXyzDict.npy on disk.
    """
    if radius_mm <= 0:
        raise ValueError(f"radius_mm must be positive, got {radius_mm}")

    img = nib.load(seg_path)
    # asanyarray, not get_fdata(): keeps the segmentation in its native integer
    # dtype. get_fdata() would inflate a 256^3 volume to 134 MB of float64 per
    # request and then need rounding back to label ids anyway.
    data = np.asanyarray(img.dataobj)
    if data.ndim > 3:
        data = data[..., 0]  # some .mgz writers keep a trailing singleton frame axis
    zooms = np.array(img.header.get_zooms()[:3], dtype=float)
    inv_tkr = np.linalg.inv(vox2ras_tkr(img))
    lut = load_label_lut()

    offsets = _neighbor_offsets(zooms, radius_mm)
    shape = np.array(data.shape[:3])

    results = []
    for electrode, contact_index, xyz in points:
        xyz = np.asarray(xyz, dtype=float)[:3]
        vox_f = (inv_tkr @ np.append(xyz, 1.0))[:3]
        base = np.rint(vox_f).astype(int)

        entry = {
            "electrode": electrode,
            "contact_index": contact_index,
            "name": f"{electrode}{contact_index}",
            "x": float(xyz[0]), "y": float(xyz[1]), "z": float(xyz[2]),
            "voxel": base.tolist(),
            "label_id": None,
            "label_name": None,
            "nearest_structure": None,
            "neighborhood": [],
        }

        if np.all((base >= 0) & (base < shape)):
            exact_id = int(data[base[0], base[1], base[2]])
            entry["label_id"] = exact_id
            entry["label_name"] = label_name(lut, exact_id)
        else:
            # Outside the segmentation FOV entirely -- a contact this far
            # out is a coordinate-space error, not an anatomical finding,
            # so say so rather than reporting the nearest edge voxel.
            entry["out_of_volume"] = True
            results.append(entry)
            continue

        neighbors = base + offsets
        dist = np.linalg.norm((neighbors - vox_f) * zooms, axis=1)
        keep = dist <= radius_mm
        neighbors, dist = neighbors[keep], dist[keep]
        in_bounds = np.all((neighbors >= 0) & (neighbors < shape), axis=1)
        neighbors, dist = neighbors[in_bounds], dist[in_bounds]
        if len(neighbors) == 0:
            results.append(entry)
            continue

        near_ids = data[neighbors[:, 0], neighbors[:, 1], neighbors[:, 2]].astype(int)

        unique_ids, counts = np.unique(near_ids, return_counts=True)
        order = np.argsort(-counts)
        total = float(counts.sum())
        entry["neighborhood"] = [
            {
                "label_id": int(unique_ids[i]),
                "label_name": label_name(lut, unique_ids[i]),
                "fraction": round(float(counts[i]) / total, 4),
            }
            for i in order[:max_neighborhood]
        ]

        structure_mask = np.array(
            [is_structure(lid, label_name(lut, lid)) for lid in unique_ids], dtype=bool
        )
        structure_ids = set(unique_ids[structure_mask].tolist())
        if structure_ids:
            candidates = np.array([lid in structure_ids for lid in near_ids], dtype=bool)
            nearest = int(np.argmin(np.where(candidates, dist, np.inf)))
            nearest_id = int(near_ids[nearest])
            entry["nearest_structure"] = {
                "label_id": nearest_id,
                "label_name": label_name(lut, nearest_id),
                "distance_mm": round(float(dist[nearest]), 2),
            }

        results.append(entry)

    return results


def label_contacts(subject: Subject, radius_mm: float = 3.0, max_neighborhood: int = 5):
    """Anatomical label for every contact of every electrode.

    Contacts come from chnXyzDict.npy in FreeSurfer surface (tkreg) RAS -- the
    space both segment() and the Slicer import write (see
    electrodes.import_contacts).

    Raises FileNotFoundError if the subject has no contacts or no segmentation.
    """
    chn_xyz = load_chn_xyz(subject)  # {electrode: [[x, y, z], ...]}, row order == contact number
    seg_path, seg_rel = find_segmentation(subject)

    points = [
        (electrode, row + 1, xyz)  # 1-based; matches the <label>.txt row order invariant
        for electrode, coords in sorted(chn_xyz.items())
        for row, xyz in enumerate(coords)
    ]
    return {
        "segmentation": seg_rel,
        "radius_mm": radius_mm,
        "contacts": label_points(points, seg_path, radius_mm, max_neighborhood),
    }
