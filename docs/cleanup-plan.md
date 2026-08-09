# Cleanup plan: legacy removal + v2 review

**Date:** 2026-08-09. Companion to [project-direction.md](project-direction.md).

## Part 1 — Remove all non-v2 legacy code

### What goes

| Path | Contents | Notes |
|---|---|---|
| `BrainQuake/` | 34 tracked files: PyQt5 client, TCP server, utils | 2.1 GB on disk (mostly untracked data/build output) |
| `BrainQuake/utils/hough-3d-lines` (+ nested) | 2 git submodules | Docker builds hough3dlines by cloning upstream directly — no dependency |
| `tutorials/` | Jupyter tutorial + elec module notes | Documents the removed v1 workflow |
| `requirements.txt` (root) | v1 PyQt5/mayavi pins | v2 has its own in `v2/server/` and `v2/web/` |
| `docs/Figure1_MainSoftware.png`, `docs/result.png` | v1 screenshots | Only referenced by the README being rewritten |

**Keeps:** `docs/Manuscript_BrainQuake_HB.pdf` (provenance — the paper this
started from), `docs/round_icon_min.png` (web app has its own copy, but
harmless), the `v2/freebrowse` submodule, `LICENSE` (Apache 2.0 still applies
to derived v2 code).

### Data is local-only, never bundled (done 2026-08-09)

`datasets/`, root `data/`, and `v2/server/data/` are gitignored — they hold
patient imaging and EEG and must never enter the repository. Already executed
ahead of Part 1: untracked four accidentally committed files (2× 38 MB
`S1_ictal.edf` runtime copies, 2 timestamped test-export zips) and widened the
ignore rule from three specific DB files to all of `v2/server/data/`.
Remaining option, only relevant if the repo is ever published: purge the two
S1 EDFs from git history (`git filter-repo`) — they are public Zenodo data, so
this is ~76 MB of bloat, not a privacy issue. Bella's data has never been
tracked.

### Verified preconditions (checked 2026-08-09)

- v2 references to `BrainQuake/` are **comments only** (provenance notes in
  `v2/server/app/services/{ictal,interictal,electrodes,soz,signal_filters}.py`).
  No imports, no path references.
- `v2/docker/base.Dockerfile` clones `hough-3d-lines` from GitHub in its
  builder stage — does not use the legacy submodule.
- Nothing in `v2/` shells out to legacy scripts.

### Steps

1. **Tag the last legacy-containing commit**: `git tag legacy-final` (push the
   tag). This replaces the "keep BrainQuake/ runnable as baseline" constraint —
   the baseline is now recoverable via checkout instead of carried in the tree.
2. Remove submodules cleanly: `git submodule deinit -f BrainQuake/utils/hough-3d-lines`
   (both entries), `git rm` the paths, prune the two `BrainQuake` entries from
   `.gitmodules` (keep `v2/freebrowse`), remove `.git/modules/BrainQuake*`.
3. `git rm -r BrainQuake tutorials requirements.txt docs/Figure1_MainSoftware.png docs/result.png`
   (untracked local data under `BrainQuake/` gets a manual `rm -rf` after the
   commit lands — it contains nothing unique; confirm before deleting).
4. **Rewrite `README.md`** for what the project is now: v2 web app + server,
   real setup instructions (docker compose / dev setup), pointer to
   `docs/project-direction.md`, provenance section crediting the original
   BrainQuake paper/repo and noting the `legacy-final` tag.
5. **Rewrite `CLAUDE.md`**: drop the v1 sections (legacy commands, legacy
   architecture, porting table, PLAN.md phase summary), keep v2 commands +
   architecture + Docker notes, point at the new docs.
6. Update the provenance comments in v2 services to reference
   `legacy-final:BrainQuake/...` instead of live paths (they remain the
   authoritative record of what was ported from where).
7. **Verify**: `docker compose build` succeeds; `pytest` in `v2/server` passes;
   `npm run build` in `v2/web` passes; `git grep -i 'BrainQuake/'` returns only
   intentional provenance/tag references.

## Part 2 — v2 cleanup, readability, review

### Scope

- `v2/server/`: ~56 Python files — app (config, db, models, schemas, 13
  routers, ~14 services, workers) + 4 test files.
