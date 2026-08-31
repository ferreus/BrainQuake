# SEEG Contact Import from 3D Slicer (.mrb) — Plan

> **Path note (2026-09-01):** this document was written against a scene at
> `data/bella_3dslicer.mrb`. That path no longer exists; the same scene (node
> `Contacts_8`, 184 contacts, 20 shafts) is now `datasets/Bella Seeg.mrb`.
> References below are left as written, since they record what was done at the
> time. Its labelling is validated in
> [bella_anatomy_validation.md](bella_anatomy_validation.md).

## Relationship to `seeg_electrode_detection_upgrade_plan.md`

That doc proposes replacing `hough3dlines` + GMM with a better *fully-automatic*
algorithm (DBSCAN+RANSAC, or a MONAI U-Net). This doc takes a different bet,
made after comparing BrainQuake's auto-detection against 3D Slicer's
semi-automatic SEEG localization on the same data: Slicer's human-verified,
per-electrode-parameterized placement (entry point, diameter, spacing,
contact count, manual per-contact nudging) is far more accurate than any
unsupervised clustering of CT voxels can be. So instead of improving the
automatic algorithm, this plan **bridges Slicer's output into BrainQuake**
and lets Slicer's SEEG module own contact localization entirely — BrainQuake
keeps the parts it's already validated: EI/HFER, HFO/HI, SOZ fusion (all in
`v2/server/app/services/{ictal,interictal,soz}.py`), which consume contact
coordinates + channel labels but don't care how those were produced.

The DBSCAN/RANSAC ideas in the other doc aren't dead — `min_samples`/density
clustering could later serve as an *auto-suggest* starting point inside a
semi-automatic review step — but that's not the current priority.

## Why this should work despite Slicer and BrainQuake using different CT↔T1 registrations

A fiducial's stored coordinate is just a point in T1 physical (RAS) space —
it carries no memory of which registration algorithm was used to align the
CT for visualization. So there's nothing to reconcile between Slicer's
internal CT→T1 registration and BrainQuake's `flirt`-based one
(`v2/server/app/services/ct_register.py`) — for subjects localized via
Slicer, BrainQuake's own registration step can simply be skipped. This only
holds if two things are true (verify both against the actual `.mrb` — see
Step 1/2 below):

1. The fiducial coordinates are **hardened** into T1 space, not left under a
   live parent transform node.
2. The T1 volume used inside the Slicer scene is the **same T1** (same
   file/series) that was fed to `recon-all` for that subject.

## Prerequisite

The `.mrb` file, on the machine this plan is continued on. Also useful to
have on hand: the subject's FreeSurfer `SUBJECTS_DIR/<subject>/mri/orig.mgz`
(or `rawavg.mgz`) and the ictal EDF used for that subject, for Steps 2 and 5.

---

## Step 1 — Unpack and inspect the `.mrb`

A `.mrb` is a zip containing a `Data/` folder and a scene file.

```bash
mkdir -p /tmp/mrb_extract
unzip -o /path/to/file.mrb -d /tmp/mrb_extract
find /tmp/mrb_extract -iname "*.mrml"
```

