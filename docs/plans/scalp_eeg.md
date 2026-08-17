# Plan: scalp-EEG source imaging (ESI) mapped onto Bella's SEEG shafts

**Status:** planned, not started. **Date:** 2026-08-17.

## Context

Bella's case currently has three SEEG-derived localizations that disagree
interestingly: ezfragility ranks shaft **D** (right posterior superior temporal
gyrus, ~19 mm outside the resection cavity) first, while the clinical EEG onset
was marked on **I** and **A**, both inside or at the margin of the failed
anterior temporal resection. All three methods read the *same* 8 SEEG
recordings, so their convergence is only partly independent.

The maintainer also holds **scalp EEGs from several years, at least three with
recorded seizures**, from before the lobectomy. That is a genuinely independent
line — different sensors, different physics, different seizure events years
apart — and the first **non-invasive** one in the dossier. It is exactly what
[project-direction.md](../project-direction.md) §"Validation roadmap" item 4
asks for.

Two questions, in the maintainer's stated priority order:

1. **Retrospective/clinical** — did the pre-surgical scalp EEG already contain
   evidence pointing away from a purely anterior right-temporal target?
2. **Methodological** — how well does a scalp-ESI contact ranking correlate with
   the EI / cEI / fragility contact rankings?

The scalp EDFs are not exported yet, so the plan is ordered so that the cheap
analysis that may answer question 1 outright runs first, and the head model —
the thing most likely to fail on paediatric anatomy — is validated behind a hard
checkpoint before any modelling investment.

## What was verified while planning

- **The recon T1 is PRE-op.** `v2/tools/fragility/resection_overlap.py` treats
  `mri/orig.mgz` as the *fixed* pre-op volume and registers a separate post-op
  NIfTI onto it. Recon anatomy, SEEG contacts and the pre-surgical scalp
  seizures therefore share one intact frame — **no pre/post bridging
  registration is needed.**
- **MNE source spaces land natively in the contacts' frame.**
  `setup_source_space("Bella", …, subjects_dir="data/Bella/subjects")` runs clean
  (oct5: 1026/hemi; ico4: 2562/hemi) and reports
  `coord_frame == FIFFV_COORD_MRI` (5) = FreeSurfer surface RAS, **in metres**;
  `src[i]["rr"] * 1000` reproduces `surf/lh.white` tkrRAS mm exactly. Contacts
  are tkrRAS **mm**. **The entire conversion is `/1000.0`** — no `trans`, no
  `c_ras`, no scanner RAS. `read_labels_from_annot(parc="aparc")` returns all 68
  labels, named `superiortemporal-rh`, which maps to `anatomy.label_contacts`'s
  `ctx-rh-superiortemporal` by a pure string transform.
