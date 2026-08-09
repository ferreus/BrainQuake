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
  seizures**: shaft **D dominates** the size-normalised ranking (4.83
  votes/channel, ~2.5× the next shaft), while clinical EEG onset was marked on
  shafts **I and A**. Fragility ranked the clinical onset shafts above the
  spread shafts (sanity check passed), but its top pick is a shaft clinicians
  did not mark. The fragility method's published validation is precisely the
  failed-surgery scenario: resections that missed the fragile region. **Open
  question: where is shaft D anatomically, and was it inside the resection?**
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
2. **Contact localization** — overlay 3D-Slicer-imported contacts on the
   registered CT and the recon (FreeBrowse): every contact inside its electrode
   artifact on CT, in anatomically sensible tissue on MRI. Cross-check shafts
   against the clinical implantation schema. Watch coordinate-space conventions
   (RAS vs voxel vs scanner) at every handoff.
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