Open the `.mrml` (it's XML) and identify:

- **Markup nodes** — `grep -o '<Markups[A-Za-z]*Node[^>]*' scene.mrml`.
  Newer Slicer (5.x) versions store control points in a separate
  `Data/*.mrk.json` file referenced from the `.mrml` node (look for a
  `storageNodeRef` / matching `MarkupsStorageNode` with a `fileName`
  attribute) rather than inline coordinates — check for both formats, don't
  assume inline.
- **Transform nodes** — `grep -o '<LinearTransform[^>]*\|<Transform[^>]*' scene.mrml`.
  For each markup node, check its `transformNodeID` (or equivalent) attribute:
  if non-empty and non-identity, that transform must be composed onto the
  raw control-point coordinates before they're valid T1 RAS.
- **Volume nodes** — `grep -o '<VolumeArchetypeStorage[^>]*\|<Volume[^>]*' scene.mrml`
  and check the `name=` / `fileName=` on the T1 and CT scalar volume nodes in
  `Data/`. Note which file is the T1 candidate for Step 2.

Load the markup file(s) directly rather than fighting the XML if easier:

```python
import json
with open("/tmp/mrb_extract/Data/<name>.mrk.json") as f:
    data = json.load(f)
for cp in data["markups"][0]["controlPoints"]:
    print(cp["label"], cp["position"])  # check coordinateSystem in the JSON — LPS, not RAS, in bella_3dslicer.mrb
```

### Findings from `data/bella_3dslicer.mrb` (2026-08-06)

Unpacked to `Bella Seeg/` (space in the folder name). Concrete answers to the
"Open items" below:

- **Slicer 5.x, external storage.** Both markup nodes use
  `MarkupsJsonStorage` → `Data/F.mrk.json` and `Data/Contacts_8.mrk.json`.
  No inline coordinates in the `.mrml`.
- **Two markup nodes, not one:**
  - `F` — 20 control points, labels like `G'-10`, `L'-12`, `X-12` — one per
    electrode (name + what looks like a target/depth index). Likely a
    planning/trajectory fiducial set, **not** the per-contact list.
  - `Contacts_8` — 184 control points, labels like `G'1`, `G'2`, ...,
    `X12` — electrode name + contact index. **This is the node for Step
    3/5/6.**
  - Both are `coordinateSystem="LPS"` in the JSON (not RAS — the plan text
    above was written loosely; convert `R,A,S = -L, -P, S` after resolving
    to T1 space).
- **Transform is live, not hardened, on both markup nodes.** Both
  `MarkupsFiducial` nodes carry `references="...transform:vtkMRMLTransformNode2;"`
  — `vtkMRMLTransformNode2` is named `"Transform CT to T1"`
  (`Data/Transform CT to T1.h5`). This is the *same* transform node applied
  to the CT volume itself. Raw control-point coordinates land inside the CT
  volume's own native bounding box (verified numerically), confirming the
  contacts were placed in CT space and displayed in T1 space only via this
  live transform — so Step 3's "compose the transform" is required, not
  optional.
- **Gotcha: the transform that works is the one whose filename doesn't say
  so.** The scene has two `.h5` files, `Transform CT to T1.h5` and
  `Transform T1 to CT.h5`, both `AffineTransform_double_3_3` (in practice
  rigid — `A · Aᵀ ≈ I`, `det(A) ≈ 1` — matching `useRigid="true"` on the
  `General Registration (BRAINS)` `CommandLineModule` node, which also
  confirms `fixedVolume=CT`, `movingVolume=T1`). Applying the standard ITK
  affine formula `y = A·(x − c) + t + c` with the parameters from
  **`Transform CT to T1.h5`** to a raw `Contacts_8`/`F` point gives
  coordinates hundreds of mm outside any sane head bounding box. Applying
  the same formula with the parameters from **`Transform T1 to CT.h5`**
  instead (i.e. the file whose name is the *opposite* of the node actually
  referenced by the fiducials) lands the point inside the T1 volume's own
  spatial extent. This is a known BRAINSFit/ITK convention trap (registration
  output transforms map fixed→moving for resampling, which inverts the
  "moving to fixed" naming used in the UI) — **when repeating this on
  another `.mrb`, verify empirically (extent check, not just filename)
  rather than trusting which `.h5` the markup node's `transformNodeID`
  points at.**
- **T1/CT identification.** `Data/Bella4YOT1.nrrd` (176×256×256, isotropic
  1mm, near-identity direction) and `Data/Bella4YOCT.nrrd` (512×512×276,
  0.5×0.5×0.6mm) — confirmed via `VolumeArchetypeStorage` `fileName=` and
  node names in the `.mrml`.
- `datasets/BellaT1.nii.gz` (144×260×320, 1×0.75×0.75mm) does not
  dimension-match `Bella4YOT1.nrrd` — it's the raw, un-conformed acquisition,
  a different processing stage. Not meaningful for Step 2; see the real
  answer below now that `orig.mgz` exists.

## Step 2 — Confirm the T1 is the same one `recon-all` used — ✅ validated (2026-08-06)

FreeSurfer subject landed at `data/Bella/` (`mri/orig.mgz`: 256³, 1mm
isotropic, LIA orientation, `Pxyz_c` (c_ras) = `[2.290, -18.935, -1.306]`).

Rather than compare `orig.mgz` against the `.mrb`'s T1 header directly
(different voxel grids — 256³ conformed vs. the `.mrb`'s 176×256×256 — so a
header diff alone is inconclusive), the composed Step 3 pipeline was run
end-to-end and checked against `data/Bella/mri/brainmask.mgz`:

```python
# raw Contacts_8 LPS point -> fwd("Transform T1 to CT.h5") -> T1-native LPS
# -> negate R,A for RAS -> nearest-voxel lookup in orig.mgz's grid
```

All 184 `Contacts_8` points land inside `orig.mgz`'s voxel grid, and
**89.7%** fall on nonzero `brainmask` voxels — the rest are the
outermost/entry contacts of each depth electrode sitting in skull/scalp,
which is exactly what's expected for SEEG trajectories (only the innermost
contacts are meant to be intracerebral). This confirms the "why this should
work" assumption for real: **the `.mrb`'s T1 and `recon-all`'s `orig.mgz`
share the same scanner RAS frame — no extra registration between them is
needed for this subject.**