- **`mri/orig.mgz` is a full head** (15.5% nonzero, bbox `[41 23 22]..[216 255 250]`
  vs `brain.mgz`'s `[63 61 36]..[189 190 185]`), so watershed and scalp-surface
  extraction have the tissue they need. It reaches the FOV edge in y — QC for
  head/neck clipping.
- **No `bem/`, no `mri/T1.mgz`, no local FreeSurfer** (`FREESURFER_HOME` points at
  a nonexistent path). But `mne.bem.make_watershed_bem` has a `volume=` argument,
  so **`volume="orig"` avoids needing `T1.mgz` entirely** — nothing is fabricated
  — and `docker images` shows **`brainquake-server:latest`** carrying
  `mri_watershed` and `mkheadsurf` at **FreeSurfer 8.2.0** *plus* MNE on its
  PATH. One container invocation, then everything else runs on the host venv.
- **`mri/transforms/talairach.xfm` is present**, so `fiducials="estimated"` works:
  `mne.coreg.get_mni_fiducials("Bella", …)` returns LPA/RPA/Nasion at
  `[-67.3, -19.5, -25.1]` / `[68.0, -6.2, -34.7]` / `[-4.6, 70.5, 12.4]` mm — a
  ~135 mm head width, plausible at age 4. The LPA/RPA asymmetry in y and z is
  worth eyeballing at QC.
- **`Coregistration.fit_icp` has an `eeg_weight`** parameter, so the
  template-montage→scalp workflow is supported. It needs `bem/outer_skin.surf`,
  which the watershed step produces.
- **`chnXyzDict.npy` does not exist** here — there is no `fslresults/` dir, so
  `electrodes.load_chn_xyz` will fail. Contacts must come from
  `electrodes.parse_mrb("data/bella_3dslicer.mrb", subject)`, re-derived live, as
  `v2/tools/fragility/contact_anatomy.py` already does. (This also side-steps the
  stale-`c_ras` hazard recorded in `docs/seeg_slicer_contact_import_plan.md:181`.)
- **The SEEG side of the correlation is mostly on disk**:
  `data/ei_reference/bella_ei_reference.csv` (per-contact `mean_ei`,
  `mean_rank_pct`, 2 reference arms × 184) and
  `data/ei_reference/bella_cei_paper.csv` (per-seizure `ei`/`out_degree`/`cei`,
  8 × 184). **Fragility is only a top-10 text dump** (`data/ezfragility_result.txt`);
  `run_frag.R:70` writes `frag_scores.rds` and no CSV — a per-contact export is a
  prerequisite (~3 lines of R).
- **The scalp montage is already documented**:
  `docs/seeg_slicer_contact_import_plan.md:190-197` records the 31 channels as
  `Fp2 F4 C4 P4 O2 F8 FT10 T8 P8 Fz Cz Pz Fp1 F3 C3 P3 O1 F7 FT9 T7 P7 EKG F10
  T10 P10 F9 T9 P9 Oz A1 A2`. All 30 non-EKG labels exist in MNE's
  `standard_1020` (verified). The inferior chain (F9/T9/P9, F10/T10/P10) is
  present, which is what makes temporal-source work viable at this density.
- **Installed**: mne 1.12.1, numpy, scipy, nibabel, scikit-learn, matplotlib
  (matplotlib is in the venv but *undeclared* in `pyproject.toml`). SimpleITK and
  pandas are absent and not needed.

## Scope

**A spike, not a feature** — same discipline as [cei_evaluation.md](../cei_evaluation.md):
sigproc modules + offline harnesses + an evaluation doc with explicit decision
gates. No job type, router endpoint, artifact kind, worker registration, UI or
SOZ-fusion integration until those gates are answered.

**Primary analysis uses pre-lobectomy scalp recordings only.** Post-resection
scalp EEGs are **deferred, not half-modelled**: a 17.3 mL cavity plus a
craniotomy skull defect is a breach-rhythm generator and a forward-model hole
that a 3-layer BEM cannot represent at all (it needs FEM — `duneuro`/`simnibs`,
not in this repo) and its own recon on the post-op T1. Running them on pre-op
anatomy would produce a confidently wrong localization.

## Prerequisites from the maintainer

Blocking for steps 3+; steps 0–2 need only items 1–3.

1. **Scalp EDFs exported**, one file per recording, each with its **recording
   date** and **pre- or post-lobectomy** status. *The surgery date is the single
   most important missing datum* — without it the primary/secondary split cannot
   be made.
2. **Seizure onset marks** per scalp seizure: EDF+ annotations (preferred — the
   repo already parses these, `v2/tools/fragility/seizure_timing.py`) or a CSV in
   the `v2/tools/fragility/seizures.example.csv` format
   (`label,edf_path,onset`, onset in seconds or `@<regex>`). Flag any file where
   the mark is a *clinical* rather than *electrographic* onset.
3. **Confirm the recording reference** (linked ears A1+A2? Cz? average?) and
   whether A1/A2 carry real signal or are flat.
4. **≥180 s of clean pre-ictal signal** before each marked onset *in the same
   file* — needed for the noise covariance. If clips are shorter, that is a
   re-export request, not something to work around.
5. **Interictal availability**: any continuous awake/sleep segment in a
   pre-surgical file? Sleep yields more spikes.
6. **Per-contact fragility CSV** (add a `write.csv` to `run_frag.R`, or export
   once by hand). Blocking for the agreement step only.
7. **FreeSurfer license path**, for the single Docker invocation.
8. Confirm `data/bella_3dslicer.mrb` `Contacts_8` is still the current localization.

---

## Step 0 — Raw scalp lateralization (½ day, no head model, do this first)

`v2/tools/esi/scalp_onset.py`. Per pre-surgical seizure: narrow-band ictal
envelope over `[onset, onset+5 s]` normalized to baseline, per channel, then

- the 5 highest channels,
- **left vs right inferior-temporal chain sums** (F9/T9/P9 vs F10/T10/P10 — the
  channels that actually see temporal sources),
- per-channel onset latency.

~60 lines, far more robust than ESI, and it validates the export, the channel
names, the reference and the onset marks before any modelling investment. **If it
says right-temporal-maximal, ESI must reproduce that or the pipeline is broken.
If it says left or bifrontal, that is already a substantive answer to question 1**
and ESI becomes corroboration rather than the load-bearing evidence.

Doing the hard thing first when the easy thing answers the question is the
failure mode to avoid here.

## Step 1 — Head model (1 day) ← **THE CHECKPOINT**

One container run, since `mri_watershed` is not installed locally:

```bash
docker run --rm \
  -v "$PWD/data/Bella/subjects:/data/subjects" \
  -v "$PWD/v2/docker/license.txt:/opt/freesurfer/license.txt" \
  brainquake-server \
  python -c "import mne; \
    mne.bem.make_watershed_bem('Bella','/data/subjects',volume='orig',overwrite=True); \
    mne.bem.make_scalp_surfaces('Bella','/data/subjects',mri='orig.mgz',force=True,overwrite=True)"
```

Then `v2/tools/esi/headmodel_bella.py` on the host: coregistration, `ico4`
source space, **five** forward solutions, QC table, `plot_bem` PNG, and the
tkrRAS assertion. Writes `data/esi/Bella-{ico4-src,bem-sol,trans,fwd-*}.fif`.

**Conductivity is a sweep, not a point estimate.** MNE's default `(0.3, 0.006,
0.3)` encodes an adult 1:50 brain:skull ratio; in-vivo adult estimates cluster
at 1:15–1:25 and a 4-year-old skull is thinner, more trabecular and more
conductive still. Default **`(0.33, 0.0165, 0.33)` = 1:20**, then build forwards
at ratios **{15, 20, 25, 50}** plus a fifth from
`mne.make_sphere_model(r0="auto", head_radius="auto")` as a permanent control.
**Do not tune it.** The *stability* across the five is the reported result.
Skull-conductivity error mostly biases depth and amplitude, much less tangential
position, so the lobar/hemispheric claim should survive it while a
"mesial vs lateral" claim may not — say that in the doc.

**Coregistration** (28 channels: drop `EKG`, drop `A1`/`A2` — ear electrodes sit
where the BEM scalp surface is least reliable, and if they are the reference
their traces carry no independent information):

```python
raw.set_montage("standard_1020", on_missing="raise")
coreg = mne.coreg.Coregistration(raw.info, "Bella", subjects_dir, fiducials="estimated")
coreg.set_scale_mode("uniform")          # not "3-axis": 3 fiducials + 28 template
coreg.fit_fiducials(nasion_weight=2.0)   # points cannot honestly constrain 3 axes
coreg.fit_icp(n_iterations=40, nasion_weight=2.0, eeg_weight=1.0, hsp_weight=0.0)
```

`standard_1020` is an adult template head; expect a uniform scale of ~0.90–0.97.
We scale the montage-to-head fit, not the MRI — the anatomy is Bella's own, so
`scale_mri` is neither used nor needed.

**QC checks, all cheap, all mandatory, all printed by the tool:**

1. `coreg.scale` ∈ [0.85, 1.05].
2. `coreg.compute_dig_mri_distances()` — median electrode-to-scalp **< 5 mm**,
   max **< 12 mm**. The single most informative number.
3. No electrode inside the outer-skin surface (within 2 mm of it or outside).
4. **Left/right symmetry**: |x(F9) + x(F10)| < 5 mm, same for T9/T10, P9/P10.
   A systematic asymmetry here directly biases the lateralization answer, which
   is the whole question.
5. Cz within 10 mm of the scalp vertex.
6. `plot_bem` matplotlib slices as a PNG (pyvista is not installed and adding it
   is not worth it), plus `bem/outer_skin.surf` for viewing in freeview/Slicer.

**`mri_watershed` is tuned for adults and fails more often on paediatric and
infant-recon output.** That is the reason this checkpoint exists. If it fails,
the fallback is `make_sphere_model` as the *primary* head model with a
lobar-only caveat — decided here, once, not improvised later.

Missing digitization costs ~5 mm of systematic electrode error → ~5–10 mm of
source error, which is small next to the ~20–30 mm intrinsic resolution of
31-channel ESI. **The channel count is the limiting factor, not the coregistration.**

## Step 2 — Sigproc modules + tests (2–3 days)

Pure numpy/scipy/mne/nibabel; no `app.config`, no models, no sqlalchemy — the
`sigproc/__init__.py:1-6` package rule.

**`app/sigproc/headmodel.py`** — `scalp_info(...)` → `mne.Info` with montage +
average-reference projection; `coregister(...)` → `(trans, qc_dict)`;
`build_forward(...)`; `sphere_forward(...)`;
`assert_src_is_tkrras_mm(src, subjects_dir, subject)` pinning the verified
invariant.

> **Keep scalp and SEEG channels in separate `Info` objects, always.**
> `channels.DEFAULT_SEEG_CONTACT_PATTERN` (`^([A-Za-z]|[A-Za-z]{1,2}')\d+$`)
> matches `F3`/`C4`/`T7`/`A1`, and Bella's SEEG shafts are literally named
> `F`, `T`, `P`, `A`. The scalp path must never route through
> `channels.load_seeg` / `channels.seeg_contacts`.

**`app/sigproc/esi.py`** — `dominant_ictal_band`, `rhythm_events`,
`spike_events`, `evoked_from_events`, `source_scalars`, `parcel_scores`,
`contact_scores_by_parcel`, `contact_scores_by_vertex`, `parcel_name_from_aseg`.
Contacts come in as plain dicts/arrays so `services/` stays out of `sigproc/`.

**`app/sigproc/agreement.py`** — `spearman`, `kendall`,
`block_permutation_test(x, y, blocks, statistic, n_perm=10000)`,
`topk_overlap`, `reliability`. This is a real simplification win: the ad-hoc
ranking/overlap logic currently duplicated across `cei_bella.py`,
`compare_ei_reference.py` and the fragility R scripts collapses into one tested
place. Adopting it in those callers is opportunistic, **not** in this spike's
required scope.

## Step 3 — ESI per seizure (1 day)

**Signal prep.** Mirror `v2/tools/cei_bella.py`'s crop-then-compute pattern
(`--pad` seconds of filtfilt runway, absolute recording seconds throughout).
Reuse `sigproc/filters.py` (`mains_harmonics`, `bandpass`, `clamp_band`) — 60 Hz
mains, Cleveland recording. Screen with `ei.find_saturated_channels`.
`set_eeg_reference("average", projection=True)`.

**Ictal windowing.**
1. Filter 1–45 Hz, notch 60/120/180.
2. Dominant ictal rhythm `f0` = peak of `[PSD(onset..onset+10 s) − PSD(onset−120..onset−60 s)]`
   in dB over 2–30 Hz. **Print it** — a child's onset rhythm is usually
   theta/alpha; if `f0` lands above ~25 Hz suspect EMG and refuse to proceed
   automatically.
3. Narrow-band to `[0.7·f0, 1.4·f0]`; events = GFP peaks in `[onset, onset+T]`.
4. `Epochs(tmin=-0.5/f0, tmax=0.5/f0)` → `.average()`.

**Average, don't invert raw samples.** 31-channel ictal EEG at onset has poor SNR
and heavy EMG; averaging 20–60 rhythm cycles buys ~5–8×. The same
`evoked_from_events` path serves the interictal branch, which is the reason to
build it this way.

Analysis instants: the **rising phase** `[-0.5/f0, 0]` is the headline (the peak
of a propagated rhythm is dominated by the largest generator; the rising phase by
the earliest), with the peak reported alongside. Also run `0–3 s` and `3–10 s`
separately — propagation over that interval is itself a finding.

**Interictal branch** (build it; availability unknown until export). Detect: 20–70 Hz
envelope, threshold at 5× robust (MAD) baseline SD, require a 1–35 Hz amplitude
peak within ±80 ms, ≥300 ms separation. Cluster by the peak topography
(correlation distance; `sigproc/clustering.choose_kmeans_k` already exists),
average within cluster, invert only clusters with ≥20 members, localize the
rising phase (~50% of peak). **This is where 31-channel ESI is actually good** —
averaged-spike ESI is far better validated than ictal ESI. If spikes exist this
may become the headline and the ictal arm the confirmation.

**Noise covariance** from `[onset−180 s, onset−60 s]` in the *same* recording,
identically filtered and referenced,
`compute_raw_covariance(method=["shrunk","empirical"], rank="info")`. Three
non-negotiable details, because this is where EEG inverse pipelines break
silently:
- the average-reference **projection must be applied before** covariance
  estimation and be present in `info` at inverse time;
- rank after average reference is **27** (28 − 1) — assert
  `compute_rank(cov, info=info) == 27` and honour `rank="info"` through
  `make_inverse_operator`;
- assert no baseline segment overlaps any annotation.

**Inverse: eLORETA on a fixed `ico4` cortical surface source space**, `dSPM` as a
second arm.
- *Not a beamformer*: LCMV assumes uncorrelated sources, and ictal rhythmic
  activity is highly correlated across the propagating network — the exact
  cancellation case where it fails. It is also the most sensitive to
  forward-model error, our largest known error.
- *Not dipole fitting as primary*: it assumes a single focal generator, which
  begs the clinical question.
- *eLORETA over dSPM/sLORETA* for the lowest depth bias. Not academic here:
  dSPM/MNE systematically pull toward superficial lateral cortex, and
  "mesial temporal vs lateral neocortical vs extratemporal" is exactly the
  discrimination being asked for. If eLORETA and dSPM disagree at the lobe level,
  report both and claim nothing.

`make_inverse_operator(loose=0.2, depth=0.8, fixed=False)`;
`apply_inverse(..., lambda2=1/9., method="eLORETA")` (SNR=3 on an average —
stated, not tuned).

`v2/tools/esi/esi_bella.py` emits a long CSV
`seizure, contact, shaft, parcel, esi_power_db, esi_onset_s, esi_power_db_vertex, head_model`
— one row per contact per head model, same shape as `cei_bella.py -o` so the join
is trivial.

## Step 4 — ESI → SEEG contact score (the novel step)

**Frame:** `src[i]["rr"] * 1000.0` *is* tkrRAS mm (verified); contacts from
`parse_mrb` are tkrRAS mm. **Divide by 1000, nothing else.** Pin it:

```python
assert np.allclose(src[0]["rr"][:len(v)] * 1000,
                   nibabel.freesurfer.read_geometry("surf/lh.white")[0], atol=1e-3)
```

**Two scalars per source vertex:**
- `power_db(v) = 10·log10( mean_t stc_ictal(v,t)² / mean_t stc_baseline(v,t)² )`,
  where the baseline STC is the same inverse applied to a baseline evoked built
  the same way. **This ratio, not raw eLORETA amplitude, is what cancels the
  depth/leadfield weighting** that would otherwise make the ranking a map of the
  head model rather than of the seizure.
- `onset_s(v)` = first crossing of `baseline_mean + 3·SD` by the 1-s-smoothed
  source power. Reuse `ei.determine_threshold_onset` verbatim, handing it source
  time courses in place of channel time courses.

**Headline arm — parcel.** `extract_label_time_course(stc, labels, src,
mode="mean_flip")` for the time-resolved view; for the scalar, aggregate
`power_db` per label with `max` (a small hot parcel should not be diluted by its
quiet half — the same argument `montage.project_pairs_to_contacts` already
makes). Each contact takes its parcel from `anatomy.label_contacts(radius_mm=3.0)`:
`ctx-{lh,rh}-X` → `X-{lh,rh}`; otherwise fall back to
`nearest_structure.label_name`; **subcortical → NaN. Do not invent a value.**
Every contact in a parcel gets the same score — **that is the point, not a
limitation**: 31-channel ESI cannot distinguish two contacts 5 mm apart, and
encoding that in the data structure prevents the reader from over-reading the
ranking.

**Robustness arm — vertex neighbourhood.** Per contact, Gaussian-weighted
(σ = 10 mm) mean of `power_db` over vertices within **R = 20 mm** — R matches the
actual resolution, not the contact spacing. Non-NaN for WM contacts too. Report
Spearman between the two arms; **< 0.7 means one of them is wrong.**

**Deep-structure arm (clearly secondary).** Many contacts sit in mesial temporal
grey where a cortical surface space has no vertices, and mesial temporal is *the*
structure at issue. Add a mixed space:

```python
src_vol = mne.setup_volume_source_space("Bella", pos=5.0, mri="aseg.mgz",
    volume_label=["Left-Hippocampus","Right-Hippocampus","Left-Amygdala",
                  "Right-Amygdala","Left-Thalamus","Right-Thalamus"], ...)
src_mixed = src_surf + src_vol      # loose=1.0 for the volume part
```

Use it **only** to answer "is there any mesial-temporal source at all", never for
contact ranking: 31 surface electrodes cannot resolve hippocampus from adjacent
neocortex, and eLORETA on a deep-source arm over-reports deep sources. Caveat it
hard; keep it out of the headline agreement statistic.

**A per-shaft coverage table is a required output**: n contacts, n with a parcel,
n falling back to `nearest_structure`, n NaN. **If >30% are NaN the parcel arm
cannot be the headline** and the vertex arm takes over.

## Step 5 — Controls (½ day) — run these *before* the headline number

Running controls afterwards invites motivated reasoning.

1. **Baseline surrogate.** Whole pipeline on a preictal window, events at the
   same rate, no seizure. ρ against EI/cEI/fragility should be ≈ 0. Strongest
   control: it exercises head model, coreg, inverse and mapping, so any residual
   agreement is pure geometry.
2. **Anatomy-only sham.** Score each contact by geometry alone (e.g. −|distance
   to scalp|, or cortical proximity), no EEG at all. If this "correlates" with
   EI/fragility as well as ESI does, ESI added nothing and the agreement is an
   implantation artefact. ~15 lines, and the control that matters most.
3. **Contralateral flip.** Mirror the source map across the midline and re-score.
   Agreement must collapse; if it does not, the analysis has no lateralizing
   power and question 1 is unanswerable by this method.

## Step 6 — Agreement with the SEEG rankings (1 day)

`v2/tools/esi/esi_vs_seeg.py`, joining the ESI CSV against
`data/ei_reference/bella_cei_paper.csv` and the fragility CSV. Run
`fusion.describe_name_overlap` **before trusting anything**.

### The statistical trap, stated first

**Contact-level n = 184 is pseudo-replication.** Contacts on a shaft are 3.5 mm
apart, share a parcel, and under the parcel arm literally share a score. The
effective sample size is closer to **20 (the shafts)** than 184. A contact-level
Spearman p-value or a hypergeometric top-K test on n=184 will produce p < 10⁻⁶
from noise and is **unsound**. Do not compute one without a block-permutation
null.

### Design

- **Primary: shaft level, n = 20.** Shaft score = mean rank-percentile of its
  contacts (the `cei_bella.mean_shaft_pct` convention), via the existing
  `fusion.rank_pct` — *do not re-implement it*; `cei_bella.py:125` and
  `compare_ei_reference_subject.py:95` already duplicate it and that is a known
  smell, not a pattern to extend. `scipy.stats.spearmanr` (new to the repo;
  scipy is already a dependency, and `compare_ei_reference.py` sets the precedent
  for importing from `scipy.stats`) plus Kendall τ-b, with bootstrap CIs over
  shafts. **At n=20, |ρ| must exceed ~0.45 for p<0.05 — say this up front so the
  result is not read after the fact.**
- **Secondary: contact level with a shaft-block permutation null.** Permute whole
  shafts' score vectors among shafts of equal length (or rotate contact indices
  within a shaft), 10 000 draws. This preserves within-shaft autocorrelation and
  is the only defensible contact-level p-value here.
