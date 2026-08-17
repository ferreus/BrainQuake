# CAR vs bipolar reference for EI, on ds004100

**Date:** 2026-08-16
**Question:** our EI applies a common-average reference; Bartolomei 2008 and Brainstorm's
epileptogenicity process both use bipolar. Does the choice change SOZ localization?

**Answer: yes, on SEEG.** Bipolar improves mean SOZ recall by **+7.4 pp** per subject
(27.2% → 34.6%, Wilcoxon p = 0.0066, better on 24/37 subjects). On ECoG grids it makes no
reliable difference, which is expected — see the caveat below.

## Setup

- 213 ictal runs, 57 subjects, OpenNeuro `ds004100` (`/media/data/eeg/ds004100`).
- Identical windows both arms: baseline `t_onset-60 → t_onset-10`, target `t_onset → t_onset+15`.
- `ei_method=band_ratio`, Bartolomei bands (3.5–12.4 / 12.4–97 Hz), prefilter 1–500 Hz.
- Only the reference differs.
- Both arms evaluated all 213 runs, so the comparison is fully paired — no run counted
  for one arm and not the other.
- Bipolar scores name pairs; ground truth names contacts. Each pair's score is projected
  onto both member contacts by max (`montage.project_pairs_to_contacts`).
- Top-K is K = number of ground-truth SOZ contacts for that run.

Reproduce:

```bash
cd v2/server
.venv/bin/python ../tools/verify_ds004100_full.py --mode ei --reference car \
  --output-csv ../verification_results/ds004100_ei_car.csv \
  --output-html ../verification_results/ds004100_ei_car.html
.venv/bin/python ../tools/verify_ds004100_full.py --mode ei --reference bipolar \
  --output-csv ../verification_results/ds004100_ei_bipolar.csv \
  --output-html ../verification_results/ds004100_ei_bipolar.html
.venv/bin/python ../tools/compare_ei_reference.py \
  ../verification_results/ds004100_ei_car.csv ../verification_results/ds004100_ei_bipolar.csv \
  --label-a CAR --label-b BIPOLAR
```

Full output: `v2/verification_results/ds004100_reference_comparison.txt` (gitignored).

## Results

Per-subject means (the headline: runs within a patient share an implantation and a focus,
so 213 runs are not 213 independent samples). p from Wilcoxon signed-rank on subject means.

### SEEG — 143 runs, 37 subjects

| metric | CAR | bipolar | delta | p |
|---|---:|---:|---:|---:|
| mean SOZ recall | 27.23% | **34.59%** | +7.36 pp | 0.0066 |
| mean resection concordance | 26.50% | **31.32%** | +4.82 pp | 0.0290 |

Run-level: SOZ top-K hit rate 67.13% → **74.13%** (+7.0 pp); bipolar better on 61 runs,
worse on 29.

### ECoG — 70 runs, 20 subjects

| metric | CAR | bipolar | delta | p |
|---|---:|---:|---:|---:|
| mean SOZ recall | 25.41% | 26.27% | +0.86 pp | 0.95 |
| mean resection concordance | 30.08% | 33.99% | +3.91 pp | 0.31 |

Run-level SOZ top-K hit rate *drops*, 77.14% → 68.57%.

**Why ECoG is excluded from the conclusion**: `montage.bipolar_pairs` pairs adjacent
contact numbers along a shaft, which is the right model for a depth electrode and the
wrong one for a grid — on an 8×8 grid, contacts 8 and 9 are on opposite edges of adjacent
rows, so a fraction of the pairs difference two non-neighbouring sites. The null result
here is evidence about that model, not about bipolar referencing on ECoG. Doing this
properly needs grid geometry, which ds004100's channel tables don't carry.

## Notes

- The CAR arm reproduces the previously reported 26.5% mean SOZ recall from
  `ezei_comparison_ds004100.md` exactly, so the refactor that added `--reference` did not
  disturb existing behaviour.
- **This puts a caveat on `ezei_comparison_ds004100.md`.** That benchmark scored our CAR
  pipeline against EZEI and reported a +9.6 pp SOZ hit-rate win. Bipolar alone moves our
  SEEG numbers by a comparable amount, so if EZEI references differently, part of that
  reported gap is a montage difference rather than a method difference. What EZEI does
  internally has not been checked.
- ds004100 label conventions are heterogeneous (`LAF1`, `EEG RG 01-Ref`, `AMFG-A2`,
  `RAF1-3`). `montage.parse_contact` handles all of them by treating trailing digits as
  the contact number and everything before as the shaft. A first version that only
  handled `LAF1` silently failed on ~40% of runs — which would have compared bipolar on a
  subset against CAR on everything.
- ~71 channel labels in the dataset (`RAF1-3` style) may already be bipolar derivations,
  in which case differencing them again is a double difference. Small enough to ignore
  here; no attempt was made to detect it.

