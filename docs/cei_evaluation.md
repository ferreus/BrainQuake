# cEI (Connectivity Epileptogenicity Index) — feasibility spike

**Status: evaluation in progress.** Numerics implemented and tested; Bella and ds004100
runs described below. The decision gate at the end is not yet answered.

## Why

Bartolomei's EI is an energy ratio: high-frequency band over low-frequency band. A seizure
that starts *slowly* is invisible to it by construction — the slow activity lands in the
denominator. Balatskaya et al. 2020 (Clin Neurophysiol 131:1947–1955, `docs/cei.pdf`)
report that 20–30% of SEEG seizures start this way, and propose adding a directed
connectivity term:

```
cEI(i) = ( nEI(i) + nOutDegree(i) ) / max_i( nEI(i) + nOutDegree(i) )
```

Their result on 51 patients: precision-recall AUC against the visually-defined SOZ of
0.72 vs 0.60 for slow onsets (p<0.01), 0.74 vs 0.71 for fast onsets, F1 0.74 vs 0.63.
cEI also beat EI specifically in MRI-negative cases.

This is validation-roadmap item 4 in [project-direction.md](project-direction.md)
("Algorithms, plural") and a direct line of attack on the unreconciled Bella clip-17
disagreement.

## What was implemented

`v2/server/app/sigproc/connectivity.py` and `v2/server/app/sigproc/cei.py`, numpy/scipy
only, following the paper's Methods exactly:

| parameter | value | source |
|---|---|---|
| montage | bipolar | "to avoid bias linked to a common reference in connectivity estimation" |
| connectivity band | 12–45 Hz | Methods, after Courtens et al. 2016 |
| estimator | h² non-linear regression | Wendling et al. 2001 |
| sliding window | 3 s, step 0.5 s | Methods |
| max lag | 0.1 s | Methods |
| edge threshold | h² ≥ 0.2, binarised per window | Methods |
| direction | lag of the stronger of the two directions | Methods |
| out-degree | outgoing edge count, **median** across windows | Methods |
| analysis period | 20–30 s starting 3 s before onset | Methods |

Published thresholds, recorded but not hardcoded: this paper's F1 analysis favours
cEI ∈ 0.2–0.5; Makhalova 2023 (PMC10646998, same group) used cEI ≥ 0.65 — but with r²
substituted for h², so the two numbers are not interchangeable.

Deliberate implementation choices:

- **h², not r².** Makhalova 2023 swapped in linear r² for speed. The published edge
  threshold and cEI ranges are calibrated on h², so faithfulness won. It is affordable:
  the signal is decimated to ~128 Hz first (legal — the band stops at 45 Hz), and h² is
  evaluated as `1 - ||y - AMy||²/var(y)` expanded into terms that contract over the
  10-bin axis, so nothing of size (n_samples × n_targets) is ever built.
  `test_batch_h2_matches_the_scalar_reference` pins the batched form to a plain readable
  implementation of the same estimator. Cost: ~33 s for 165 channels over a 30 s window.
- **A zero best-lag draws no edge.** The paper takes direction from the lag, and a lag of
  zero names no leader. This also disposes of most of the artifact from adjacent bipolar
  pairs sharing a contact, which couples them strongly at lag zero.
- **EI and cEI share `ei.prepare_signals`**, so both provably measure the same
  re-referenced, filtered signal. A divergence there would invalidate every comparison
  below.

Known deviation from the paper: the paper's Methods say the connectivity band is 12–45 Hz
while its own Results say "12-25 Hz". Methods (and Courtens 2016) won; the band is a
parameter.

## Scope

This is a spike, not a feature. There is no job type, router endpoint, artifact kind,
worker registration, UI, or SOZ-fusion integration — only the sigproc modules and two
offline harnesses. Plumbing is deferred to the decision gate.

## How to reproduce

```bash
# Bella: 8 'SZ nP' clips, EI vs out-degree vs cEI, plus a preictal control
v2/server/.venv/bin/python v2/tools/cei_bella.py --window paper --control \
    -o data/ei_reference/bella_cei_paper.csv

# ds004100: cEI arm, paired against the existing bipolar EI arm
cd v2/server
.venv/bin/python ../tools/verify_ds004100_full.py --mode ei --metric cei --reference bipolar \
  --output-csv ../verification_results/ds004100_cei_bipolar.csv \
  --output-html ../verification_results/ds004100_cei_bipolar.html
.venv/bin/python ../tools/compare_ei_reference.py \
  ../verification_results/ds004100_ei_bipolar.csv ../verification_results/ds004100_cei_bipolar.csv \
  --label-a EI --label-b CEI
```

The ds004100 harness keeps its existing window convention (baseline onset−60 s to
onset−10 s, target onset to onset+15 s) in **both** arms, so the comparison is properly
paired. That window carries no preictal period, which is a deviation from the paper —
cEI is being asked to work on a window chosen for EI.

## Results

_To be filled from the runs above._

## Decision

_Not yet answered._