- **Top-K overlap**, K ∈ {10, 20} contacts and {3, 5} shafts, against the *same*
  block null — not hypergeometric. (Hypergeometric is the singleton-block special
  case, which makes a nice unit test.) This is the only metric fragility can join
  on until the per-contact CSV exists.

### Reliability ceiling — the gate before any cross-modality number

Cross-modality agreement is meaningless without each modality's own test-retest
agreement.

1. Between-seizure Spearman of the ESI shaft vector across the ~3 scalp seizures.
2. Same for EI, cEI, fragility across the 8 SEEG seizures (`cei_bella.py -o`
   already has everything needed for EI/cEI).
3. **Gate: if ESI between-seizure median ρ < 0.5, stop.** A ranking that is not
   reproducible against itself cannot meaningfully agree or disagree with
   anything.
4. Report every cross-modality ρ **as a fraction of the geometric mean of the two
   within-modality reliabilities**. "ESI vs fragility ρ = 0.42, ceiling 0.55" is
   honest; "ρ = 0.42" alone is not.

### What can and cannot be concluded — into the doc verbatim

**Can:**
- Positive agreement is informative: two independent modalities, different
  recordings years apart, converging on the same shafts is real evidence.
- A clear ESI result pointing outside the right temporal lobe on pre-surgical
  scalp seizures speaks directly to question 1 — subject to the controls and the
  five head models.