## Step 3 — Resolve fiducials to a clean intermediate list — ✅ done (2026-08-06)

Ran the composed transform (per the Step 1 findings above: `Transform T1 to
CT.h5`, applied forward, despite the name) over all 184 `Contacts_8` points,
parsed `label` into `electrode` + `contact_index` (regex `^([A-Za-z]+'?)(\d+)$`
— handles the `'`-suffixed left-hemisphere electrode names like `G'`, `K'`).
Result: 20 electrodes, 6–12 contacts each (6/8/10/12 — matches standard
Dixi/Ad-Tech depth-electrode lengths, a good independent sanity check that
label parsing is right). Written to
`bella_contacts_resolved.csv` (`label, electrode, contact_index, R, A, S,
surfR, surfA, surfS` — scanner RAS and surface RAS both included, see Step 4)
in the scratch dir this session; not yet committed to the repo — ask before
adding a contacts CSV under `data/` or `docs/`, since it's derived/generated
data rather than something to hand-maintain.

## Step 4 — Convert to FreeSurfer surface RAS — ✅ done (2026-08-06)

Used `orig.mgz`'s header `Pxyz_c` directly (nibabel `MGHHeader.get('Pxyz_c')`
— equivalent to `mri_info --cras`) rather than shelling out:

```
surface_RAS = scanner_RAS - c_ras   # c_ras = [2.290, -18.935, -1.306] for Bella
```

Both scanner and surface RAS are in the Step 3 CSV. The brainmask check in
Step 2 already validates these land in-brain, which subsumes the originally
planned pial-scatter sanity check — skipped as redundant.

## Step 5 — Reconcile contact naming with EDF channel names — ⛔ blocked, no SEEG EDF for Bella yet

Searched the repo for a Bella ictal/interictal EDF (`BrainQuake/upload/Bella/`,
`BrainQuake/Server_codes/data/recv/Bella/`, `datasets/`) — found none
originally. `datasets/Bella.edf` was added 2026-08-06 but is a 31-channel
scalp 10-20 recording (`Fp2, F4, C4, P4, O2, F8, FT10, T8, P8, Fz, Cz, Pz,
Fp1, F3, C3, P3, O1, F7, FT9, T7, P7, EKG, F10, T10, P10, F9, T9, P9, Oz, A1,
A2`) — not the SEEG recording (channel names don't remotely match the
`Contacts_8` electrode labels `A, B, D, F, G, G', I, K, K', L, L', M, M', N,
P, Q, S, T, X, X'`). Disregarded. Still waiting on the correct SEEG EDF for
Bella from the user.

```python
import mne
raw = mne.io.read_raw_edf("/path/to/subject.edf", preload=False)
print(raw.ch_names)
```

Compare against the Slicer fiducial `label` strings. Expect a naming
mismatch (e.g. `OF1` vs `OF01`, zero-padding, electrode-prefix differences)
— build an explicit mapping table rather than assuming string equality;
`soz.py`'s fusion step needs this mapping to land EI/HFO results on the
correct 3D contact.

## Step 6 — Wire into `v2/server` — ✅ done (2026-08-06)

