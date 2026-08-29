# Scalp-EEG spike: implementation order, reconciled with the data that arrived

**Status:** ready to execute; Phases A/B/D unblocked, Phase C blocked on the
maintainer. **Date:** 2026-08-24. Companion to [scalp_eeg.md](scalp_eeg.md),
which stays the design document; this is the working order.

## Context

[scalp_eeg.md](scalp_eeg.md) was written on 2026-08-17/23 against scalp EEGs that
were **not yet exported**. They are now on disk (`datasets/ScalpEEG/`, 5 studies,
359 files, all carrying `eeg2edf-sidecar/1` JSON), and the SEEG study has been
**re-exported** to `datasets/BellaNew/` after nk2edf bug fixes. Several of the
design document's load-bearing assumptions are wrong as a result, and its Step-6
join targets were produced by the buggy converter.

This plan does three things: rebuild the SEEG side on the fixed export, run
Step 0 (fully unblocked today), and correct the design document to match
reality. It does **not** start head modelling — that stays behind the G0
checkpoint and two maintainer prerequisites.

### The timeline, now pinned from sidecar dates

| When | What | Seizures |
|---|---|---|
| 2022-09-21 | Ichilov scalp VEEG, age 2, `nicolet-nervus` | **4**, reviewer-marked |
| 2024-02-06→13 | Cleveland scalp EMU phase-I, `nihon-kohden` | **2** (`SZ 1P`, `SZ 2P`) |
| 2024-02-13 | PET/MR | — |
| 2024-03-15→23 | **Cleveland SEEG implant** (`datasets/BellaNew`) | **8** (`SZ 1P`–`SZ 8P`) |
| 2024-03-23 → 05-01 | right temporal lobectomy | — |
| 2024-05-01→03 | Cleveland post-op scalp VEEG | 0 |
| 2025-06-29 | Sheba post-op scalp VEEG, `micromed-vwr` | 1 unmarked "Trigger" |

### What the arriving data changes

1. **The best montage is the oldest recording.** Ichilov 2022 is the *only*
   study with the inferior temporal chain (`F9/T9/P9`, `F10/T10/P10`) plus
   `A1/A2` and `Oz` — 30 EEG ch at 512 Hz, 4 focal seizures, 339–466 s of
   pre-ictal runway per clip. Cleveland has `TP9/TP10 FT9/FT10` but **no
   inferior chain**, 25 EEG ch at 200 Hz. The design document assumed the reverse.

2. **Every export is clip-based, and that is the hospital's doing, not a
   converter bug.** Verified by byte arithmetic against the source `.EEG`:

   | File | Size | implied by ch×Hz×2B | nk2edf extracts | Δ |
   |---|---|---|---|---|
   | `CA6476I6.EEG` | 342,560,789 B | 3.21 h @ 200 Hz × 74 words | 93 clips = 3.21 h | 0.12% |
   | `DA6465AU.EEG` | 12,173,286,149 B | 6.31 h @ 1000 Hz × 268 words | 63 clips = 6.31 h | 0.03% |

   The residual is exactly the per-block headers and channel tables; the `.EGF`
   companions are 670–4056 byte stubs. Monitoring ran 24×7, but the review
   station stored only its saved clips: **2.2%** of the 149 h scalp admission,
   **2.9%** of the 217 h SEEG admission. `nk.read_blocks` enumerates the file's
   own extended datablock table and is not dropping anything.

   **The clips are event-centred and annotation-complete.** `nk2edf --dump-log`
   on `CA6476I6` reports **447/447 log events fell inside a clip**, and the
   events sit ~30 s into stereotyped 61/91/151/421 s windows (`SPK left frontal`
   at 30.587 s of 61 s; `SPK left FC` at 30.694 s of 61 s). So the archive is
   "every marked event ± fixed padding", not a sample. What is missing is
   *unannotated* background — which costs true spike density (techs do not mark
   every discharge) but loses no flagged finding.

   Consequence: both Cleveland seizure clips put the mark at ~120 s, so the
   180 s same-file covariance prerequisite cannot be met there and cannot be
   recovered by re-converting. **Draw the noise covariance from adjacent
   non-ictal clips on the same night** (clips 06 and 08 bracket clip 07's
   seizure) — same amplifier, montage, reference and impedance state, which is
   what "same file" was protecting. Ichilov needs no such relaxation.

   Upside: those 93 clips exist *because a tech marked something*, so IED
   density inside them is far above that of random continuous EEG. This
   strengthens the interictal arm.

