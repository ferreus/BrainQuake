# Neural fragility vs the resection: a retrospective test on Bella

**Date:** 2026-08-13. Answers the open question left in
[project-direction.md](project-direction.md): *where is shaft D anatomically, and
was it inside the resection?*

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
125 ms step. **R² median 0.841–0.998 across the 8** — the linear models the
method rests on fit well, so the ranking is describing dynamics, not noise.

Only SZ 1P and 2P carry an explicit `EEG onset` annotation, and in clip 17 that
mark sits 3 s *before* the `SZ nP` mark. The `SZ nP` mark is therefore used as a
uniform t = 0 and the statistic is reported across five windows; the conclusion
does not depend on the choice.

## Result 1 — the outcome statistic

SOZ = the clinically annotated onset shafts **A and I** (16 contacts). Paper
window, −10 s to the first 5% of each seizure:

| | interpretability ratio | Cohen's d | SOZ percentile |
|---|---|---|---|
| Paper, successful resections | > 1 | **+1.51** | high |
| Paper, failed resections | ~1 | n.s. (p = 0.355) | — |
| **Bella (8 seizures)** | **0.986** | **+0.24** | **55.5** |

The clinical SOZ sits at the 55th percentile of her own implant — indistinguishable
from an arbitrary electrode. Per-seizure the sign flips (d from −0.42 to +0.67,
most p > 0.05), so even the weak positive mean is not consistent.

**This is the paper's surgical-failure signature, and the surgery did fail.**

Shaft ranking, mean fragility over the paper window (20 shafts):

```
D  0.6503     S  0.5877     P  0.5739     F  0.5692     M  0.5661
I  0.5611  <<< clinical SOZ, 6th
...
A  0.5295  <<< clinical SOZ, 10th
```

## Result 2 — where those shafts are

Contacts from `datasets/Bella Seeg.mrb` via the server's own `parse_mrb` (LPS,
inverse transform, 93% in brain), labelled against `aparc+aseg.mgz`. `y` is
anterior(+)/posterior(−) in tkreg RAS.

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
— and left the most fragile shaft 2 cm outside the cavity. That is precisely the
failure mode the fragility method was built to detect: *a resection that missed
the fragile region*.

Independent support: the fragile set is broadly posterior (D −21.5, F −31.5,
S −18.3, P −53.3) while the resection was anterior temporal. If that holds up,
the implication is not "one missed shaft" but a posterior temporal network an
anterior resection could not reach — the well-described "temporal plus" pattern
behind failed temporal lobectomies.

## What this does not establish

- **One patient, outcome known in advance.** Consistent with the paper's claim;
  cannot confirm it.
- **No trained classifier**, so the comparison is against the paper's published
  success/failure distributions, not its model. Weaker than running it.
- **Cohort mismatch.** Their patients averaged ~160 electrodes and were mostly
  older; Bella was 4. Fragility is unvalidated at that age.
- **Half the seizures have a near-saturated map.** In SZ2P/4P/6P/7P both SOZ and
  SOZC 90th percentiles sit at 0.85–0.89, so those maps barely discriminate
  anything whatever set is chosen. The contrast lives in SZ1P/3P/5P/8P.
- **D's lead is consistent but modest.** Top-N voting and mean fragility both
  rank D first, which is real convergence, but the mean-fragility spread is
  narrow (0.65 → 0.46): D leads S by ~10%, not the ~2.5× voting suggested.
- **S (postcentral) and P (precuneus) ranking high is not obviously
  epileptogenic** and may reflect electrode-specific signal properties.
- **Brain shift.** Rigid registration ignores post-resection collapse, so
  distances near the cavity margin are approximate. D at ~19 mm is well clear of
  that uncertainty; A's "at margin" call is not.

## Reproducing

```bash
python v2/tools/fragility/seizure_timing.py datasets/Bella -o data/fragility/timing.csv
python v2/tools/fragility/export_edf.py --manifest data/fragility/seizures.csv -o data/fragility/export
Rscript v2/tools/fragility/frag_compute.R data/fragility/export --parallel
Rscript v2/tools/fragility/frag_outcome.R data/fragility/export --soz=A,I

python v2/tools/fragility/contact_anatomy.py "datasets/Bella Seeg.mrb" -o data/fragility/contact_anatomy.csv
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