Confirmed the target format by reading `ElectrodeSeg.resulting()`
(`v2/server/app/services/electrodes.py`): it writes `<label>.txt` under
`<SUBJECTS_DIR>/<patient>/fslresults/<patient>_result/` with voxel
`(vx,vy,vz) -> (128-vx, vz-128, 128-vy)` for a 256³ 1mm conform volume —
which is exactly FreeSurfer's `vox2ras_tkr` (surface/tkreg RAS), i.e. the
same space Step 4 already produces (`scanner_RAS - c_ras`). No extra
conversion needed between the Step 4 CSV and this artifact format.

- `import_contacts(subject, contacts)` added to `electrodes.py`: groups by
  `electrode`, sorts by `contact_index`, validates the indices are
  contiguous `1..N` (raises with a clear message otherwise — a gap would
  silently mislabel every later contact, since `soz.py`'s
  `load_contact_xyz` builds channel names as `f"{label}{row_index+1}"`, not
  from a stored index), writes `<label>.txt` in the identical format
  `ElectrodeSeg.resulting()` uses, then reuses the existing `savenpy()` to
  build `chnXyzDict.npy`. `_patient_dirs()` already resolves to
  `fslresults/` regardless of whether `ct_register` ever ran, so
  `chn-xyz`/`contacts`/`soz_fuse` work unmodified on the result — bypasses
  `ct_register`/`detect`/`segment` entirely, satisfying the "skip flirt for
  these subjects" goal without any special-casing (the client/caller simply
  never invokes `/register-ct`, `/detect`, `/segment` for a Slicer-sourced
  subject).
- `run_elec_import_job(db, job, log_file)` added, dispatched via a new
  `elec_import` job type (registered in `workers/jobs_worker.py`).
- New endpoint: `POST /subjects/{id}/electrodes/import` in
  `routers/electrodes.py`, body `{"contacts": [{"electrode", "contact_index",
  "x", "y", "z"}, ...]}` (electrode name kept as whatever the source used —
  e.g. Slicer's `G'`/`K'` primed left-hemisphere convention — not coerced to
  the legacy A-Z-minus-I alphabet, since nothing downstream assumes that
  character set). Returns a `Job` like every other job-creating endpoint.
- Tests added in `v2/server/tests/test_api.py`:
  `test_electrodes_import_contacts` (round-trips through
  `chn-xyz`/`contacts/{label}`) and
  `test_electrodes_import_rejects_non_contiguous_indices`. Full suite (19
  tests) passes.
- **GUI wiring, v1 (superseded same day)**: an "Import from 3D Slicer" button
  that opened a file picker for the Step 3/4 CSV. Confusing in practice — the
  button's name told users to pick their `.mrb`, not a CSV they had to
  generate by hand first (there was no tool to generate it), so every real
  attempt failed with a "malformed file" error. The CSV/`csv_text` path
  itself (`parse_contacts_csv`, the `elec_import` job type,
  `POST /electrodes/import`) is unchanged and still works for a
  pre-resolved CSV from another source — just no longer what this button
  does.

### Automating Steps 1-4: raw `.mrb` upload with a review step (2026-08-06)

Steps 1-4 above were done by hand, once, against `data/bella_3dslicer.mrb` —
useful to establish the method, but not something to repeat manually per
subject. `services/electrodes.parse_mrb()` automates the same steps end to
end, with the two genuinely subject-scene-specific judgment calls from the
manual pass re-derived automatically rather than hardcoded:

- **Which markup node is the real per-contact list** (a scene can have more
  than one — Bella's had `F`, a 20-point per-electrode entry/target set,
  alongside `Contacts_8`, the real 184-point list). Picked as whichever
  `MarkupsFiducial` node has the most control points whose labels *all*
  parse as `electrode name + integer` (`G1`, `K'12`, ...) — `F`'s labels
  (`G'-10`) fail this because of the embedded `-`, which turned out to be
  exactly the right discriminator.
- **Which direction to apply the node's referenced registration transform**
  — the manual pass found Slicer's exported ITK transform parameters did
  *not* reliably match the direction implied by the transform node's own
  name (a BRAINSFit fixed/moving-convention trap, not something to trust
  from naming alone). Automated as: try both directions, keep whichever
  lands more points inside the subject's own `brainmask.mgz` (the same
  check done by hand in Step 2).