3. **Cleveland's seizures are epileptic spasms** (`SZ 2P epileptic spasms
   (cluster = X)`) with nurse-typed real-time marks — clinical, not
   electrographic. Ichilov's marks (`fp1 onset`, `p8 onset`, each paired with an
   `offset`) are reviewer-placed.

4. **"Years apart" is wrong for the Cleveland arm.** Those 2 scalp seizures are
   **5 weeks** before the SEEG seizures, same clinical state, same admission
   series. The "cannot attribute disagreement" caveat should be scoped to the
   2022 arm only.

5. **Cleveland's reference is `SystemReference=F3,C3`** from the `.21E`
   `[SYSTEM_SETUP]` section — genuine NK metadata, not a converter bug; `A1/A2`
   are absent and the log says `A1+A2 OFF`. Answers prerequisite 3 without
   asking. Both reference electrodes are stored channels, so average-referencing
   recovers the topography and the `rank == n_eeg − 1` assertion holds.

6. **Four seizures carry an electrographic onset mark, not two** — SZ1P, SZ2P,
   `EEG onset JB` (SZ3P), `EEG onset - IA fast` (SZ7P). The marks were never
   missing from the data: `seizure_timing.py` matched `^EEG onset$`, an anchored
   pattern that rejects a reviewer's initials or a localising note. Relaxed to
   `^EEG onset\b`; `timing.csv` regenerated. Two `Arousal-SZ` events also appear
   that are not in the set of 8.

7. **cEI is not in the tree.** `sigproc/cei.py`, `sigproc/connectivity.py` and
   `v2/tools/cei_bella.py` do not exist despite being described in
   [cei_evaluation.md](../cei_evaluation.md) and cited throughout the design
   document. Dropped from scope.

---

> **The API server being down blocks nothing in Phases A, B or D.** Those are
> offline CLI tools importing `app.sigproc.*` directly — no FastAPI, no worker,
> no SQLite. Only the Phase C recon wants the job runner, and it is deferred
> anyway; it can also be run as a bare `recon-all` in the container.

## Phase A — rebuild the SEEG side on `datasets/BellaNew` — **DONE 2026-08-28**

Outcome: the nk2edf fix touched only annotation timing (sub-second `.LOG`
resolution, bookmark de-duplication) — no sample changed. Marks moved by up to
0.94 s. **All conclusions in
[bella_fragility_resection_analysis.md](../bella_fragility_resection_analysis.md)
survive** (ratio 0.986→0.987, d +0.24→+0.37, SOZ pctile 55.5→59.0, D still the
top shaft). Python fragility now passes parity against EZFragility on identical
windows (ρ 0.866, same top shaft). Per-contact CSVs written for EI and fragility.
Two tooling bugs fixed — see A1/A4. Still open: SZ 2P is amplifier-clipped on
152/184 contacts, and the "D was missed" reading fails a base-rate check.

The 8 seizure filenames already hardcoded in `v2/tools/verify_fragility_bella.py`
(lines 32–41) match `datasets/BellaNew` **exactly** — this is a repoint, not a
rewrite.

**A1. Fragility (Python).** Turn `BELLA_DIR`
(`verify_fragility_bella.py:30`) into an `--edf-dir` argparse option defaulting
to `datasets/BellaNew`. Re-run all 8 through
`app.sigproc.fragility.compute_fragility_pipeline`. Print the per-seizure R²
median as before.

**A2. Re-run the R oracle on the same fixed export.** `data/ezfragility_result.txt`
was computed from the *old* export, so parity against it would compare two
different inputs. Re-export from BellaNew with
`v2/tools/fragility/export_edf.py --manifest` (the `label,edf_path,onset`
format, `@^SZ 1P$` regex onsets), then `run_frag.R` → `frag_to_csv.R`.
`frag_to_csv.R` already exists, which appears to close prerequisite 6
(per-contact fragility CSV) — confirm it emits one row per contact before
assuming so.

**A3. EI.** `v2/tools/EI_all_seizures.ipynb` reads
`EDF_DIR = "../../data/Bella/BellaEDF"` and **globs `*.edf`** — repointing it at
BellaNew would sweep in all 63 clips. Promote the batch to `v2/tools/ei_bella.py`,
a small CLI shaped like `verify_fragility_bella.py`: the same explicit
8-seizure list, `--edf-dir`, `-o` per-contact CSV, reusing `app.sigproc.ei`.
This kills the stale-path problem permanently and gives Step 6 a stable join
target that a notebook cannot. Leave the notebook as the exploratory view.

**A4. Reconcile the annotations.** Resolve what `EEG onset - IA fast` names
(shaft I? contacts I and A? — A and I are the clinical onset shafts per
`GT_ONSET_SHAFTS`, `verify_fragility_bella.py:43`), then correct the "only SZ 1P
and 2P" sentence in
[bella_fragility_resection_analysis.md](../bella_fragility_resection_analysis.md)
and re-state its five-window robustness argument now that four electrographic
onsets exist.

**A5. Re-check the headline.** Re-run `frag_outcome.R` and update the
interpretability ratio / Cohen's d / SOZ-percentile table. **If those numbers
move, the document's conclusion moves with them** — that is the whole point of
the re-run. Update memory `bella-fragility-missed-resection` if so.

## Phase B — Step 0: raw scalp lateralization

Unblocked today. No head model. Runs on **both arms** so the
ictal-vs-interictal headline is chosen from evidence rather than assumed.

**`v2/server/app/sigproc/scalp_montage.py`** — the per-file channel contract:
`normalize_labels(edf_ch_names, sidecar=None)` (strip `-<ref>` suffix and `EEG `
prefix, case-fold, map onto `standard_1005`; `T1`/`T2` → `FT9`/`FT10`),
`classify_channels(...) -> (eeg, aux, unknown)`, `laterality_chains(canonical)`.
The three real montages it must handle are now known:

| Study | Labels the normalizer must survive |
|---|---|
| Ichilov | `Fp2 F4 C4 P4 O2 F8 FT10 T8 P8 Fz Cz Pz Fp1 F3 C3 P3 O1 F7 FT9 T7 P7 EKG F10 T10 P10 F9 T9 P9 Oz A1 A2` |
| Cleveland | `FZ CZ PZ FP1 F3 C3 P3 O1 F7 T7 TP9 NR1 FP2 F4 C4 P4 O2 E F8 T8 P7 FT9 P8 FT10 TP10 NR2 EKG1 EKG2` + `EEG Mark1/2`, `Events/Markers` |
| Sheba | `Fp1-G2 T1-G2 … T3 T4 T5 T6 A1 A2 elA24-G2 EMG1+-EMG1- PNG1+-PNG1- ECG1+-ECG1- thor+-thor- abdo+-abdo- xyz+-xyz- MKR+-MKR-` |

`NR1`/`NR2`/`E`/`elA24` and every `Mark`/`MKR`/`Events` label route to
aux/unknown and are **reported, never silently dropped**. Cleveland is
all-uppercase (`FZ`, `FP1`) — case-folding is load-bearing, not cosmetic.

**`v2/tools/esi/scalp_onset.py`** — per pre-op seizure: narrow-band ictal
envelope over `[onset, onset+5 s]` normalized to baseline; top-5 channels;
left-vs-right lowest-available temporal chain sums; per-channel onset latency.
Reads onsets from the sidecar JSON (`events[]` where **`onset_s is not None`** —
that field is clip-local and null for events belonging to sibling clips, which is
the contract that makes the multi-clip Ichilov study parseable), falling back to
EDF+ annotations via `v2/tools/fragility/seizure_timing.py`.

Six pre-op seizures: Ichilov clips 00/01/02/03 and Cleveland clips 07/54.

A `--census` flag prints, per study, the seizure and IED-annotation counts read
from the sidecars, plus **clips / recorded hours / wall-clock span / coverage %**
so the "is data missing?" question is answerable at a glance instead of
re-derived from byte arithmetic. ~15 lines inside this tool, **not** a separate
harvest tool or doc.

IED labels use at least three prefixes — `SW`, `IS` and `SPK` (`SW left
frontal`, `IS R POST TEMP`, `SPK left FC`) — with inconsistent case and
truncation at 20 chars. Match all three; report anything unmatched rather than
assuming the vocabulary is closed.

## Phase C — head models (blocked; do not start)

Two prerequisites, both on the maintainer:

1. **Restore `data/`** — this machine holds only `S1.zip` + `manifest.json`.
   Step 1 needs `data/Bella/subjects/Bella` with `mri/orig.mgz`,
   `mri/transforms/talairach.xfm` and `surf/`.
2. **The 2-year-old MRI, as DICOM.** Convert with `mri_convert <first-dicom>
   datasets/Bella2YOT1.nii.gz` inside `brainquake-server` (FreeSurfer 8.2.0 is
   already on its PATH) rather than adding `dcm2niix` as a dependency — and QC
   that the result is a **full head**, since watershed and `make_scalp_surfaces`
   need scalp tissue. Then `recon-all` as subject `Bella2YO` (hours, needs
   `FS_LICENSE`).

**Design simplification that removes cross-timepoint registration entirely:**

- **`Bella2YO` + Ichilov 2022** answers **question 1** (did the pre-surgical
  scalp point away from right anterior temporal?). That is a *lobar/hemispheric*
  claim, so it never needs to touch SEEG contacts — no 2yo→4yo warp anywhere.
- **`Bella` (4yo) + Cleveland Feb-2024** answers **question 2** (agreement with
  the SEEG contact rankings). Same anatomy as the contacts, 5 weeks from the
  SEEG seizures.

Each question runs on the data and the anatomy that suit it. Step 1's six QC
checks and the G0 gate apply **per subject**, and the 2022 arm's `coreg.scale`
bound may need widening — `standard_1005` is an adult template and this is a
2-year-old head.

## Phase D — correct the design document

Amend [scalp_eeg.md](scalp_eeg.md) in place:

- Mark prerequisites 1–5 answered by the data, with the timeline table above.
- Prerequisite 6: `frag_to_csv.R` exists — verify and mark done.
- Prerequisite 8: the contact file is `datasets/Bella Seeg.mrb`, not
  `data/bella_3dslicer.mrb` (see `v2/tools/fragility/contact_anatomy.py:10`).
- Delete every cEI reference (`cei_bella.py`, `bella_cei_paper.csv`, the cEI
  callers in the `sigproc/agreement.py` spec); Step 6 joins ESI against **EI and
  fragility only**, using the Phase A CSVs.
- Scope the "different seizures years apart" caveat to the 2022 arm.
- Record the montage/reference/semiology facts and the clip-based export
  (coverage table above); replace the 180 s same-file covariance rule with
  "180 s from the same file where available (Ichilov), otherwise pooled from
  adjacent non-ictal clips on the same night (Cleveland)", and say why.
- Add the two-anatomy design from Phase C.

Add a status note to the top of [cei_evaluation.md](../cei_evaluation.md)
recording that the code it describes is not in the tree.

Extend memory `nkt-eeg2100-file-inventory` with the clip-coverage finding and
the byte-arithmetic check that establishes it — "is nk2edf losing data?" is a
natural question to re-ask, and re-deriving the answer is not free.

---

## Files

| Path | What |
|---|---|
| `v2/tools/verify_fragility_bella.py` | **edit** — `--edf-dir`, default `datasets/BellaNew` |
| `v2/tools/ei_bella.py` | **new** — 8-seizure EI batch CLI, per-contact CSV |
| `v2/tools/EI_all_seizures.ipynb` | **edit** — repoint `EDF_DIR`, stop globbing |
| `v2/server/app/sigproc/scalp_montage.py` | **new** — per-file label normalizer, classification, laterality chains |
| `v2/tools/esi/scalp_onset.py` | **new** — Step 0 + `--census` |
| `v2/tools/esi/README.md` | **new** — prerequisites, per-study montage table, file contract |
| `v2/server/tests/test_scalp_montage.py` | **new** — the 5 tests below |
| `docs/bella_fragility_resection_analysis.md` | **edit** — A4, A5 |
| `docs/plans/scalp_eeg.md` | **edit** — Phase D |
| `docs/cei_evaluation.md` | **edit** — status note |

**Reuse, do not reimplement:** `app.sigproc.fragility.compute_fragility_pipeline`;
`app.sigproc.ei.{determine_threshold_onset, ONSET_THRESHOLD_K, find_saturated_channels}`;
`app.sigproc.filters.{bandpass, clamp_band, mains_harmonics}` (60 Hz mains at
Cleveland, **50 Hz at Ichilov** — Israel);
`app.sigproc.fusion.{rank_pct, describe_name_overlap}`;
`v2/tools/fragility/export_edf.py`, `seizure_timing.py`; the
`sys.path.insert(0, ../server)` + argparse + stdout-table + `-o` CSV convention
from `v2/tools/`.

**Do not use** `channels.load_seeg` / `channels.seeg_contacts` on scalp data —
`DEFAULT_SEEG_CONTACT_PATTERN` matches `F3`/`C4`/`T7`/`A1`, and Bella's SEEG
shafts are literally named `F`, `T`, `P`, `A`.

**No new dependencies.** mne, numpy, scipy, nibabel, scikit-learn are declared
and installed.

## Verification

1. **Phase A** — all 8 seizures produce 184 contacts × ~239 windows; per-seizure
   R² median printed and comparable to the 0.841–0.998 range on record; Python
   fragility vs the re-run R oracle at Spearman ≥ 0.8 with matching top shaft
   (`SPEARMAN_MIN`, `verify_fragility_bella.py:48`). Both CSVs land with 184 rows
   and joinable contact names — check with `fusion.describe_name_overlap` before
   trusting any join.
2. **`pytest v2/server/tests/test_scalp_montage.py`** — five tests, written
   against the three real montages:
   - `test_legacy_1020_names_resolve` — `T3/T4/T5/T6`, `Fp1-G2`, uppercase `FP1`.
   - `test_t1_t2_map_to_ft9_ft10` — pins the **side**; a swap silently inverts
     the lateralization answer.
   - `test_unmapped_labels_are_reported_not_dropped` — `elA24`, `NR1`, `E`,
     `MKR+-MKR-`, `thor+-thor-` reach aux/unknown and appear in the report.
   - `test_laterality_chains_degrade_without_inferior_chain` — Ichilov →
     `F9/T9/P9` vs `F10/T10/P10`; Sheba → `T1/T3/T5` vs `T2/T4/T6`; Cleveland
     has neither → raises rather than guessing.
   - `test_sidecar_reference_beats_string_parsing`.
3. **Step 0 end to end** — `python v2/tools/esi/scalp_onset.py` over the 6 pre-op
   seizures prints, per seizure, the top-5 channels, the chain-sum contrast with
   the chain pair it chose, and onset latencies. Sanity anchors already in the
   annotations: Ichilov clip 00 is marked `fp1 onset`, clip 02 `p8 onset` — if
   the tool disagrees with the reviewer on those two, the tool is wrong.
   `--census` reports counts and coverage per study.

## Out of scope

Head modelling, ESI, contact mapping, controls, agreement statistics — all
behind Phase C's prerequisites and the G0 gate. No job type, router endpoint,
artifact kind, worker registration or UI (G5). No post-resection ESI. No cEI.