- A methods statement: "with 31 channels, a template montage and a subject BEM,
  scalp ESI reproduces / does not reproduce the SEEG shaft ranking at ρ = X."

**Cannot:**
- **A negative ESI result is weak.** Low-density (≤32-ch) ESI sensitivity is
  roughly 55–60% versus ~85% at high density. Any "the scalp EEG pointed away
  from right temporal" claim must survive all three controls and all five head
  models, and even then must read **"did not support"**, never "excluded".
- **No contact-level claim.** Shaft and lobe only.
- **Disagreement is not attributable.** Scalp and SEEG events are different
  seizures, years apart, possibly different types, possibly a network changed by
  resection. Low agreement could be ESI failure, SEEG-metric failure, or
  genuinely different events — this design **cannot** distinguish them, and no
  amount of statistics will make it able to.
- **n≈3 scalp seizures.** Report per-seizure always; never present a pooled
  n=1-equivalent ranking as a finding (the existing convention in `cei_bella.py`
  and `bella_fragility_resection_analysis.md`).

---

## Files

| Path | What |
|---|---|
| `v2/server/app/sigproc/headmodel.py` | **new** — scalp `Info`, coreg + QC, forward solutions, tkrRAS assertion |
| `v2/server/app/sigproc/esi.py` | **new** — band/event detection, evoked, inverse scalars, parcel/vertex contact mapping |
| `v2/server/app/sigproc/agreement.py` | **new** — Spearman/Kendall, block-permutation null, top-K overlap, reliability |
| `v2/tools/esi/README.md` | **new** — Docker one-liner, prerequisites, file contract, matplotlib note |
| `v2/tools/esi/scalp_onset.py` | **new** — Step 0, no head model |
| `v2/tools/esi/headmodel_bella.py` | **new** — the checkpoint artifact |
| `v2/tools/esi/esi_bella.py` | **new** — per-seizure ESI → long CSV |
| `v2/tools/esi/esi_vs_seeg.py` | **new** — the agreement tables |
| `v2/server/tests/test_esi.py`, `tests/test_agreement.py` | **new** — see Verification |
| `docs/esi_evaluation.md` | **new** — evaluation doc + decision gates, shaped like `docs/cei_evaluation.md` |