Because both of those are heuristics that can be wrong on an unfamiliar
scene, and this feeds electrode coordinates for epilepsy surgery planning,
`parse_mrb()` never writes contacts directly. The flow is a two-step
job/review split, mirroring the existing Detect → Review Clusters → Segment
pattern:

1. **`POST /subjects/{id}/electrodes/import/preview`** (`slicer_mrb_parse`
   job) — parses an uploaded `.mrb` (`POST /upload?file_type=mrb`, artifact
   kind `raw_mrb`) into a *preview*: a contacts list plus diagnostics
   (`node_name`, `candidate_node_names`, `transform_used`,
   `in_brain_fraction`, `warnings`), stored as a `slicer_contacts_preview`
   artifact — not `chnXyzDict`/`contact_txt`.
2. **`GET .../import/preview`** returns that preview for review.
3. **`POST .../import/preview/approve`** — synchronous (not a job; the heavy
   work already happened in step 1) — writes the preview's contacts via the
   existing `import_contacts()`, then discards the preview. **`POST
   .../import/preview/reject`** discards it without writing anything.

Web UI (`v2/web/src/features/electrodes/`): `ImportSlicerForm.tsx` now
uploads the raw `.mrb` and starts the preview job (progress bar, like
`RegisterCtStep`'s CT upload); `SlicerImportReviewPanel.tsx` shows the
diagnostics (in-brain % in red below 50%) with Approve/Reject buttons;
`components/three/SlicerContactsPreview.tsx` renders the pending contacts in
the 3D pane in orange (vs. black for approved contacts via
`ElectrodeContacts`) so it's visually obvious nothing's been committed yet.
`electrodes/clear_contacts()` (the "Delete Contacts" button) also discards
any pending preview, for a clean reset.

New server dependency: `h5py` (reads the `.mrb`'s ITK `.h5` registration
transform files) — added to `v2/server/pyproject.toml` and
`v2/server/requirements.txt` (the latter is what the Docker image installs
from).

Tests: `test_parse_mrb_picks_contact_like_node_over_others`,
`test_parse_mrb_picks_correct_transform_direction`,
`test_slicer_import_preview_approve_flow`, `test_slicer_import_preview_reject`,
`test_slicer_import_preview_requires_recon` — all against synthetically
constructed `.mrb`s (real zip/XML/JSON/HDF5 structure, not the real Bella
scene, which isn't a committed fixture). `parse_mrb()` was also run directly
against the real `data/bella_3dslicer.mrb` + `data/Bella/` as a one-off
sanity check: it reproduced the manual Step 1-4 analysis exactly (same node
chosen, `transform_used="inverse"`, `in_brain_fraction=0.8967...` — identical
to the hand-computed 89.67% from Step 2).

## Step 7 — Validate

- If a `hough3dlines`-derived contact set exists for the same subject, diff
  coordinates against the Slicer-imported set as a sanity check (not a
  correctness proof — Slicer is now the trusted source).
- Visually check a handful of contacts against the T1/CT overlay.
- Run the imported contacts through EI/HFO/SOZ end-to-end and confirm
  `soz.py` fusion produces sane output.

---

## Open items — status as of 2026-08-06

All resolved for `data/bella_3dslicer.mrb` + `data/Bella/` except naming,
which is blocked on missing data:

- ✅ Slicer version / bundle format — 5.x, external `Data/*.mrk.json`.
- ✅ Markup transform — live (not hardened); see Step 1 findings for the
  fixed↔moving naming gotcha in the `.h5` files.
- ✅ `.mrb`'s T1 vs. `recon-all`'s input — same scanner RAS frame, validated
  via brainmask overlap (Step 2).
- ⛔ Electrode/contact naming vs. EDF channel naming — can't check, no EDF
  for the Bella subject exists in the repo. Needed before Step 6.

**Next step: Step 6** (wire `import_contacts` into `v2/server`) can start
independent of Step 5/EDF — the naming-mapping table just needs to land
before `soz.py` fusion is run on real ictal data.
