# Next session plan — picking up after 2026-08-09

Written to be self-contained, since the session it came from ends here and the
work continues on another machine.

## Machine-local things that will NOT travel

- `datasets/Bella/*.edf` — the 63 converted clips. Gitignored, as they must be.
- `C:\dev\frag\SZ{1..8}P.csv` + `_ch.txt` — the fragility exports. These are
  *regenerable*: that is what `v2/tools/fragility/export_edf.py` is for, and it
  reproduces them bit-for-bit. See the manifest recipe below.
- `C:\dev\frag\frag_scores.rds` — already lost, and see the bug note below for
  why the ranking it held should not be trusted anyway.



## 1. Re-run fragility with the fixed aggregation

`v2/tools/fragility/` is committed (`abe43ea`). To regenerate everything:

```bash
# manifest: label,edf_path,onset   (onset may be @<annotation regex>)
SZ1P,<...>/DA6465AU_17_20240319072231.edf,@^SZ 1P$
SZ2P,<...>/DA6465AU_20_20240319173404.edf,@^SZ 2P$
SZ3P,<...>/DA6465AU_21_20240319221721.edf,@^SZ 3P$
SZ4P,<...>/DA6465AU_22_20240320060204.edf,@^SZ 4P$
SZ5P,<...>/DA6465AU_26_20240321033634.edf,@^SZ 5P$
SZ6P,<...>/DA6465AU_27_20240321070053.edf,@^SZ 6P$
SZ7P,<...>/DA6465AU_29_20240321125835.edf,@^SZ 7P$
SZ8P,<...>/DA6465AU_44_20240322221053.edf,@^SZ 8P$

python v2/tools/fragility/export_edf.py --manifest seizures.csv -o <dir>
Rscript v2/tools/fragility/run_frag.R <dir> --onset-shafts=A,I --spread-shafts=N,P,G,L,K,Q,S
```

All eight seizures are marked at t = 120 s in their own clip.

**Discard any shaft ranking produced before this fix.** The original script used
`sub("...", "\1", x)`; R reads `"\1"` as the octal escape for character 1, not a
regex backreference (that needs `"\\1"`), so all 184 contacts collapsed into one
bucket and `ord[1:12]` indexed past the end of a length-1 vector. The per-seizure
`top 10:` lines were never affected.

When reading the output, check the **median R²** the script prints per seizure
first: a 250-sample window fits a 184×184 transition matrix from 249
transitions, which is barely overdetermined, and a low R² means the ranking
describes noise.

Decision recorded 2026-08-09: fragility **stays a CLI tool**. Promoting it to a
`fragility_compute` job + web tab would put R + EZFragility + Epoch into
`v2/docker/base.Dockerfile`, and that is not worth it until the results have
been validated against EI/HFO. Do not reimplement it in Python — being an
independent implementation is the entire point.

## 3. Write up the interictal file selection

Not yet in `docs/`. The findings below are the whole of it.

### Which file, and why

**Use `datasets/Bella/DA6465AU_12_20240317131709.edf`** (271 s, 17 Mar 13:17,
~42 h before the first seizure). Replication file:
`DA6465AU_07_20240316140404.edf` (151 s).

Only clips 00–16 (15–18 Mar) precede the first seizure — clip 17 is marked
`SZ 1P`, and everything after is cluster or post-ictal. Screening those:

| clip | start | dur | sat% | δ rel | 60 Hz | ripple noise med/max µV | verdict |
|---|---|---|---|---|---|---|---|
| 01 | 15/03 15:33 | 271 s | 0.01 | 0.81 | 22.9× | 0.33 / 22.0 | reject |
| 04 | 16/03 02:35 | 151 s | 0.04 | 0.88 | 44.2× | 0.26 / 9.4 | reject |
| 07 | 16/03 14:04 | 151 s | 0.00 | 0.81 | 2.7× | 0.19 / 0.81 | good |
| **12** | **17/03 13:17** | **271 s** | **0.00** | **0.74** | **2.6×** | **0.18 / 0.83** | **best** |
| 14 | 18/03 19:04 | 151 s | 0.13 | 0.62 | 2.2× | 0.32 / 4.3 | reject |
| 16 | 18/03 23:42 | 61 s | 0.00 | 0.90 | 2.5× | 0.16 / 0.80 | cleanest, too short |

Mains interference collapses after 16/03 ~14:00 — before that the 120/180/240 Hz
harmonics sit inside the ripple band and clip 04 yields a diffuse, meaningless
ranking (median 4.8 events/min spread over I2, P10, G9, B7, F9).

### HFO settings

Band 80–250 Hz · **mains 60** (the form defaults to 50; this is a Cleveland
Clinic recording) · rel 2.5 · abs 2.5 · min gap 10 ms · min duration 20 ms ·
whole recording. Sampling is 1 kHz, so fast ripples (250–500 Hz) are out of
reach — do not widen the band.

### The result worth writing up

Top channels **I6, A1, A2**, stable across thresholds 2.0–4.0, reproduced
independently on clip 07. That matches the annotations (`SPK IAB`,
`rep SPKing amygdala`, `A LVFA -> broad`, `EEG onset - IA fast`) and matches the
`GT_ON <- c("A","I")` the fragility script already encoded. Three methods, one
answer — this is exactly the convergence `docs/project-direction.md` is after.

### Better ictal clips than clip 17

Clip 17 rails on every channel from t ≈ 240 s and is a ~30-seizure cluster. The
`SZ nP` clips are each cut with the seizure at t = 120 s, giving ~110 s of
built-in baseline:

- **clip 29** — `EEG onset - IA fast` at 118 s; names I and A explicitly, so it
  directly tests the HFO result. Baseline saturation 0.04%.
- **clip 44** — `preictal spiking A` 111, `SZ 8P` 120, `clinical onset` 123,
  `end` 184. Clean baseline.
- clip 27 also clean. Avoid clip 20 (**25% saturation during the seizure**),
  clip 21 (58× line noise), clip 26 (clips in its own baseline).

Suggested EI settings for clip 29: baseline 20–105, target 112–205, band 1–300,
mains 60. Keep the baseline off the preictal spiking marks — the
`baseline_max + 20σ` threshold in `ictal.py` means one spike in the baseline can
put a channel out of reach for the whole seizure, still the leading suspect for
`docs/bella_ictal_ei_vs_annotation_discrepancy.md`.

## Repo state

Committed and pushed as of 2026-08-09: `38524f7` (aux-channel auto-exclusion,
server + web + 2 tests) and `abe43ea` (the fragility tool and soz_analysis).

`v2/server` tests: 110 pass. `test_artifact_download_and_subject_zip` fails on
Windows with `application/x-zip-compressed` vs `application/zip` — pre-existing,
verified independent of those changes, and worth either fixing or marking.