A `v2/tools/esi/` subdir (mirroring `v2/tools/fragility/`) because this is a
multi-stage pipeline with a Docker step and needs a README. Follow the existing
tool conventions: `sys.path.insert(0, ../server)`, argparse with hardcoded
sensible defaults, stdout tables + optional `-o` CSV.

**Reuse (do not reimplement):** `electrodes.parse_mrb` / `vox2ras_tkr`;
`anatomy.{label_contacts, find_segmentation, label_name, load_label_lut, is_structure}`;
the `SimpleNamespace(name="Bella")` + `SUBJECTS_DIR` pattern from
`v2/tools/fragility/contact_anatomy.py`; `fusion.{rank_pct, describe_name_overlap}`;
`montage.parse_contact`; `ei.{determine_threshold_onset, ONSET_THRESHOLD_K, find_saturated_channels}`;
`filters.{bandpass, clamp_band, mains_harmonics}`; `clustering.choose_kmeans_k`.

**Do not use** `channels.load_seeg` / `channels.seeg_contacts` for scalp data
(regex collision), or `electrodes.load_chn_xyz` (its file does not exist here).

**No new dependencies.** mne, nibabel, numpy, scipy, scikit-learn are all
declared and installed. matplotlib is used only by the tools' QC plot — import it
lazily inside the plotting function and note it in the tools README rather than
adding a server dependency for a plot.

