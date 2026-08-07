# Open question: EI ranking disagrees with the clinical annotation (Bella, clip 17)

**Status:** unresolved observation, parked for discussion. Not a known bug.

**Date:** 2026-08-07

## The observation

Running the ictal module's EI on the first seizure of Bella's clip 17 ranks the
top channels as:

```
P6:1.000, P5:0.707, F6:0.580, G'3:0.493, F5:0.445, REF2:0.403, G'5:0.373, G'2:0.347
```

The reviewer who annotated the recording marked the onset on **shaft A**
(`A LVFA -> broad`). Shaft A does not appear anywhere near the top of that list.

Either the annotation, the EI parameters, or the EI implementation is telling us
something we haven't reconciled. Worth understanding before EI output is used to
inform anything.

## Data this was measured on

- File: `datasets/Bella/DA6465AU_17_20240319072231.edf`
  (converted from `DA6465AU.EEG` clip 17 — see `v2/tools/nk2edf/`)
- Recording start: 2024-03-19 07:22:31, 1447 s, 203 channels @ 1000 Hz
- Source: Cleveland Clinic (the `...Toronto` path is where it was *sent* for a
  second opinion, which was declined — not where it was recorded). 60 Hz mains.

Parameters used:

```
baseline_start = 20     baseline_end = 75
target_start   = 100    target_end   = 170
band_low       = 1      band_high    = 300
mains_freq     = 60
```

## Timing map for this clip

The Nihon Kohden viewer's "elapsed" is **cumulative recorded time across all 63
clips**, not wall clock. Clip 17 begins at elapsed 1673 s, so:

> `t in the EDF = viewer elapsed - 1673 s`

Annotations from `DA6465AU.LOG`, converted to EDF-relative seconds:

| t (s) | annotation |
|---|---|
| 0 | `A1+A2 OFF` (montage setting, logged at every clip start) |
| 80 | `IA SPKing` (interictal spiking, shafts I and A) |
| 101 | `in bed with dad` |
| **105** | **`A LVFA -> broad`** |
| 117 | `EEG onset` |
| 120 | `SZ 1P` |
| 121 | `FP_Onset` |
| 122 | `clinical onset`, `L leg proximal tonic`, `CLUSTER` |
| 129 | `MARK ON` (patient/carer button) |
| 158 | `MARK OFF` |
| 164 | `end` |
| 179 | next seizure begins |

This clip contains a **cluster** — roughly 30 further `SZ` marks out to t+1154 s.
Only the first seizure has a clean pre-ictal baseline; all later ones are
preceded by another seizure within ~15 s.

## Signal evidence for shaft A

Measured independently of EI, on bipolar pairs along shaft A (A1-A2 … A9-A10),
25–100 Hz power in 2 s windows, normalised to the pre-ictal median:

```
t=104   1.9x    baseline
t=106   9.1x    <- A LVFA
t=110  11.4x
t=116   2.2x    <- EEG onset
t=122  10.0x    <- clinical onset
t=138 219.4x    organized ictal rhythm
t=158  74.3x
t=164   7.5x    <- end
t=166   0.1x    post-ictal suppression
```

Supporting points:

- The ~10x rise at the LVFA comes at a median bipolar amplitude of only ~190 µV
  p-p, which is consistent with genuine *low-voltage* fast activity.
- After `end` the band power drops to **0.1x** pre-ictal — post-ictal
  suppression, which an artifact would not produce.
- Contacts A6/A7/A8 fire **zero** sharp transients in the first 105 s, then A8
  and A9 are the two most active contacts on the shaft during the seizure.
- A8 logs 255 transients over the whole clip, against 219–317 for every other
  contact on the shaft — i.e. it is *not* an unusually noisy electrode.

So the raw signal does support shaft A (A8/A9 in particular) as the onset zone,
which sharpens the disagreement with the EI ranking rather than explaining it.

### A caution recorded deliberately

During analysis, the A8 discharge at t=106 was initially dismissed as an
electrode pop, on two arguments that turned out to be invalid:

1. *"It's broadband, so it's artifact."* Wrong — a sharp transient is broadband
   by definition, so this does not discriminate artifact from a genuine sharp
   epileptic discharge (e.g. a sentinel spike).
2. *"It's only one contact, so it's artifact."* Backwards — focality is exactly
   what SEEG is implanted to find.

A third concern, amplifier saturation, does not apply at the onset: A8 hits the
±3200 µV rail only from **t≈240 s** onward, 0.00% during 0–240 s.

## Things to check

- [ ] Whether `target_start=100` is right. It sits 5 s before the LVFA, but the
      formal `EEG onset` mark is at 117 s — EI's onset-rank term is sensitive to
      where the window opens.
- [ ] Whether the baseline at 20–75 s is contaminated. `IA SPKing` is flagged at
      80 s, and interictal spiking before that is likely.
- [ ] `determine_threshold_onset` uses `baseline_max + 20*sigma`, so a single
      large baseline excursion raises a channel's threshold and delays its
      detected onset. Channels with quiet baselines are favoured.
- [ ] `compute_ei_index` weights by `1/onset_rank`, so onset *order* dominates
      the ranking. Verify the ordering it derives against the annotated order.
- [ ] Whether P/F shafts genuinely have earlier threshold crossings, or are
      winning on the energy term.
- [ ] Re-run over the full clip rather than a 200 s crop (this run cropped to
      0–200 s to keep memory bounded).
- [ ] Confirm with whoever annotated the study what `SZ 1P` and `FP_Onset` mean
      — `F` and `P` are also shaft names in this implant, so `FP_Onset` may
      itself be referring to the F and P shafts, which would change the reading
      of this entirely.

## Unrelated data-quality note

All 203 channels hit the amplifier rail from ~240 s onward in this clip. The
first seizure (105–165 s) is clean, but any analysis of the ~30 later seizures
in the cluster will be operating on clipped data. Nothing in the pipeline
currently detects or warns about saturation.