- `v2/web/`: 75 files — API layer (client, endpoints, types, ~13 query hooks),
  EEG viewer components, three.js scene components, feature tabs.

### Review order (by risk, highest first)

The numeric services decide scientific correctness and were ported from rough
v1 code with **zero tests on the math**. They come first:

1. `services/ictal.py` + `services/signal_filters.py` — EI/HFER; already has a
   known unreconciled discrepancy (see
   [bella_ictal_ei_vs_annotation_discrepancy.md](bella_ictal_ei_vs_annotation_discrepancy.md)).
   Specific attention: `determine_threshold_onset` baseline sensitivity,
   `1/onset_rank` weighting, crop/window handling, saturation blindness.
2. `services/electrodes.py` — hough3dlines + GMM contact segmentation; feeds
   every spatial result. Coordinate-space conventions (voxel vs RAS) audited
   end-to-end, including the 3D Slicer import path
   (`routers/electrodes.py`, `patient_io.py`, web `SlicerContactsPreview`).
3. `services/interictal.py` — HFO/HI detection.
4. `services/soz.py` — fusion/ranking.
5. `services/{edf,edf_common}.py` — channel naming, units, annotation parsing
   (the layer most likely to silently corrupt everything downstream).
6. `workers/jobs_worker.py` + `services/job_control.py` — crash/stale-job
   handling, concurrency.
7. Routers + schemas — thin, review for consistency and error handling.
8. `v2/web` — EEG viewer state (`useEegViewerState`), three.js contact
   rendering (coordinate handling again), API layer last.

### Marking convention for bad code

Findings land in two places so they're both greppable and triaged:

- **In code**: `# FIXME(correctness): ...` for suspected wrong behavior,
  `# TODO(cleanup): ...` for readability/structure debt,
  `# NOTE(v1-quirk): ...` where v1 behavior was preserved deliberately and
  looks wrong but changing it needs a decision. Same tags in TS (`//`).
- **In `docs/code-review-findings.md`** (created during the review): one line
  per finding — file:line, severity (high = could corrupt results silently,
  medium = fragile/unclear, low = style), status. High-severity correctness
  items get fixed in the same pass; the rest get marked and batched.

### Readability pass (per module, same visit as review)

- Rename v1-inherited names that obscure meaning (`trackRecognition`,
  `CTresult_dir`, single-letter loop state) — keep a provenance comment where
  the v1 name aids cross-referencing against `legacy-final`.
- Docstrings on every service entry point stating **units, array shapes,
  coordinate space, channel-name conventions** — the three bug classes this
  project has actually hit.
- Delete dead code and commented-out v1 remnants (git history keeps them).
- Type hints on service signatures (not a full mypy campaign — signatures and
  return types where they document intent).

### Tests to add while reviewing (cheap, no golden outputs needed)

Synthetic-signal characterization tests for the pure numeric functions:

- EI on a synthetic recording with a known injected onset channel → that
  channel must rank first; shifting the injection shifts the ranking.
- Filters: pass/stop-band behavior on synthetic tones (incl. 50 vs 60 Hz mains).
- HFO detector on synthetic ripples at known times → detected count/timing.
- Contact segmentation on a synthetic point cloud of K straight shafts → K
  shafts recovered with correct contact counts.
- EDF layer: channel-name round-trip with primed names (`X'12`), annotation
  time math (the clip-elapsed-offset trap from the discrepancy doc).

These double as the layer-2 (implementation) isolation instrument from
[project-direction.md](project-direction.md): a port that fails synthetic
sanity checks is buggy regardless of any clinical ground truth.

### Tooling baseline (once, before the module passes)

- `ruff check` + `ruff format` for `v2/server` (config in `pyproject.toml`),
  autofix the noise first so review diffs stay readable.
- `eslint` + `tsc --noEmit` clean for `v2/web`.
- Optional: pre-commit hooks once both are green.

### Sequencing

1. Part 1 (legacy removal) — one commit, verified.
2. Tooling baseline — one commit of autofixes, zero manual changes mixed in.
3. Module-by-module review in the risk order above — one commit per module,
   findings logged, high-severity fixes included, synthetic tests added with
   the module they cover.