## Verification

1. **Head-model QC** — the six checks in Step 1, plus `plot_bem` slices showing
   nested, anatomically correct inner-skull / outer-skull / scalp surfaces.
2. **`tests/test_esi.py`**
   - `test_src_rr_is_tkrras_millimetres` — synthetic src; asserts ×1000 and that
     nothing else is applied. (The real-subject version is an assertion inside
     `headmodel.py`, not a test, since `data/` is gitignored.)
   - `test_parcel_name_from_aseg_round_trip` — `ctx-rh-superiortemporal` →
     `superiortemporal-rh`; `Right-Hippocampus` → `None`.
   - `test_contact_scores_by_vertex_matches_reference` — plain readable loop
     pinning the vectorized version. The `test_batch_h2_matches_the_scalar_reference`
     pattern from `test_connectivity.py`.
   - `test_dominant_ictal_band_finds_injected_rhythm` — 6 Hz sine + 1/f noise →
     `f0` within 0.5 Hz; `rhythm_events` count ≈ duration × 6.
   - `test_scalp_montage_excludes_seeg_lookalikes` — the regression guard: assert
     `scalp_info` yields 28 channels *and* that `channels.seeg_contacts` on the
     same list would wrongly return F3/C4/T7/A1, pinning the hazard so nobody
     "simplifies" the two paths together later.
