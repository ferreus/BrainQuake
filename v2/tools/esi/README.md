# esi — scalp-EEG analysis for Bella's case

Scalp EEG is the only **non-invasive** line of evidence in this case, and the
only one recorded independently of the SEEG implant. The design document is
[docs/plans/scalp_eeg.md](../../../docs/plans/scalp_eeg.md); the working order
and what has actually been built is
[docs/plans/scalp_eeg_impl.md](../../../docs/plans/scalp_eeg_impl.md).

**Built so far: Step 0 only.** Head modelling, source imaging, contact mapping
and the agreement statistics are not implemented and are gated behind a
head-model checkpoint (G0) that has not been run.

## Why this exists

Two questions, in the maintainer's priority order:

1. Did the pre-surgical scalp EEG already contain evidence pointing away from a
   purely anterior right-temporal target?
2. How well does a scalp-derived ranking correlate with the EI / fragility
   contact rankings from SEEG?

Step 0 answers neither on its own. It exists because the cheap analysis has to
run before the expensive one: it validates the export, the channel names, the
reference and the onset marks, and it is far more robust than source imaging.

## The data

Five studies under `datasets/ScalpEEG/`, three vendors, three years. Only 1 and
2 are pre-resection; the surgery was between 2024-03-26 and 2024-05-01.

| # | study | date | format | EEG ch | fs | mains | seizures |
|---|---|---|---|---|---|---|---|
| 1 | BellaVeegIchilov | 2022-09-21 | nicolet-nervus | 30 | 512 | 50 | **4** |
| 2 | BellaCCVEEG1 | 2024-02-06→13 | nihon-kohden | 23 | 200 | 60 | **2** |
| 3 | ..._POSTSZ_PART1 | 2024-05-01→02 | nihon-kohden | 23 | 200 | 60 | 0 |
| 4 | ..._POSTSZ_PART2 | 2024-05-02→03 | nihon-kohden | 23 | 200 | 60 | 0 |
| 5 | BellaSheebaPostSurgerVEEG | 2025-06-29 | micromed-vwr | 23 | 256 | 50 | 1 unmarked |

Each folder has a `Readme.md` holding the clinical report for that study. Read
them: they are the only independent statement of what the recordings show.

**The exports are clips, not continuous.** 2.2% of the 149 h Cleveland
admission, 18% of Ichilov. This is the hospital's export, not a converter bug —
`--dump-log` places 447/447 log events inside a clip, and the clips are
event-centred with ~30 s of padding. What is missing is unannotated background.

### Montages differ per study, so nothing may be hardcoded

| study | labels the normalizer must survive |
|---|---|
| Ichilov | 10-10 with the full inferior chain `F9/T9/P9`, `F10/T10/P10`, plus `A1 A2 Oz` |
| Cleveland | all-caps `FZ FP1`, `TP9/TP10 FT9/FT10`, no inferior chain; aux `NR1 NR2 E EKG1/2`, `EEG Mark1/2`, `Events/Markers` |
| Sheba | legacy `T3 T4 T5 T6`, `T1 T2`, every label suffixed `-G2`; aux `EMG*/PNG*/ECG*/thor/abdo/xyz/MKR`; `elA24` unmapped |

`app.sigproc.scalp_montage` resolves these per file. `T3/T4/T5/T6` canonicalise
to `T7/T8/P7/P8` (verified co-located, 0.000 mm, in `standard_1005`), and
`T1/T2` map to `FT9/FT10` — the only substitution without a standard position.
Anything unmapped is **reported and excluded**, never guessed at.

## Usage

Needs `mne`, `numpy`, `scipy` — the repo `.venv` is enough. No server, no
database, no FreeSurfer.

```bash
# what is in each study: clips, recorded hours, coverage, mark counts
python v2/tools/esi/scalp_onset.py datasets/ScalpEEG --census

# Step 0 on a study, or one file
python v2/tools/esi/scalp_onset.py "datasets/ScalpEEG/1. BellaVeegIchilov"
python v2/tools/esi/scalp_onset.py "datasets/ScalpEEG/2. BellaCCVEEG1" -o out.csv
```

