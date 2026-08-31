# Does `parse_mrb → aparc+aseg` agree with the clinicians?

**Date:** 2026-09-01. Answers validation stage 2 of
[project-direction.md](project-direction.md) — *"cross-check shafts against the
clinical implantation schema"* — which had been open since that roadmap was
written.

## Why this was the highest-value thing left

Every anatomical claim in this repo is derived from one computation:
`parse_mrb` reads the 3D Slicer scene, applies an ITK affine in LPS, flips to
RAS, converts to tkreg RAS, and samples `aparc+aseg.mgz` at each contact. That
chain has eleven tests (`test_api.py:786-1178`), all against *synthetic*
volumes and transforms. They prove the arithmetic. They cannot prove that this
subject's contacts land where the clinicians put them, because until now
nothing independent said where that was.

The Cleveland Clinic SEEG report names electrodes by anatomy ~30 times —
`A1-3 (right amygdala)`, `I1-3 (right temporal pole)`, `B1-2 (right
parahippocampal gyrus / collateral sulcus)`, plus two full spread tables. It is
the first contact-level ground truth the project has had, and it was produced
by people who never saw this pipeline.

## Method

33 claims transcribed to `datasets/BellaNew/report_anatomy.csv` (gitignored,
beside the report), each a shaft, contact range, side, and `|`-separated region
tokens sourced to its line in the report.

Each claim is scored as a **distance**, not a boolean: how far is the contact
from the nearest voxel carrying any label the clinician's phrase admits. The
phrase→label map is generic neuroanatomy and lives in the tool. Desikan-Killiany
has no sulcal parcels, so a named sulcus maps to the gyri it separates —
`collateral-sulcus` → {parahippocampal, fusiform}, `SFS` → {superiorfrontal,
rostralmiddlefrontal, caudalmiddlefrontal}.