3. **`tests/test_agreement.py`**
   - `test_spearman_matches_scipy`.
   - `test_block_permutation_reduces_to_hypergeometric_for_singleton_blocks` —
     the readable-reference pin.
   - `test_block_permutation_p_is_uniform_under_independence` — 500 draws, mean p
     in [0.4, 0.6], *and* demonstrate that the naive non-block test on
     autocorrelated blocks gives mean p ≪ 0.5. This test documents why the block
     null exists.
   - `test_reliability_of_identical_vectors_is_one`.
4. **Synthetic dipole round-trip** — place a known dipole, simulate scalp data
   through the same forward solution, add noise, run inverse + contact mapping,
   assert the top-scoring contact is nearest the true dipole within a stated
   tolerance. Pins the whole frame/unit chain end to end.

## Decision gates (each a hard stop, answered in `docs/esi_evaluation.md`)

- **G0 — head model.** Three non-intersecting watershed surfaces; `make_bem_model`
  succeeds; median electrode-to-scalp < 5 mm; uniform scale ∈ [0.85, 1.05];
  L/R electrode symmetry < 5 mm. **Fail → the analysis does not happen**, and
  report it: that is a real finding about paediatric BEM tooling.
- **G1 — sensitivity.** Parcel-score Spearman ≥ 0.8 across all five head models,
  identical hemisphere and top-3 parcels. **Fail → report as
  conductivity-dependent; make no lateralization claim.**
