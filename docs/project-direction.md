# Project direction: what this project is and where it's going

**Date:** 2026-08-09. This document supersedes the original "reproduce the
BrainQuake paper" framing and the removed PLAN.md phases.

## What this project actually is

This started as an effort to reproduce the BrainQuake paper
([PMC8782204](https://pmc.ncbi.nlm.nih.gov/articles/PMC8782204/)) with its
published code and dataset. Along the way the code needed enough bug-fixing and
rebuilding that the project became something else: a **self-hosted research
platform for SEEG analysis** — a FastAPI server that runs FreeSurfer recon and
registration jobs, a React web UI, 3D Slicer contact import (replacing the
paper's unreliable detection), and a FreeBrowse tab for inspecting FreeSurfer
output without a local install.

The BrainQuake algorithms (EI, HFO, SOZ fusion) are **one replaceable plugin**
inside that platform, not its purpose.

## Why: the real goals

The motivating case is Bella — the maintainer's daughter, who has epilepsy, was
4 years old at scan time, underwent SEEG implantation and a **right temporal
lobectomy that did not help** (seizures unchanged post-surgery).

1. **Re-analyze Bella's case rigorously.** Build a clean, reproducible,
   multi-method dossier strong enough to present to her medical team and
   motivate re-evaluation. Post-failed-surgery re-evaluation is standard
   practice at comprehensive epilepsy centers; the job of this project is to
   give clinicians a concrete, quantitative reason to reopen the case — never
   to out-diagnose them.
2. **Contribute to the epilepsy/neuroinformatics community** where possible. A
   web-based, self-hostable SEEG pipeline with job orchestration genuinely does
   not exist in usable open-source form (EpiTools, AnyWave, Brainstorm each
   cover fragments, desktop-bound). Goal 1 drives priorities; generalization
   comes after it delivers.

## Current evidence state

- **Neural fragility (ezfragility, R; Li et al. 2021 method) on 8 of Bella's
  seizures**: shaft **D** leads the size-normalised ranking at a top-20 vote
  cutoff, while clinical EEG onset was marked on shafts **I and A**. The
  fragility method's published validation is precisely the failed-surgery
  scenario: resections that missed the fragile region. **Resolved 2026-08-13,
  substantially qualified 2026-09-01** — see
  [bella_fragility_resection_analysis.md](bella_fragility_resection_analysis.md).
  Shaft D is right **posterior superior temporal gyrus** (y = −21.5, the most
  lateral shaft), and post-op MRI puts it **16.9–19.5 mm outside the 17.3 mL
  resection cavity**, while clinical-SOZ shaft I was resected outright and A to
  its margin. Li et al.'s own outcome statistic on the same 8 seizures gives an
  interpretability ratio of **0.987** and Cohen's d **+0.37** (their successful
  resections: 1.51; their failures: n.s.) — the method retrodicts this failure
  correctly. **That outcome statistic is the load-bearing result; D is not.**
  D's first place holds only at vote cutoffs ≥ 20 — at top-5 and top-10 the
  clinical shaft **A wins and D is fourth** — and the Cleveland SEEG report
  names A 16 times and I 14 times while never mentioning D, F or T at all.
  Caveats, including the unreleased classifier and the age-4 cohort mismatch,
  are in that document.
- **BrainQuake's own EI on Bella clip 17 disagrees with the clinical
  annotation** — see
  [bella_ictal_ei_vs_annotation_discrepancy.md](bella_ictal_ei_vs_annotation_discrepancy.md).
  Independent band-power analysis supports the annotated shaft A onset, which
  sharpens (not resolves) the disagreement. Unreconciled.
- **Bella's `infant_recon_all` reconstruction**: automated indicators all pass
  (clean pipeline exit; Euler numbers −36/−34; symmetric hemisphere volumes;
  normal vertex counts). FreeSurfer 8.1+ `infant_recon_all` officially supports
  ages 0–5 (templates at 26 and 56 months), so it is the *recommended* pipeline
  for age 4 — adult `recon-all` is not recommended below ~4.5 years. **Visual
  surface-overlay QC still pending** (freeview or FreeBrowse).

## The diagnostic frame: three layers that could be wrong

When an algorithm's output on Bella's data "looks like garbage," the cause sits
in one of three layers, and they must be isolated in order:

1. **Inputs** — recon quality, CT→MRI registration, contact coordinates and
   channel-name↔contact matching, seizure onset annotations, sampling/filtering.
   Most "algorithm gave garbage" outcomes in this field are input problems.
2. **The implementation** — BrainQuake's ported code, known to be rough.
3. **The method itself** — least likely for EI specifically: Bartolomei's EI is
   mainstream and clinically used for two decades. If a faithful EI on clean
   inputs gives nonsense, suspect layers 1–2 first.

That ezfragility produced a *coherent* result on (presumably) the same EEG is
weak evidence the EEG data and annotations are fundamentally okay, shifting
suspicion toward the BrainQuake implementation and the contact/coordinate
pathway.

## Validation roadmap

At each stage, define what "verified" means before moving on. Prefer
**convergence of independent tools** over trust in any single one.

1. **Recon QC** *(in progress)* — visual pass: white/pial surfaces overlaid on
   `norm.mgz`, coronal scroll; skull-strip check on `brainmask.mgz`; aseg
   sanity. Automated indicators already pass.
2. **Contact localization** *(cross-check done 2026-09-01; CT overlay still
   pending)* — the shaft cross-check against the clinical schema is complete:
   the SEEG report's own contact-range labels agree with `parse_mrb` →
   `aparc+aseg` for **66% of named contacts within 2 mm against a 7% base
   rate**, hemisphere-consistent 184/184, with no index offset or reversal. See
   [bella_anatomy_validation.md](bella_anatomy_validation.md) — including the
   two shafts (G, K) that disagree by a whole gyrus, and the 105 contacts the
   report never names. Still open: overlay the contacts on the registered CT and
   the recon (FreeBrowse) to confirm every contact sits inside its own electrode
   artifact — the cross-check validates the *labelling*, not the detection.
3. **EEG integrity** — channel names match contact names exactly (primed vs
   unprimed shafts, apostrophe encoding); onset annotations traceable to the
   clinical log (see the timing map in the EI discrepancy doc); mains filtering
   (60 Hz — Cleveland recording); amplifier-saturation screening (all 203
   channels rail from ~240 s in clip 17; nothing currently warns about this).
4. **Algorithms, plural** — EI via an independent implementation (AnyWave hosts
   Bartolomei's own tooling; `epycom` in Python), keep the ezfragility line,
   add an HFO detector. Where independent methods agree, the finding is
   presentable; where only BrainQuake's port disagrees, that's a bug found.
5. **Resection mapping** — if post-op MRI is available: register it to the
   pre-op T1, map the resection cavity, classify every contact as
   inside/outside. The target sentence: *"the most fragile contacts across 8
   seizures were outside the resected volume"* (if true).

## Deliverable for goal 1

Not software — a dossier: verified imaging, verified contacts, multi-method
concordant localization, resection-overlap analysis, methods cited, presented
as questions for the medical team rather than conclusions. Failed temporal
lobectomy has well-studied patterns ("temporal plus" epilepsy — insular,
orbitofrontal, posterior involvement) that clinicians will recognize if the
data maps onto one.

## Decision log

- **No automated golden-output harness** for v1↔v2 parity (long-standing):
  EI/HFO depend on manual GUI inputs the legacy app never persists. Manual
  spot-verification instead — now largely superseded by the multi-method
  convergence strategy above.
- **2026-08-09: legacy `BrainQuake/` code will be removed** along with
  `tutorials/` and the root `requirements.txt`. Its baseline role is over; git
  history (tag `legacy-final`) preserves it. See
  [cleanup-plan.md](cleanup-plan.md).
- **2026-08-09: v2 gets a thorough readability/correctness review pass** with
  explicit markers on code needing improvement. See
  [cleanup-plan.md](cleanup-plan.md).
