# Neural fragility in the web app — via a process-driven Analysis tab


## Context

`v2/tools/verify_fragility_bella.py` runs the analysis behind the project's strongest
finding to date (`docs/bella_fragility_resection_analysis.md`): 8 seizures →
`compute_fragility_pipeline` per seizure → top-10 contacts vote for their shaft → votes
normalised by shaft size → shaft **D** first, 2 cm outside the resection cavity. It is
CLI-only; `app/sigproc/fragility.py` has exactly one importer inside `server/`, its own
test file.

The obvious move — a Fragility tab — is wrong, and the reason matters. Ictal, Interictal
and a future Fragility tab are the *same screen*: recording bar, `EegCanvas`, compute
form, result panel. `IctalPage.tsx` (219 L) and `InterictalPage.tsx` (190 L) differ in
only four things: EI needs baseline/target picking, HFO needs an event-overlay switch,
and each has its own params form and result panel. `next_steps.md` queues Spike-PAC
(Phase 2) and gPDC (Phase 3) behind fragility, so this duplicates twice more.

**The tabs are organised by algorithm; they should be organised by view.** This plan
makes the per-recording analysis screen process-driven, collapses Ictal and Interictal
into it, and adds fragility as a third process. Tabs go 6 → 5 and stop growing.

`SozPage` is deliberately *not* folded in — it is subject-scoped (it never touches
`edfArtifactId`), consumes artifacts rather than recordings, and renders on the brain.
Different archetype, and where fragility's aggregate eventually belongs.

### Supersedes a recorded decision

`docs/next-session-plan.md:49-53` (2026-08-09) records: *"fragility stays a CLI tool…
Promoting it to a `fragility_compute` job + web tab would put R + EZFragility + Epoch
into `v2/docker/base.Dockerfile`, and that is not worth it until the results have been
validated against EI/HFO."* Both premises are void: `app/sigproc/fragility.py` is pure
NumPy/SciPy with no R, and `next_steps.md` Phase 1.6 reports Spearman **1.0000** vs R
across all 8 seizures. Update that note in the same change rather than silently
overriding it.

## Design

### Every run is a list of recordings

There is no single-vs-multi distinction. A run set is always a list; EI on one recording
is a list of length one. This is why the registry needs no `scope` field, and it hands
EI and HFO multi-recording runs for free — which `docs/` already argues they should have
had, since an n=1 ranking is not a finding.

```ts
type RunSet = Array<{
  edfArtifactId: number;
  marks: Record<string, number | [number, number]>;   // keyed by the process's input slots
}>;
```

### The process registry

Two typed lists replace the ad-hoc per-algorithm flags. `inputs` is what a process
*needs* per recording; `outputs` is what it *produces*, which drives both the result
pane and the canvas overlays.

```ts
// src/features/analysis/processes.ts
interface AnalysisProcess {
  id: string;                                  // "ei" | "hfo" | "fragility" | ...
  label: string;
  inputs: InputSlot[];
  outputs: OutputSpec[];
  viewerDefaults?: Partial<ViewerState>;       // e.g. display band; just a seed
  ParamsForm: ComponentType<ProcessFormProps>;
}

type InputSlot =
  | { key: string; type: "instant"; label: string; fromAnnotation?: boolean; optional?: boolean }
  | { key: string; type: "range";   label: string; optional?: boolean };

type OutputSpec =
  | { kind: "channel_scores"; label: string }   // ranked bar chart
  | { kind: "channel_time";   label: string }   // heatmap
  | { kind: "events";         label: string };  // canvas overlay
```

| Process | `inputs` | `outputs` |
|---|---|---|
| EI | `baseline` range, `target` range | `channel_scores` (EI), `channel_time` (HFER) |
| HFO | `window` range, optional | `channel_scores` (counts), `events` |
| Fragility | `onset` instant, `fromAnnotation` | `channel_scores`, `channel_time` |

Channel exclusions ride the existing `remain_chns` path for every process — the script
grew a `--exclude` flag for exactly this (drop contacts *before* the CAR, so an excluded
contact leaves the reference too, not just the plot).

What this deletes: `markers` (the run-set table renders one column per declared slot, and
`BaselineTargetLayer` generalizes to "pick a time for slot *k*"), `overlays` (`events` is
just an output kind the canvas can draw), and the `mode: "ictal" | "interictal"` branch
in `useEegViewerState` (now `viewerDefaults`, same two bands, sourced from the
declaration). A new method declares its slots and kinds and touches nothing else.

**Params stay a component, not a schema.** `EiComputeForm`'s bipolar-preview summary is
genuine bespoke UI; a generator that accommodates it is not simpler than the component.
Revisit if a later process wants server-published params.

### Onsets come from stored annotations

