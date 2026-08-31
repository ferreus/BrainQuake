# Neural fragility vs the resection: a retrospective test on Bella

**Date:** 2026-08-13; numbers re-run 2026-08-28 on the corrected nk2edf export
(`datasets/BellaNew`), which moved every `SZ nP` mark off its whole-second value
by up to 0.94 s. Conclusions unchanged. Answers the open question left in
[project-direction.md](project-direction.md): *where is shaft D anatomically, and
was it inside the resection?*

**Amended 2026-09-01**, after the clinical reports arrived on disk. Two things
weakened the case for D and one strengthened the anatomy it rests on; all three
are below. The load-bearing result — the interpretability ratio — is untouched.

## The question

Li et al. 2021 ([PMC8547387](https://pmc.ncbi.nlm.nih.gov/articles/PMC8547387/),
*Nat Neurosci* 24:1465) claim neural fragility predicts surgical outcome: it
flagged 43 of 47 surgical failures at 76% accuracy, AUC 0.88. Their mechanism is
a contrast — in **successful** resections the clinically annotated SOZ is the
fragile set (Cohen's D 1.507); in **failures** SOZ and non-SOZ are
indistinguishable (p = 0.355).

Bella's outcome is known: a right temporal lobectomy that did not stop her
seizures. So this is a **retrodiction on one patient**, not a validation of the
method — but it is falsifiable, which the shaft ranking alone was not.

## What could and could not be reproduced

Their trained classifier is **not public**. Code availability points only to a
Gigantum notebook for reproducing figures, and Gigantum is defunct; the random
forest fitted on 91 patients was never released. So there is no "probability of
success = 0.2" to quote.

What *is* exactly reproducible is the classifier's input and the paper's own
interpretability statistic, both computed by `EZFragility::fragStat`:

- the 10 SOZ + 10 SOZ-complement fragility quantiles per time window,
- the interpretability ratio `I = F_SOZ(90th) / F_SOZC(90th)`.

The paper's permutation analysis reports the AUC is driven primarily by the 90th
quantile and above, so this is the load-bearing part of the feature vector.

## Inputs

8 `SZ nP` seizures, 184 contacts, 1 kHz, common-average referenced, window
[-20, +10] s around the `SZ nP` mark. Fragility per Li et al.: 250 ms window,
125 ms step. **R² median 0.816–0.998 across the 8** — the linear models the
method rests on fit well, so the ranking is describing dynamics, not noise.

**Four** seizures carry an explicit electrographic onset annotation: SZ 1P
(−3.0 s), SZ 2P (+0.9 s), SZ 3P (`EEG onset JB`, −0.2 s) and SZ 7P
(`EEG onset - IA fast`, −2.2 s), all relative to their `SZ nP` mark. An earlier
version of this document said only 1P and 2P did; that was
`seizure_timing.py`'s anchored `^EEG onset$` regex refusing labels that carry a
reviewer's initials or a localising note, not a property of the recording. The
`SZ nP` mark is still used as a uniform t = 0 and the statistic is reported
across five windows; the conclusion does not depend on the choice.

## Result 1 — the outcome statistic

```
SZ1P: 184 electrodes x 239 windows, R2 median 0.941, frag range [0.000, 0.992], 3.1 min
SZ2P: 184 electrodes x 239 windows, R2 median 0.995, frag range [0.000, 0.999], 3.2 min
SZ3P: 184 electrodes x 239 windows, R2 median 0.884, frag range [0.000, 0.994], 2.9 min
SZ4P: 184 electrodes x 239 windows, R2 median 0.816, frag range [0.000, 0.993], 2.8 min
SZ5P: 184 electrodes x 239 windows, R2 median 0.842, frag range [0.000, 0.995], 2.8 min
SZ6P: 184 electrodes x 239 windows, R2 median 0.998, frag range [0.000, 0.998], 3.5 min
SZ7P: 184 electrodes x 239 windows, R2 median 0.998, frag range [0.000, 0.995], 3.5 min
SZ8P: 184 electrodes x 239 windows, R2 median 0.858, frag range [0.000, 0.993], 3.0 min
done -> data/fragility/bellanew/frag_full
```

SOZ = the clinically annotated onset shafts **A and I** (16 contacts). Paper
window, −10 s to the first 5% of each seizure:

| | interpretability ratio | Cohen's d | SOZ percentile |
|---|---|---|---|
| Paper, successful resections | > 1 | **+1.51** | high |
| Paper, failed resections | ~1 | n.s. (p = 0.355) | — |
| **Bella (8 seizures)** | **0.987** | **+0.37** | **59.0** |

The clinical SOZ sits at the 59th percentile of her own implant — indistinguishable
from an arbitrary electrode. Per-seizure the sign flips (d from −0.43 to +0.87,
most p > 0.05), so even the weak positive mean is not consistent.

**This is the paper's surgical-failure signature, and the surgery did fail.**

Shaft ranking, mean fragility over the paper window (20 shafts):

```
D  0.6110     P  0.5432     S  0.5354     F  0.5244     M  0.5125
I  0.5113  <<< clinical SOZ, 6th
...
A  0.5069  <<< clinical SOZ, 9th
```

## Result 2 — where those shafts are

Contacts from `datasets/Bella Seeg.mrb` via the server's own `parse_mrb` (LPS,
inverse transform, 93% in brain), labelled against `aparc+aseg.mgz`. `y` is
anterior(+)/posterior(−) in tkreg RAS.

**That labelling is now validated against the SEEG report** — 66% of the
contacts the clinicians name are inside or within 2 mm of the structure they
name, against a 7% base rate, with `I1-3 = temporal pole` exact. See
[bella_anatomy_validation.md](bella_anatomy_validation.md), which also records
the two shafts (G and K) where the pipeline and the clinicians disagree by a
whole gyrus. D is not one of them, and is the easy case: five of six contacts in
one large parcel.

| shaft | y | x | dominant labels |
|---|---|---|---|
| **I** (clinical SOZ) | +18.6 | 29.5 | rh-temporalpole, rh-middletemporal |
| **A** (clinical SOZ) | −0.7 | 35.2 | rh-middletemporal, **Right-Amygdala, Right-Hippocampus** |
| **D** (most fragile) | **−21.5** | **56.3** | **rh-superiortemporal (5/6)** |

D is right posterior superior temporal gyrus, ~21 mm behind A and ~40 mm behind
I, and the most lateral shaft in the implant.

## Result 3 — what was actually resected

Post-op T1 (`5_sag_t1_mprage_iso`, from the DICOM CD) rigidly registered to the
pre-op T1 with SimpleITK (Mattes MI; 9 mm translation, ~8° rotation). Cavity =
pre-op parenchyma that is CSF-dark post-op, largest connected component.

Keying the cavity on pre-op *tissue* labels rather than the brainmask matters: a
brainmask-based mask leaks through the ventricles and subarachnoid space into a
41.7 mL blob spanning both hemispheres. The tissue-based mask gives a single
**17.3 mL** component, centroid RAS (33.3, 2.5, −18.0), extent y ∈ [−31, +25] —
a right anterior temporal lobectomy, as expected.

| shaft | | distance to cavity | verdict |
|---|---|---|---|
| **I** | clinical SOZ | 4/6 contacts inside, median 0.0 mm | **resected** |
| **A** | clinical SOZ | 2/10 inside, median 1.4 mm (all within 1–7 mm) | **resected / at margin** |
| B, T | — | median 3.3 / 4.7 mm | spared, adjacent |
| F | — | median 11.4 mm | spared |
| **D** | **most fragile** | **min 16.9 mm, median 19.5 mm** | **spared** |

Verified visually in `data/fragility/resection/qc.png`: pre-op and post-op align
on skull, ventricles and cerebellum, and D1–D6 sit lateral and posterior to a
cavity whose nearest voxels are anterior and inferior to them.

## Reading

The resection removed the clinically annotated SOZ — I entirely, A to its margin
— and left the highest-mean-fragility shaft 2 cm outside the cavity. That
*looks* like the failure mode the fragility method was built to detect: *a
resection that missed the fragile region*. Read the base-rate objection below
before believing it — 90% of the shafts are outside the cavity, so D's position
is nearly what chance predicts; D's rank flips to A below a top-20 vote cutoff;
and the SEEG report never mentions D. The load-bearing result is the
interpretability ratio, not D.

Independent support: the fragile set is broadly posterior (D −21.5, F −31.5,
S −18.3, P −53.3) while the resection was anterior temporal. If that holds up,
the implication is not "one missed shaft" but a posterior temporal network an
anterior resection could not reach — the well-described "temporal plus" pattern
behind failed temporal lobectomies.

## The base-rate objection to Result 2+3

**"D is outside the cavity" is close to uninformative on its own.** Only I and A
have contacts inside the 17.3 mL cavity; the other 18 shafts do not. A method
that must rank something first therefore lands outside the resection with
probability ≈ 18/20 = **90% under the null**. The geometry adds almost nothing,
and D could simply be a false positive.

What this does *not* touch is the interpretability ratio, which never mentions
D: it asks only whether the clinically annotated SOZ separates from its
complement, and the answer (0.987, d +0.37, 59th percentile) is the paper's
failure signature either way.

Points for D being a false positive:

- the mean-fragility spread is narrow — D leads P by 12%, and P (precuneus) and
  S (postcentral) are not plausibly epileptogenic here, so D sits in the same band;
- in the Python run `X1` enters the top 10 of 6 of 8 seizures, which reads more
  like a channel property than a generator;
- SZ 7P's own annotation is `EEG onset - IA fast` — the reviewers named **I and
  A**, not D;
- **D's first place is an artifact of the vote cutoff** (below);
- **D appears nowhere in the SEEG report** (below).

### D's rank depends entirely on the vote cutoff

Size-normalised top-N votes per channel, summed over the 8 seizures, from
`data/fragility_bellanew.csv`:

| cutoff | 1st | 2nd | 3rd | 4th |
|---|---|---|---|---|
| top 5 | **A 1.10** | B 0.62 | X 0.58 | D 0.50 |
| top 10 | **A 1.80** | I / B / D 1.00 | | |
| top 20 | **D 2.67** | A 2.10 | F 1.80 | I 1.67 |
| top 30 | **D 4.33** | B 3.12 | A 2.80 | F 2.40 |

**At the two most selective cutoffs the clinical SOZ shaft A wins outright, and
D is fourth.** D only takes first place at top-20 and above — and
`v2/tools/compare_fragility_r.py:32` hardcodes `TOP_N = 20`, the tightest cutoff
at which D leads. The `next_steps.md` claim that "shaft D holds rank 1 in every
configuration" is false for this family of configurations.

EI does the same thing on the same contacts (`data/ei_bella_bellanew.csv`): I
first at top-5, A first at top-10, D first at top-20, B first at top-30. Two
methods agreeing that D wins at top-20 is not two independent votes for D; it is
two methods sharing a cutoff sensitivity.

This does not make D noise — it is above chance (0.87 votes/ch at top-20) at
every cutoff. It makes "**the** most fragile shaft" an overstatement of what a
narrow, cutoff-dependent lead supports.

### D appears nowhere in the SEEG report

The Cleveland SEEG evaluation report (`datasets/BellaNew/Readme.md`) names
electrodes by contact range ~30 times. Mention counts:

```
A 16    I 14    G 5    P 5    L 4    N 4    Q 4    B 3
K 2     M 2     S 2    X 2    D 0    F 0    T 0
```

**A and I are named 30 times between them; D, F and T zero.** This is not a
labelling disagreement — the report describes activity, so silence means those
shafts did nothing the clinicians thought worth writing down, across 11 days of
monitoring and 13 seizures. Before this report the "points against" list below
had nothing from SEEG; it now has a clear negative from SEEG.

Points against, all from outside SEEG:

- the 2022 Ichilov scalp study (age 2) concludes **"4 seizures from a right
  parietal source"** and *"epilepsy from a right parietal source"* — all four
  seizures, onsets marked `P4P8O2`, `P4T8`, `P4P8O2`. An earlier version of this
  document cited it as "marks one seizure `p8 onset`", which undersold it;
- the **Cleveland February 2024 pre-surgical phase-I** study — physician-signed,
  ictal, one month before implantation — reads `EEG Seizure, Regional, **Right
  parietal**`, with interictal `Intermittent Rhythmic Slow, Regional, Right
  posterior`. Two centres, 18 months apart, independently localising right
  posterior/parietal from scalp;
- the **post**-resection May 2024 scalp study logs `IS R POST TEMP` twice and
  `IS R P`. Different modality, years apart, no shared processing — but three
  technologist annotations in a study with no recorded seizure, and "posterior
  temporal" from scalp is a large region.

The first two are stronger than this document previously credited: they are
pre-surgical, independent of each other, and neither had ESI. But note what they
do *not* say — neither names the temporal pole/amygdala focus the SEEG later
confirmed, so they are evidence that scalp localisation was pointing somewhere
posterior, not evidence that D specifically is a generator.

**Open test, cheap once `contact_anatomy.csv` exists:** permute the shaft
ranking and ask how often the top shaft sits ≥17 mm from the cavity. Until that
number exists, "the resection missed the fragile region" is a hypothesis, not a
result.

## What this does not establish

- **One patient, outcome known in advance.** Consistent with the paper's claim;
  cannot confirm it.
- **No trained classifier**, so the comparison is against the paper's published
  success/failure distributions, not its model. Weaker than running it.
- **Cohort mismatch.** Their patients averaged ~160 electrodes and were mostly
  older; Bella was 4. Fragility is unvalidated at that age.
- **Three seizures have a near-saturated map.** In SZ2P/6P/7P both SOZ and SOZC
  90th percentiles sit at 0.82–0.88, so those maps barely discriminate anything
  whatever set is chosen. The contrast lives in SZ1P/3P/4P/5P/8P.
- **SZ 2P is amplifier-clipped across most of the implant.**
  `ei.find_saturated_channels` flags **152 of 184 contacts**, i.e. the recording
  itself is pinned at the rail, not just the fragility map. Fragility and EI over
  clipped signal describe the amplifier. SZ 2P should probably be excluded
  outright; it is retained here only so the 8-seizure set matches the earlier
  analysis.
- **D's lead is neither consistent nor large.** An earlier version of this
  bullet said top-N voting and mean fragility both rank D first, "which is real
  convergence". That is wrong: top-N voting ranks **A** first at cutoffs of 5
  and 10, and only puts D first at 20 and above (see the cutoff table above).
  Mean fragility does rank D first, but the spread is narrow (0.61 → 0.42) — D
  leads P by ~12%, not the ~2.2× voting at top-20 suggests.
- **S (postcentral) and P (precuneus) ranking high is not obviously
  epileptogenic** and may reflect electrode-specific signal properties.
- **Brain shift.** Rigid registration ignores post-resection collapse, so
  distances near the cavity margin are approximate. D at ~19 mm is well clear of
  that uncertainty; A's "at margin" call is not.

## Reproducing

```bash
python v2/tools/fragility/seizure_timing.py datasets/BellaNew -o data/fragility/timing.csv
python v2/tools/fragility/export_edf.py --manifest data/fragility/bellanew_seizures.csv -o data/fragility/bellanew
Rscript v2/tools/fragility/frag_compute.R data/fragility/bellanew
Rscript v2/tools/fragility/frag_outcome.R data/fragility/bellanew --soz=A,I

# Python port vs EZFragility on the same windows; exits nonzero on disagreement.
python v2/tools/verify_fragility_bella.py --ref data/fragility/bellanew/ezfragility_shafts.txt

python v2/tools/fragility/contact_anatomy.py "datasets/Bella Seeg.mrb" -o data/fragility/contact_anatomy.csv

# Does the anatomy agree with the clinicians? (bella_anatomy_validation.md)
python v2/tools/fragility/anatomy_vs_report.py "datasets/Bella Seeg.mrb" \
    --truth datasets/BellaNew/report_anatomy.csv --sweep \
    -o data/fragility/anatomy_vs_report.csv

python v2/tools/fragility/resection_overlap.py --postop data/fragility/postop/5_sag_t1_mprage_iso.nii.gz \
    --contacts data/fragility/contact_anatomy.csv -o data/fragility/resection
python v2/tools/fragility/cavity_analysis.py --reg data/fragility/resection/postop_in_preop.nii.gz \
    --contacts data/fragility/contact_anatomy.csv -o data/fragility/resection
python v2/tools/fragility/cavity_qc.py --reg data/fragility/resection/postop_in_preop.nii.gz \
    --cavity data/fragility/resection/cavity_mask.nii.gz \
    --contacts data/fragility/contact_anatomy.csv --focus D -o data/fragility/resection/qc.png
```

Post-op DICOM → NIfTI via `dcm2niix` (MRIcroGL). All outputs land under
`data/fragility/`, which is gitignored — they contain patient imaging.