Verdicts: **in** (the contact's own voxel carries an accepted label), **≤2 mm**,
**≤5 mm**, **miss** (nothing within 15 mm).

```bash
python v2/tools/fragility/anatomy_vs_report.py "datasets/Bella Seeg.mrb" \
    --truth datasets/BellaNew/report_anatomy.csv --sweep \
    -o data/fragility/anatomy_vs_report.csv
```

## Result: the pipeline is validated

```
hemisphere invariant: 184/184 consistent

119 claim-covered contacts: 41 inside the named structure, 38 within 2 mm,
                            18 within 5 mm, 22 miss
agreement (inside or within 2 mm): 79/119 = 66%
same-side contacts on other shafts:  237/3560 = 7%   (base rate)
```

**66% against a 7% base rate.** The base rate is the same scoring applied to
every contact on every *other* same-side shaft — what agreement looks like if
the label had nothing to do with the contact. Nearly a tenfold separation. By
distinct contact (79 of the 184 are named by the report at least once) the
figures are 65% within 2 mm, 81% within 5 mm.

### The numbering convention is right too

```
index-mapping sweep (agreement inside-or-2mm):
  identity      79/119 =  66%
  offset -1     70/104 =  67%
  offset +1     63/118 =  53%
  offset -2     54/ 87 =  62%
  reversed      50/119 =  42%
  offset +2     45/111 =  41%
```

Identity has the highest *absolute* agreement (79) and the only complete
denominator; the offsets score on fewer rows because they push indices off the
end of a shaft. This rules out the off-by-one and deep-vs-superficial reversal
that would be the natural failure of `_validate_contiguous_indices`'s
row-order-equals-contact-number invariant. Note that even `reversed` beats the
base rate 6:1 — reversing a shaft keeps its contacts on the same trajectory, so
this test discriminates less sharply than the null does.

### Where it is exactly right

The most load-bearing claims — the ones the surgical decision rested on — are
exact:

| Claim | Result |
|---|---|
| `I1-3` right temporal pole | **3/3 inside**, median 0.49 mm |
| `A1-2` right amygdala | **2/2 inside**, median 0.48 mm |
| `Q1-4` right IFG / circular sulcus | 3 inside + 1 at 0.87 mm |
| `M1-7` right SFG/SFS | 4 inside + 3 within 2 mm |
| `M'6-9` left MFG | 3 inside + 1 within 2 mm |
| `P1-3` precuneus, `P2-8` precuneus/SPL | 5 inside, median 0.66 mm |
| `N1-4` right SFG/SFS | 3 inside, median 0.43 mm |
| `S3-6` central sulcus / postcentral | all 4 within 2.11 mm |
| `B1-2` parahippocampal / collateral sulcus | both within 1.63 mm |

`I1-3 = temporal pole` landing dead-on matters most: it is the contact set the
Patient Management Conference named as the epileptogenic zone.

## The two real disagreements

Everything below is a *whole gyrus* off and cannot be explained by an index
shift, so it is worth stating plainly.

**Shaft K.** Report says `K6-10 right superior frontal gyrus`; the pipeline puts
K6-K10 in precentral / caudal middle frontal, with the nearest superiorfrontal
voxel **13.5 mm to >15 mm away**. The largest disagreement in the implant. On
this shaft the pipeline finds superiorfrontal at K3 (2.78 mm), five contacts
deeper than the report puts it. Reversing K's numbering does not fix it. The
report's other K claim, `K8-9 right SFG / premotor`, passes only through the
`premotor` token.

**Shaft G.** Report says `G1-7 right cingulate/IFG`, `G4-6 IFG`, `G6-8 IFG`; the
pipeline puts G4-G8 in rostral middle frontal, 5.2–9.0 mm from the nearest IFG
parcel. G1-G2 (`cingulate sulcus / genu cinguli` → rostral anterior cingulate)
agree exactly, so the shaft's deep end is right and its lateral end is one gyrus
too superior. Note the left twin `G'` *does* reach pars triangularis at G'8-10 —
so this is a right-G-specific finding, not a systematic frontal bias.

These two are opposite in direction (G reads superior to the report, K reads
inferior), which rules out a single rigid mis-registration producing both.

### Disagreements that are probably the pipeline being right

`A3-A5`: the report calls the whole of `A1-5` "right amygdala"; the pipeline
says A1-A2 amygdala, A3-A4 hippocampus, A5 white matter near fusiform. Naming a
shaft by its target is normal clinical shorthand. A depth electrode entering the
amygdala and continuing posteriorly *does* pass into hippocampus. The pipeline
is the more precise of the two here, not the wrong one.

Similar, smaller cases: `P8-P9` (report SPL, pipeline supramarginal — the shaft
runs off the parietal lobule onto the gyrus), `X6-X8` (report supramarginal,
pipeline inferior parietal), `I5` (report temporal pole, pipeline middle
temporal, 2.64 mm).

## Limits of this check

- **Desikan-Killiany cannot express the clinicians' vocabulary.** They write in
  sulci — superior frontal sulcus, circular sulcus, collateral sulcus, genu
  cinguli; the atlas has 34 gyral parcels per hemisphere and no sulcal labels.
  Every sulcal token is approximated by the gyri it separates, which makes those
  claims easier to satisfy than the gyral ones.
- **The metric penalises white-matter contacts.** A contact correctly placed in
  the white matter under a gyrus is several mm from that gyrus's ribbon, so it
  scores ≤5 mm rather than *in*. This depresses the headline number; it does not
  inflate it.
- **The report is not an implantation schema.** It names contacts that
  *participated in something*, not every contact's location. 79 of 184 contacts
  are covered; the remaining 105 are unvalidated.
- **One subject, one segmentation.** This validates Bella's recon, not
  `parse_mrb` in general — though it does exercise the LPS handling, the
  transform-direction heuristic, and the scanner→tkreg step on real data for the
  first time.

## What this means for shaft D

**The report never mentions D** — nor F, T, `G'` or `K'`. Its silence is an
absence of pathological activity, not a labelling disagreement, but it does mean
D's anatomy has no clinical corroboration and inherits whatever confidence this
check establishes for the pipeline as a whole.

That inheritance is favourable. D is the easy case, not the hard one: D1 and D5
are exactly `ctx-rh-superiortemporal`, D2-D4 are white matter whose nearest grey
is superior temporal at 0.90–1.06 mm, and D6 is outside the segmentation. Five
of six contacts, one large parcel, no crowding — unlike G and K, where several
small frontal parcels compete. And the
"posterior" half of *"right posterior superior temporal gyrus"* comes from
y = −21.5 in tkreg RAS, a coordinate, which does not depend on the atlas at all.

So the resection-distance argument's *anatomical* premise survives. Its
weaknesses remain the ones
[bella_fragility_resection_analysis.md](bella_fragility_resection_analysis.md)
already states: the 90% base rate for landing outside the cavity, D's narrow
lead, and the fact that no clinical source of any kind names D.

## Reproducing

```bash
python v2/tools/fragility/contact_anatomy.py "datasets/Bella Seeg.mrb" \
    -o data/fragility/contact_anatomy.csv
python v2/tools/fragility/anatomy_vs_report.py "datasets/Bella Seeg.mrb" \
    --truth datasets/BellaNew/report_anatomy.csv --sweep \
    -o data/fragility/anatomy_vs_report.csv
```

Both the truth table and the outputs live under `datasets/` and `data/`, which
are gitignored — they are derived from the patient's clinical record. The tool
and this document contain only shaft letters and region names.