- **G2 — controls.** Baseline surrogate within noise; anatomy-only sham does not
  match ESI's agreement; midline flip collapses it. **Fail → the "agreement" is
  geometry, not physiology; stop.**
- **G3 — reliability.** ESI between-seizure median ρ ≥ 0.5. **Fail → "31-channel
  ictal ESI is not reproducible enough on this data to compare" — a legitimate
  answer to question 2.**
- **G4 — the actual question.** Only if G0–G3 pass: shaft-level ρ with CIs and
  ceilings, and the lobar/hemispheric answer to question 1, phrased "supported /
  did not support".
- **G5 — promotion.** Job type / router / artifact only if the pipeline runs
  end-to-end unattended on a second subject. One patient is not a reason to build
  plumbing.

## Things to refuse

1. Any contact-level or millimetre-level claim from 31-channel ESI. The mapping
   is built so this is structurally hard, not merely discouraged in prose.
2. A contact-level correlation or top-K test without a shaft-block null.
3. Reading a negative ESI result as evidence *against* right temporal.
4. Analysing post-resection scalp EEG on pre-op anatomy.
5. Treating scalp-vs-SEEG disagreement as a methods result.
6. Running Bartolomei's literal EI bands on scalp source time courses — the
   high-frequency numerator at the scalp is dominated by EMG. If an EI-form
   composite is wanted, set the band per-seizure from the observed ictal rhythm,
   and do not make it the headline.
7. Tuning skull conductivity until the answer comes out. One default (1:20), a
   four-point sweep, and the *stability* is the result.