`RecordingParams.annotations_json` is already populated at upload
(`populate_annotations_on_upload`), so `SZ 1P` … `SZ 8P` are already in the DB. An
`instant` slot with `fromAnnotation` renders as a dropdown of that recording's marks —
the UI equivalent of `seizures.csv`'s `label,edf_path,onset`. No manifest format, no
detection heuristic, no new table.

### One job per run; aggregate is a read

`POST /subjects/{id}/analysis/{process}/run` with `{params, runs: [...]}` creates **one
job per run** and returns them. Job types stay per-process (`ei_compute`, `hfo_compute`,
`fragility_compute`) so existing rows and `RETRY_DISPATCH` keep working.

**Required relaxation:** the current guard rejects a submit when any job of that type is
queued/running for the subject, so an 8-seizure batch would 400 on run 2. Narrow it to
*this process on this recording*. The worker's atomic claim already serializes jobs per
subject, so the batch simply queues — no new queue, one loosened predicate.

The shaft ranking is computed **at read time** by
`GET /subjects/{id}/analysis/{process}/aggregate`, scanning that process's result
artifacts and doing the top-N voting + size normalisation from
`verify_fragility_bella.py:196-209`. Generic over any process emitting `channel_scores`,
so EI gains aggregation for free.

This avoids a batch job type, a manifest table and a study state machine, and degrades
gracefully: 3 of 8 finished means an aggregate over 3, with `n` shown. Re-running one
seizure updates it with no bookkeeping.

### Result viewers keyed by output kind

Not by algorithm — this is where the duplication actually gets deleted:

| Viewer | Serves |
|---|---|
| `RankedChannelChart` | `channel_scores` — replaces `EiResultPanel` + `HiResultPanel` (already near-duplicate inline SVG) |
| `ChannelTimeHeatmap` | `channel_time` — fragility matrix, EI's HFER |
| `EventOverlay` | `events` — existing `EegCanvas` overlay path |
| `ShaftRankingPanel` | the aggregate, with clinical onset/spread tagging |

House style holds: inline SVG for bar charts, Canvas2D + `ImageData` for the heatmap (the
`SpectrogramModal` recipe — per-cell `fillRect` was 600k calls), `d3-scale-chromatic` for
colour, explicit light/dark `COLORS` off `useComputedColorScheme`. No charting library;
adding one would be the first in the repo.

### QC surfaced, parity not

Show `median_r2` per seizure — `v2/tools/fragility/README.md` is explicit that a low
median means the ranking describes noise rather than dynamics, and it is the one guard
the script insists you read first. Skip the Spearman-vs-R gate: it needs
`data/ezfragility_result.txt`, local-only and belonging to the CLI verifier.

## Files

**Server**