## The Bella case, both ways

Run over all 8 `SZ nP` seizures (`v2/tools/compare_ei_reference_subject.py`), 184 contacts,
aggregated by mean rank-percentile across seizures rather than read off any single one.
Windows identical in both arms: baseline 55–105 s, target 115–135 s (the `SZ nP` mark sits
at t=120 in every clip). No seizure tripped the degenerate-window diagnostic.

```bash
cd v2/server
.venv/bin/python ../tools/compare_ei_reference_subject.py ../../data/Bella/BellaEDF \
    --soz A,I --spared-of-interest D -o ../../data/ei_reference/bella_ei_reference.csv
```

**Shaft level — both references agree, and both agree with the clinicians.**

| rank | CAR | | bipolar | |
|---|---|---:|---|---:|
| 1 | **A** (clinical SOZ) | 0.700 | **I** (clinical SOZ) | 0.702 |
| 2 | **I** (clinical SOZ) | 0.673 | **A** (clinical SOZ) | 0.687 |
| 3 | B | 0.629 | S | 0.677 |
| … | | | | |
| | D (most fragile, spared) | 0.485 | D (most fragile, spared) | 0.507 |

**Contact level within shaft A — the references disagree sharply.**

| | A1 | A2 | A3 | A4 | A5 | A6 | A7 | A8 | A9 | A10 |
|---|---|---|---|---|---|---|---|---|---|---|
| CAR | 0.79 | 0.69 | 0.75 | 0.78 | 0.66 | 0.63 | 0.69 | 0.64 | 0.66 | 0.72 |
| bipolar | 0.69 | 0.69 | 0.68 | 0.72 | 0.69 | 0.52 | 0.68 | **0.78** | **0.78** | 0.64 |

CAR puts the shaft's weight on the mesial contacts A1/A3/A4 and ranks A8/A9 *below* the
shaft average. Bipolar peaks exactly on **A8/A9**.

That matters because A8/A9 is what the independent manual analysis in
[bella_ictal_ei_vs_annotation_discrepancy.md](bella_ictal_ei_vs_annotation_discrepancy.md)
had already singled out — measured on hand-built bipolar pairs, 25–100 Hz power, with no
EI involved: *"A8 and A9 are the two most active contacts on the shaft during the seizure"*,
and A6/A7/A8 fire zero sharp transients before the LVFA. Two independent routes to the
same two contacts; CAR EI disagrees with both. This is the volume-conduction argument
showing up as a concrete contact-level disagreement, on the case the project exists for.

Supporting, not conclusive: CAR crossed the onset threshold on more channels than bipolar
in 6 of 8 seizures (mean 44.4% of channels vs 37.9%), which is the direction the
CAR-smearing mechanism predicts.

### What this does *not* say

- **Agreement with the clinical SOZ is not validation here.** A and I were the annotated
  onset shafts, they were resected, and the surgery failed. An algorithm that reproduces
  the annotation reproduces the reasoning that led to a failed resection.
- **EI does not corroborate the fragility result.** D — ezfragility's most fragile shaft,
  2 cm outside the cavity — sits mid-pack under both references (0.485 / 0.507). EI and
  fragility disagree about this patient. That disagreement is the interesting finding and
  is not resolved by the montage.
- One patient, outcome known in advance.

## Shipped in the app (2026-08-16)

`reference` is now selectable per EI job, defaulting to **bipolar** for new jobs. A job
whose `params_json` predates the field replays as CAR, so retrying an old job reproduces
its original result rather than silently switching method.

The npz stores both levels: `chn_names`/`ei` hold the analysed channels (derivations under
bipolar — that is what was measured), and `contact_names`/`ei_by_contact` hold the
projection onto contacts. `services/soz.py::load_ei_result` prefers the projection, which
is what keeps `fuse_ei_hfo_scores` and the 3D view joining on contact names unchanged.
Without it a bipolar run would have produced `A1-A2` names, matched nothing, and silently
degraded to an HFO-only fusion — pinned by
`tests/test_soz_matching.py::test_bipolar_ei_archive_loads_keyed_by_contact`.

The EDF window endpoint takes `reference` too, so the result panel's per-channel
drill-down can show the actual bipolar derivation — which is also the spectrogram you need
for Brainstorm-style band selection.

## Still open

- **Native pair support downstream.** `fusion.py`, `soz.py` and the 3D viewer see only the
  contact projection; they cannot show that `A8-A9` was the hot derivation. Fine for
  ranking, a real limitation for interpretation.
- **ECoG grids.** Adjacent-number pairing is wrong for a 2-D grid; doing it properly needs
  grid geometry the channel tables don't carry.
- **EZEI's referencing** is still unchecked, so the caveat on
  `ezei_comparison_ds004100.md` stands.
- **The EI/fragility disagreement about Bella** (D mid-pack under both references) is
  untouched by any of this, and is the more interesting question.