## What Step 0 computes

Per seizure, over `[onset, onset+5 s]` against a `[onset-60, onset-10]` baseline,
band-limited 3–30 Hz, average-referenced, notched at the detected mains:

- **top channels** by envelope ratio in dB, with per-channel onset latency
  (`ei.determine_threshold_onset`, reused rather than reimplemented);
- **region contrasts** — mean dB per side for every left/right region the
  montage fully supports (frontopolar, frontal, central, mid-temporal,
  inferior-temporal, inferior-chain, parietal, occipital, ear);
- the dominant rhythm `f0`, printed rather than assumed.

**Region contrasts, not one chain.** The design document specified a single
inferior-temporal contrast because it framed the case as a temporal-lobe
question. Bella's scalp onsets are *parietal*, so that contrast reads 0.1 dB —
the right region was never in it. Reporting every region the montage supports
removes the assumption; a region is only reported when both sides are complete,
so an asymmetric montage cannot manufacture a lateralisation.

## Onsets come from the sidecar

`eeg2edf-sidecar/1` JSON beside each EDF. The contract that matters:

> `events[].onset_s` is **clip-local, and null for events belonging to sibling
> clips**. Every clip's sidecar carries the whole study's event list, so
> filtering on `onset_s is not None` is what selects this clip's own events.

Without that filter a four-clip study looks like it has sixteen seizures.

## Verification

Step 0 was checked against the clinical reports in the study folders — written
by different teams, 18 months apart, with no knowledge of this code. It
reproduces the read for **5 of 6** pre-surgical seizures:

| seizure | report | tool |
|---|---|---|
| Ichilov #1 | frontal/frontopolar bilateral, *"more pronounced on the right, F4F8"* | frontopolar RIGHT −5.4 dB, frontal RIGHT −3.0 dB |
| Ichilov #2 | right hemisphere, *"right parietal P4T8"* | RIGHT in frontal, central, temporal, parietal, occipital |
| Ichilov #3 | *"P4P8O2 → spreading to T8C4 … C4P4T8P8"* | top: F4, **T8**, P3, **C4**, **P4** |
| Cleveland SZ 1P | *"heralding fast activity in **P4 and P8**"* | top: O2, **P4**, C4, **P8**, P7 |
| Cleveland SZ 2P | *"quasi-rhythmic right hemispheric slow"* | mixed, left frontopolar — **does not match** |

The failing one is the event the clinicians themselves flagged as needing to be
differentiated from parasomnia, and its recording clips 1.4 s after the mark.

`pytest v2/server/tests/test_scalp_montage.py` — 7 tests. The load-bearing one
is `test_t1_t2_map_to_ft9_ft10`, which pins the substitution **and its side**
against the montage's own x-coordinates: a left/right swap there would invert
every lateralisation result silently.

## A bug worth knowing about

Mains was first detected by comparing raw band power at 50 vs 60 Hz. That
reported **50 Hz for a Cleveland recording**, in a 60 Hz country. The spectrum
falls toward Nyquist, so at 200 Hz sampling any broadband rise — drowsy EMG,
movement — adds more power at 50 Hz than at 60 Hz, and the comparison measures
spectral slope rather than line noise. `detect_mains` now uses each line's
**prominence above its local sideband background**, which is flat under a
broadband change. It never affected a result (the analysis band is 3–30 Hz), but
it made the diagnostic untrustworthy.

## Interpretation limits

- **Step 0 is amplitude and timing at the scalp, nothing more.** It supports no
  claim about a generator's location.
- **Scalp EEG cannot see amygdala or temporal-pole onset.** Those generators are
  too deep and too small. The SEEG report has spread reaching parietal contacts
  within 0.5 s, so "scalp sees parietal first" is exactly what you would predict
  if the SEEG read is correct. The scalp data alone cannot separate a parietal
  onset from fast propagation out of a mesial temporal one.
- **Post-resection studies (3, 4, 5) are excluded from the primary analysis.** A
  17.3 mL cavity plus a craniotomy defect is a forward-model hole a 3-layer BEM
  cannot represent, and modelling them on pre-op anatomy would produce a
  confidently wrong answer.