| File | Change |
|---|---|
| `app/sigproc/fragility.py` | add `save_fragility_result` / `load_fragility_result`, modelled on `ei.save_ei_result` (diagnostics as a JSON string so `allow_pickle` stays off) |
| `app/services/fragility.py` | **new** — `run_fragility_compute_job(db, job, log_file)`; mirror `services/ictal.py`: `resolve_edf_path` → `load_seeg` → crop → `compute_fragility_pipeline` → save → `register_artifact(..., "fragility_npz", ...)`; `check_cancelled` between windows |
| `app/services/analysis.py` | **new** — `aggregate_channel_scores(db, subject, process)`, the voting/normalisation ported from the script |
| `app/routers/analysis.py` | **new** — `POST /subjects/{id}/analysis/{process}/run`, `GET .../{process}/aggregate`. Existing `ictal.py`/`interictal.py` result endpoints stay as-is |
| `app/routers/ictal.py`, `interictal.py` | narrow the in-progress guard to `(job_type, edf_artifact_id)` |
| `app/workers/jobs_worker.py` | one `JOB_HANDLERS` entry: `"fragility_compute"` |
| `app/main.py` | `include_router` |
| `app/services/edf.py` | add `edf/FRAGdets/{stem}_frag.npz` to `delete_edf_recording`'s cleanup |
| `alembic/` | migration adding `fragility_params_json` to `recording_params` (columns don't auto-create) |
| `docs/next-session-plan.md` | mark the 2026-08-09 CLI-only decision superseded |

**Web** — new `src/features/analysis/`

| File | Contents |
|---|---|
| `AnalysisPage.tsx` | merged page: run set, `EegCanvas`, process selector, params, results |
| `processes.ts` | the registry above |
| `RunSetPanel.tsx` | recording list + one column per declared input slot |
| `FragilityParamsForm.tsx` | method, win/step, crop, eval window, top-N, high-pass. Defaults mirror `run_frag.R` (pre 20 s, post 10 s, eval 0–5 s, top-20) — `verify_fragility_bella.py` is explicit that a parity check on a different window compares windows, not implementations |
| `ChannelTimeHeatmap.tsx` | Canvas2D + `ImageData` |
| `RankedChannelChart.tsx` | generalized from `EiResultPanel` |
| `ShaftRankingPanel.tsx` | aggregate table + per-seizure `median_r2` QC |

Moved in unedited except imports: `EiComputeForm`, `HfoComputeForm`, `SpectrogramModal`.
Deleted: `features/ictal/IctalPage.tsx`, `features/interictal/InterictalPage.tsx`,
`EiResultPanel`, `HiResultPanel` (superseded by `RankedChannelChart`).

Touched: `components/subjectViews.ts` (drop `ictal` + `interictal`, add `analysis`),
`routes/SubjectLayoutPage.tsx`, `api/types.ts` (`JobType` += `fragility_compute`),
`api/endpoints.ts` + `api/queries/useAnalysis.ts`, `components/eeg/useEegViewerState.ts`
(`mode` → `viewerDefaults`), `components/eeg/BaselineTargetLayer.tsx` (generalize to
slot-keyed picking).

Reused unchanged: `EdfRecordingBar`, `EegCanvas`, `EegChannelList`, `AnnotationsPanel`,
`CollapsibleSection`, `useJobPolling`, `useEdfWindow`, `useRecordingParams`.

## Constraint: EI and HFO numerics must not move

The merge is UI-only. Both compute forms keep sending byte-identical params, including
`remain_chns` from the channel list and the mains frequency shared with the display
filter. `docs/plans/clinical-eeg-view.md` documents what happened when analysis inputs
were perturbed by a viewer rewrite — three live regressions. Same hazard; the mitigation
is that `EiComputeForm` and `HfoComputeForm` move file-for-file.

## Sequencing

1. **Server fragility**, no UI — service, job type, save/load, router. Verifiable with
   `pytest` and `curl`.
2. **Guard relaxation + batch run endpoint + aggregate endpoint.** Still no UI.
3. **Analysis tab** — generalize `IctalPage` into `AnalysisPage`, add the registry, port
   EI and HFO in, delete `InterictalPage`. No fragility yet; verify EI/HFO params
   unchanged before moving on.
4. **Fragility process** — run set with annotation-sourced onsets, params form,
   per-seizure heatmap and ranking.
5. **Aggregate** — shaft ranking panel.

## Follow-on, deliberately not in this change

- **Fragility as a third SOZ source.** `fuse_ei_hfo_scores` is hardcoded to two
  modalities; Phase 4 of `next_steps.md` wants EI + fragility + PAC fused. Its own change
  with its own numeric risk.
- **Folding `SozContacts` into the Electrodes 3D view** as a "colour by" mode. Both are
  instanced spheres on the same mesh; mostly deletion, and would give every process a 3D
  view for free.
- **Declarative params schemas** — see the note above.
- **Parity gating against `ezfragility_result.txt`** — stays in the CLI verifier.

## Verification

**Server** (`cd v2/server && source .venv/bin/activate`):

```bash
pytest    # 110 existing must pass unchanged -- EI/HFO numeric paths are untouched
```

New tests, following `tests/test_api.py` conventions (`TestClient` + mocked heavy calls):
- `test_fragility_compute_registers_an_artifact` — job → `fragility_npz` → result endpoint.
- `test_analysis_run_creates_one_job_per_recording` — 8 runs → 8 queued jobs, no 400.
  Pins the relaxed guard.
- `test_analysis_run_rejects_a_duplicate_run_for_one_recording` — the guard still guards.
- `test_aggregate_votes_shafts_by_size` — synthesize two result artifacts with known
  `channel_scores`; assert the ranking matches hand-computed `votes / n_contacts`.
- `test_aggregate_is_empty_without_runs` — 200 + `n_seizures: 0`, not a 500.
- `test_delete_edf_recording_removes_fragility_npz`.

**The real acceptance test** — the web app must reproduce the CLI result on Bella:

```bash
python v2/tools/verify_fragility_bella.py --method extended
```

Run all 8 seizures through the Analysis tab with matching params (extended, 0.25 s /
0.125 s, crop **−20/+10** around each `SZ nP` mark, eval **0–5 s**, **top-20** vote, CAR)
and compare against that output. **Shaft D must lead**, with I and A tagged as clinical
onset. Per-seizure `median_r2` should land in **0.816–0.998**
(`docs/bella_fragility_resection_analysis.md`, re-run 2026-08-28 on `datasets/BellaNew`).
Divergence means the service's cropping or referencing differs from the script's — check
those first.

**Then confirm the merge changed nothing**: EI on clip 29 and HFO on clip 12 with the
settings in `docs/next-session-plan.md` (EI baseline 20–105, target 112–205, band 1–300,
mains 60; HFO 80–250 Hz, mains 60, rel/abs 2.5). HFO must still return **I6, A1, A2**;
EI must match its previous run on the same recording.

**Web**: `cd v2/web && npm run build && npm run lint` (no test runner exists).
