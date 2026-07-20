# BrainQuake Re-Architecture: Complexity & Effort Estimate (Revised, Smaller Scope)

## Working Model: parallel `v2/` folder, old app stays untouched

All new work lives under a new top-level `v2/` directory in the repo (`/home/ferreus/dev/BrainQuake/v2/`):
`v2/server/` (FastAPI+SQLite service, own dependency set) and `v2/client/` (redesigned PyQt5 client,
own dependency set). **Nothing under the existing `BrainQuake/` app directory is modified** — the
current socket server (`BrainQuake/Server_codes/`) and current 5-window PyQt5 client keep running
exactly as today, so there is always a known-good baseline to run side-by-side and compare against
(manual output verification, visual comparison) throughout the whole migration. This also removes the
"big-bang cutover" risk noted earlier — since old and new can coexist on disk indefinitely, cutover
becomes "when v2 is validated, start using it," not "flip a switch and hope."

## Context

BrainQuake is a single-user PyQt5 desktop tool for epilepsy surgery planning: FreeSurfer/FSL brain
reconstruction, CT-based intracranial electrode localization, EEG-derived seizure-focus computation
(EI/HFO), and a final 3D "SOZ" (seizure onset zone) fusion view. Today it's a hand-rolled TCP socket
client/server pair (`Server_codes/` + 5 independent `client_*.py` PyQt5 windows) with a flat-text-file
task queue, and ALL numerical computation (electrode segmentation, EI/HFER, HI/HFO, SOZ fusion) running
client-side — which also means every researcher's machine needs FreeSurfer/FSL/a C++ toolchain +
libeigen3 installed locally just to build the vendored `hough-3d-lines` binary (documented as a painful
manual step in `tutorials/supplementary_steps_for_elec_module.txt`).

**This revises an earlier, much larger estimate** (full React/Three.js/custom-web-EDF-viewer rewrite,
~15-24 person-weeks raw effort / ~3 months) that the user rejected as unsustainable for daily
solo+AI-assisted work. The revised ask keeps the desktop PyQt5 client — no web frontend — and instead:

1. Turns `Server_codes/` into a proper FastAPI + SQLite REST service.
2. Ships a Docker image bundling that service together with FreeSurfer + FSL + hough-3d-lines, so no
   client machine needs the native toolchain anymore.
3. Moves ALL numeric computation (electrode segmentation, EI/HFER, HI/HFO, SOZ fusion — not just
   recon-all/FSL) into that server, per the user's explicit choice.
4. Keeps the client as a **standalone PyQt5 app**, redesigned into a **single unified main window**
   with tabs per stage (Recon / Electrodes / Ictal / Interictal / SOZ) plus a shared Jobs/Logs panel
   with progress bars and live log tails, replacing the 5 independent windows and bespoke socket
   protocol with REST calls.

This is a materially smaller project than the web rewrite: the existing matplotlib trace-viewer UI and
mayavi 3D visualization code **stay exactly as they are** (no Three.js/canvas port needed at all) — only
their data source changes, from local computation to REST-fetched results.

## 1. Current-State Summary (unchanged from prior research)

**Server** (`Server_codes/`, ~1100 lines): `server.py` is a raw TCP accept loop (port 6669, custom
pickle-framed protocol in `utils_scs.py`). Each request spawns a `multiprocessing.Process` into
`task_utils.py`, which opens a *new single-use* listening socket per payload transfer (ports
6666/6665/6664/6667/6668). `utils.py` implements a flat-text-file task queue (`task_log.txt`/
`task_done.txt`, 6 space-delimited fields, no locking, states `wait→running→finished`). `combine.py`
is a separate always-running poller (~8s cycle) that spawns blocking jobs shelling out to `recon-all`
(hours), `flirt`/`fnirt` (minutes), `mri_convert`/`mri_binarize`/`mri_annotation2label` (fast), and
vendored C++ `hough3dlines` (seconds). FastSurfer/infant_recon_all call sites are currently
commented-out stubs. No tests, no CI, config scattered/inconsistent (`SUBJECTS_DIR` read differently
per file, `FASTPATH` hardcoded to a dead path).

**Client compute today** (currently all local, all moving server-side per this plan): `client_elec.py`
triggers `utils/elec_utils.py` (845 ln: hough3dlines subprocess calls, `sklearn.mixture.GaussianMixture`
clustering, a grid-search/coordinate-descent parameter tuner in `OptimizeParams_thread`, and a
310-line `ElectrodeSeg` class doing iterative per-contact centroid convergence + regression).
`client_ictal.py` has ~330 lines of **already pure numpy/scipy/sklearn computation** at module scope
(`compute_hfer`, `compute_ei_index`, `compute_full_band`). `client_inter.py` triggers `utils/HI_apis.py`
(76 ln) + `utils/interictal_utils.py` (120 ln), both already framework-agnostic. `soz_result.py`
(251 ln) is already framework-agnostic pure-numpy fusion + CSV + a mayavi `plot_3d` call. **Zero tests
exist anywhere**, including for the EI/HFO/electrode-segmentation numeric pipelines that must be ported
with byte-for-byte equivalence — there is no safety net today.

**Client UI today**: 5 independent top-level PyQt5 widgets launched from `client_main.py`, connected
only by a filesystem `subject_dir` convention (no shared app state). All interactivity (2-click
baseline/target range-select, wheel pan, right-click threshold edit) is matplotlib-`mpl_connect`-based,
no custom drag physics. All mayavi 3D scenes are static renders (default trackball camera only).

## 2. Target Architecture

### 2.1 FastAPI service structure
```
server/app/
  main.py            # FastAPI app, CORS, router mounts
  config.py          # pydantic-settings: SUBJECTS_DIR, FREESURFER_HOME, DATA_ROOT, DB_URL, FS_LICENSE
  db.py              # SQLAlchemy engine/session, SQLite (WAL mode)
  models/            # Subject, Job, Artifact
  schemas/           # pydantic request/response models
  routers/           # subjects, recon, electrodes, ictal, interictal, soz, jobs, files
  services/          # ported numeric modules (elec_utils, HI_apis, ictal compute_*, eePipeline, soz_result)
  workers/           # polling job worker(s): recon_runner, elec_runner, ei_runner, hi_runner, soz_runner
alembic/             # schema migrations
```
Direct, low-invention mapping of the existing task types onto routers + a single `jobs` table, now
also covering electrode-seg/EI/HI/SOZ (previously local-only) as first-class job types.

### 2.2 SQLite schema (index metadata, disk stays the artifact store)
```sql
subjects(id, name UNIQUE, hospital, recon_type, subject_dir, created_at, updated_at)

jobs(id, subject_id, job_type,          -- recon|fastsurfer|infant_recon|ct_register|
                                         -- elec_detect|elec_segment|ei_compute|hi_compute|soz_fuse
     state,                             -- queued|running|finished|failed|cancelled
     progress_pct, progress_message, params_json, log_path,
     pid, host, started_at, finished_at, created_at, updated_at)

artifacts(id, subject_id, job_id NULL, kind,   -- orig_mgz|ct_reg_nii|chnXyzDict|labels_npy|
          rel_path, meta_json, created_at)     -- ei_npz|hfo_npz|soz_csv|contact_txt...
```
Replaces `task_log.txt`/`task_done.txt` (unlocked flat-file appends) with transactional rows. The
existing file tree convention (`fslresults/chnXyzDict.npy`, `edf/EIdets/*.npz`, `edf/HFOdets/*.npz`,
`*_labels.npy`, `*_result/*.txt`) is preserved on disk server-side — the DB indexes what exists,
numeric services still read/write `.npy`/`.npz` exactly as today, keeping manual output comparison simple.

### 2.3 Background jobs — polling worker table, not Celery/Redis
A dedicated worker process polls `jobs` (`queued` → claim → `running` → execute → `finished`/`failed`),
run as a second process/container sharing the SQLite DB + `SUBJECTS_DIR` volume with the API — the
lowest-risk evolution of the *existing* architecture (already a polling loop + process spawner), made
transactional/restart-safe. Skip Celery+Redis — unjustified infra for this deployment size.

### 2.4 File storage & client access model
Local disk on the server/container, `SUBJECTS_DIR` stays root of truth, served via FastAPI
`StaticFiles`/streaming `FileResponse`. **Client has no filesystem access to the server at all** —
strictly HTTP: uploads T1/CT/EDF via `POST .../upload`, downloads only the specific small/medium result
artifacts each tab needs (`chnXyzDict.npy`, `*_ei.npz`, `*_events.npz`, `*_labels.npy`, contact `.txt`
files, and — for the SOZ/electrode 3D views — the `.pial` surface files) to render locally with the
existing matplotlib/mayavi code. The full FreeSurfer subject directory remains downloadable as a zip
(as today) for researchers who want it, but isn't required for any in-app view.

### 2.5 Auth
Out of scope (per earlier decision) — deploy behind the existing trusted-network assumption.

### 2.6 Docker image — built FROM the official `freesurfer/freesurfer` image
Rather than installing FreeSurfer from scratch, base our image on the **official
`freesurfer/freesurfer` Docker Hub image** (`FROM freesurfer/freesurfer:8.2.0` or whichever version is
pinned — confirmed: 13.3GB, contains the full FreeSurfer distribution, actively maintained, ~375
pulls/week). This removes FreeSurfer installation entirely from our own build concerns — we inherit a
working, upstream-maintained FreeSurfer environment instead of scripting `recon-all`'s install
ourselves. On top of that base we add: **FSL** (confirmed NOT bundled in `freesurfer/freesurfer` — a
separate install/license from FMRIB, still needed for `flirt`/`fnirt`), `hough-3d-lines` built from the
git https://github.com/cdalitz/hough-3d-lines.git repo (C++ compiler + libeigen3, build-time only — not needed in the client toolchain
anymore), and our Python deps (fastapi, uvicorn, sqlalchemy, mne, nibabel, numpy, scipy,
scikit-learn) + the FastAPI app + worker. Run via `docker compose` (api + worker containers sharing a
`SUBJECTS_DIR` volume + the SQLite file).

**Key operational details**:
- FreeSurfer requires a personal `license.txt` (free via registration) **mounted at runtime** via the
  `FS_LICENSE` env var — confirmed this is how the official image expects it too, so no change needed
  from the original plan; the license key must never be baked into the image.
- **Version pinning matters**: `recon-all` output can shift subtly between major FreeSurfer versions.
  Pin a specific tag (not `latest`) and treat a version bump as something to re-validate manually
  (§ Phase (b)), not just a routine dependency bump.
- Net effect on effort: **Phase (c) gets smaller/lower-risk** than originally estimated — building
  FreeSurfer from source (the biggest unknown in a from-scratch Dockerfile) is eliminated; remaining
  work is mostly FSL install + hough-3d-lines build + our own app layer on a known-good base. Image
  size stays large (official base 13.3GB + FSL ~5-10GB + app layer), which still affects build time and
  registry storage but is now a inherited, well-understood cost rather than a self-built unknown.

### 2.7 Wrapping numeric Python as jobs (now covers everything, not just recon)
- **Recon-all/FSL/CT-registration**: unchanged shell-out logic, invoked from a worker instead of
  `combine.py`'s poll loop — glue migration only.
- **Electrode segmentation** (`utils/elec_utils.py`): decomposed into job endpoints the *existing* Qt
  widgets already call into today, just swapping "call local function" for "POST job, poll, GET
  result": `detect` (hough3dlines+GMM given params → proposed labels/clusters), `segment` (per-contact
  `ElectrodeSeg` convergence given committed labels → contact `.txt` files + `chnXyzDict.npy`). The
  `OptimizeParams_thread` grid-search tuner's live per-trial progress messages map directly onto the
  `jobs.progress_message` field the client already needs to poll for the Jobs/Logs panel — no new UX
  invention required, since the parameter-tuning *widgets* stay in Qt.
- **EI/HFER** (`client_ictal.py` module functions) and **HI/HFO** (`utils/HI_apis.py`): already pure
  numpy/scipy — near-verbatim relocation into `services/`, called from a job given `(edf_id,
  baseline_range, target_range)` — the baseline/target 2-click matplotlib selector stays client-side
  exactly as today; only the "compute" button's action changes from a direct call to a REST POST.
- **SOZ fusion** (`soz_result.py`): numeric fusion (ranking, CSV) ports near-verbatim; the client's
  existing `plot_3d`-equivalent mayavi rendering stays local, now fed by a `GET .../soz/result` JSON
  response instead of locally recomputing.

## 3. REST API Surface (high level)

| Resource group | Endpoints |
|---|---|
| Subjects | `GET/POST /subjects`, `GET/DELETE /subjects/{id}`, `POST /subjects/{id}/upload` (T1/CT/EDF) |
| Recon | `POST /subjects/{id}/recon`, `GET /jobs/{id}`, `GET /jobs/{id}/log`, `GET /subjects/{id}/recon/result`, `POST /jobs/{id}/cancel` |
| Electrodes | `POST /subjects/{id}/electrodes/register-ct`, `POST .../detect` (hough3dlines+GMM params), `PUT .../labels` (commit reviewed labels), `POST .../segment` (contact convergence), `GET .../chn-xyz`, `GET .../contacts/{label}` |
| Ictal/EI | `POST /subjects/{id}/ictal/{edfId}/ei` (baseline/target ranges + band params), `GET .../ei-result` |
| Interictal/HI | `POST /subjects/{id}/interictal/{edfId}/hfo`, `GET .../hfo-result` |
| SOZ | `POST /subjects/{id}/soz/fuse`, `GET .../result` |
| Jobs (shared) | `GET /jobs?...`, `GET /jobs/{id}`, `GET /jobs/{id}/log`, `POST /jobs/{id}/cancel` |
| Artifacts | `GET /subjects/{id}/artifacts`, `GET /artifacts/{id}/download`, `GET /subjects/{id}/download.zip` |

## 4. Client Architecture (PyQt5, redesigned)

- **Single `QMainWindow`** replacing `client_main.py`'s launcher + 5 independent widgets: a tab bar (or
  sidebar) with one tab per pipeline stage — Recon, Electrodes, Ictal, Interictal, SOZ — each tab
  hosting today's widget content (mostly unchanged `gui_forms/*.py` layouts + matplotlib/mayavi
  canvases), now wired to REST calls instead of raw sockets/local function calls.
- **Shared Jobs/Logs panel** (new, e.g. a dockable `QDockWidget` at the bottom): lists in-flight and
  recent jobs across all tabs, each with a `QProgressBar` (driven by `jobs.progress_pct`/`_message`
  polled via `QTimer` + a lightweight `requests`/`httpx` HTTP client) and an expandable log tail (`GET
  /jobs/{id}/log`). This is the one genuinely new piece of client UI; everything else is existing
  widgets rewired to a new backend.
- **HTTP client layer**: a thin `api_client.py` module (base URL + `requests` session) replacing
  `utils/surfer_utils.py`'s socket protocol and every direct call into `utils/elec_utils.py`,
  `utils/HI_apis.py`, and `client_ictal.py`'s local `compute_*` functions.
- **Visualization stays as-is**: matplotlib trace viewers (`client_ictal.py`/`client_inter.py`) and
  mayavi 3D scenes (`client_elec.py`'s `vis3D`, SOZ view) are **not ported** — same libraries, same
  code, just re-pointed at REST-fetched `.npz`/`.npy`/`.pial` files instead of in-process results. This
  eliminates essentially all of the visualization-parity risk that the earlier web-rewrite estimate
  carried.
- **Dependencies**: client drops `scikit-learn`, `mne` (unless still needed for lightweight EDF
  metadata reads to drive the range-selector), and any direct FSL/FreeSurfer/hough3dlines calls;
  keeps `PyQt5`, `matplotlib`, `mayavi`/`vtk`, `nibabel` (for reading downloaded `.pial`/`.mgz`
  artifacts), plus `requests`.

## 5. Phased Breakdown

Dependencies: (a) is foundational; (b) can start once (a)'s job-table pattern exists; (c) (Docker) can
start in parallel with (a)/(b) once the service's dependency list is settled; (d) (client REST wiring)
depends on (a)+(b)'s endpoints existing; (e) (unified UI shell) can start in parallel with (d) and then
integrate; (f) depends on everything.

| Phase | What | Size (raw effort) | Key risk |
|---|---|---|---|
| (a) FastAPI+SQLite service skeleton | Job table, routers, worker, port recon-all/FSL shell-outs | **M — 1.5-2.5 wk** | Protocol replacement (pickle-socket → REST) touches every call site |
| (b) Numeric pipeline port | Electrode-seg (detect/segment split), EI/HFER, HI/HFO, SOZ fusion → services + job endpoints | **L — 3-5 wk** | **Highest correctness risk in the project** — zero existing tests; correctness is verified by the user manually comparing ported output against the legacy app's output on the S1 dataset, not an automated diff |
| (c) Docker image | Dockerfile `FROM freesurfer/freesurfer:<pinned>` + FSL + hough-3d-lines + app, license/env handling, compose file | **S — 0.5-1 wk** | Lower risk now (FreeSurfer install itself is inherited from the official image); remaining work is FSL install, hough-3d-lines build, app layer, and version-pin validation |
| (d) Client REST integration | Replace socket protocol + all local compute calls across client_surf/elec/ictal/inter/soz with REST+polling | **M — 1.5-2.5 wk** | Mechanical but touches every module; needs careful mapping of today's QThreads onto async job polling |
| (e) Unified Qt UI redesign | Single QMainWindow + tabs + shared Jobs/Logs dock with progress bars/log tail | **M — 1.5-2 wk** | Moderate — new dock widget + tab consolidation, but reuses existing per-tab widget code largely as-is |
| (f) Integration/E2E testing | Full pipeline (recon→CT-reg→electrode-seg→EI→HI→SOZ) via Docker server + redesigned client, on S1 dataset | **S-M — 1-1.5 wk** | Multi-hour full run; needs a short-circuit/mocked recon path for fast iteration |

**Raw effort total: ~9-14.5 person-weeks** — down from the earlier ~15-24 week web-rewrite estimate,
mainly because the visualization layer (matplotlib/mayavi) and interaction design (range selectors,
parameter-tuning widgets) don't need to be redesigned or ported to a new framework at all, plus a
smaller Docker phase now that it builds on the official `freesurfer/freesurfer` base image instead of
installing FreeSurfer from scratch.

## 6. Top Cross-Cutting Risks (ranked)

1. **Zero test coverage on numeric code.** Unchanged from before — still the biggest correctness risk.
   No automated golden-output harness: EI/HFO computation depends on manual GUI inputs (baseline/target
   click ranges, band-pass/threshold params) that the legacy app never persists alongside its output, so
   a captured `.npz` can't be reproduced from scratch without knowing the exact session that made it.
   Electrode-segmentation params (threshold %, K, erosion iterations) *are* fully recoverable from
   filenames, but per the user's explicit decision, no automated diff test is being built for any of it —
   correctness for all of Phase (b) is verified by the user manually running the ported service and the
   legacy app side-by-side on the S1 dataset and comparing results.
2. **New network dependency for previously-offline workflows.** Today, electrode segmentation and
   EI/HFO computation run **entirely locally**, no network needed (only the FreeSurfer recon step ever
   talked to a server). Moving all compute server-side means every workflow now requires a reachable
   server — worth explicitly confirming this trade-off is acceptable (it was chosen deliberately this
   session, but flagging it here since it's a real behavior change for any researcher who currently
   works disconnected).
3. **Docker image size & FreeSurfer licensing.** Building `FROM freesurfer/freesurfer:<pinned>`
   (confirmed 13.3GB official image) removes the FreeSurfer-install risk entirely, but the combined
   image (base + FSL + app) is still large; license key must be runtime-mounted per deployment
   (`FS_LICENSE`), never baked in — confirmed this matches the official image's own convention. Build/CI
   time and registry storage remain a real but bounded ops cost, not a correctness risk. New sub-risk:
   **version pinning** — a FreeSurfer version bump can subtly change `recon-all` output, so pin a tag
   and manually re-validate on any upgrade rather than tracking `latest`.
4. **Electrode-segmentation workflow decomposition.** Splitting the old in-process, live-tuning flow
   (`OptimizeParams_thread`, GMM label review, per-contact review) into discrete `detect`/`labels`/
   `segment` job endpoints is the most involved API-design piece of Phase (b) — lower risk than the
   earlier web-rewrite plan (same Qt widgets, not a new framework) but still needs care to keep the
   "live tuning" feel acceptable over request/response + polling rather than in-process calls.
5. **SQLite concurrency.** Low risk at this usage scale (a handful of researchers, low write
   concurrency); keep job-state-update transactions short and never hold one open across a multi-hour
   recon-all run. Revisit Postgres only if usage grows materially.
6. **Cutover strategy — de-risked by the `v2/` parallel-folder model.** No natural protocol bridge
   exists between the old sockets and the new REST API, but since `v2/` lives alongside the untouched
   `BrainQuake/` app rather than replacing it in place, there's no forced big-bang moment — old and new
   can run side-by-side indefinitely, cut over whenever `v2` is validated, and fall back instantly by
   just launching the old client again.

## 7. Total Estimate

Raw effort: **~9-14.5 person-weeks** (§5 total) — reflects inherent task complexity, not who does the
work.

**Calendar time for user + AI pair-programming**: the mechanical phases — (a) FastAPI/SQLite
scaffolding, (c) Dockerfile, (d) REST-wiring the client, (e) the new Jobs/Logs dock widget — compress
well with AI assistance, since they're largely well-specified translation of existing logic onto a new
transport, not novel design. What does **not** compress: wall-clock waits on `recon-all` runs (hours),
the human review/validation cycles needed for the user to manually trust electrode-seg/EI/HFO
correctness (Phase (b)), and end-to-end pipeline runs in Phase (f).

Net: **roughly 5-9 calendar weeks (1-2 months)** of near-daily user+assistant collaboration, weighted
toward the low end if manual domain review turns around quickly, toward the high end if Phase (b)'s
correctness validation is the pacing factor (likely, given zero existing tests).

**Assumptions:**
- Existing recon-all/FSL/electrode-seg/EI/HFO **algorithms are trusted as-is** — faithful port +
  rewrap, not re-validation of the underlying science.
- Client UI can be **functionally redesigned** (unified window, new Jobs/Logs panel) without needing
  pixel-parity with the old 5-window layout; per-tab widget content (forms, buttons, canvases) is
  otherwise reused largely as-is.
- Auth and full offline/no-network operation remain explicitly out of scope (see risk #2 above for the
  behavior-change implication of the latter).
- Excludes: FreeSurfer/FSL license procurement, any formal clinical validation/regulatory process, and
  data-migration effort beyond preserving the current on-disk file-tree convention.

## 8. Itemized Action Checklist

Organized by phase from §5, with concrete file/dir targets under `v2/`. Nothing here touches the
existing `BrainQuake/` directory — it stays runnable as the verification baseline throughout.

### Phase 0 — Setup (do first)
- [x] Create `v2/server/`, `v2/client/` directory skeletons
- [x] Save this plan to `.claude/PLAN.md` (itemized checklist, kept up to date as work progresses)
- [x] `v2/server/` gets its own `pyproject.toml`/`requirements.txt` (fastapi, uvicorn, sqlalchemy,
      alembic, pydantic-settings, mne, nibabel, numpy, scipy, scikit-learn) — independent of the
      existing repo-root `requirements.txt`
- [x] `v2/client/` gets its own `requirements.txt` (PyQt5, matplotlib, mayavi, vtk, nibabel, requests)

### Phase (a) — FastAPI + SQLite service skeleton  ✅ DONE
- [x] `v2/server/app/main.py` — FastAPI app + router mounts + CORS
- [x] `v2/server/app/config.py` — pydantic-settings: `SUBJECTS_DIR`, `FREESURFER_HOME`, `DATA_ROOT`,
      `DB_URL`, `FS_LICENSE` (single source of truth, replacing the scattered/inconsistent env-var
      reads in today's `Server_codes/*.py`)
- [x] `v2/server/app/db.py` — SQLAlchemy engine/session, SQLite WAL mode
- [x] `v2/server/app/models/` — `Subject`, `Job`, `Artifact` ORM models (schema per plan §2.2)
- [x] `v2/server/alembic/` — migrations set up from the start
- [x] `v2/server/app/schemas/` — pydantic request/response models for subjects/jobs/artifacts
- [x] `v2/server/app/routers/subjects.py` — `GET/POST /subjects`, `GET/DELETE /subjects/{id}`,
      `POST /subjects/{id}/upload`, `GET /subjects/{id}/artifacts`, `GET /subjects/{id}/download.zip`
- [x] `v2/server/app/routers/jobs.py` — generic `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/log`,
      `POST /jobs/{id}/cancel` (shared by every job type)
- [x] `v2/server/app/routers/artifacts.py` — `GET /artifacts/{id}/download`
- [x] `v2/server/app/workers/jobs_worker.py` — polling worker: claim `queued` job, run it, update
      `progress_pct`/`state`, write log; restart-safe (requeue/fail stale `running` rows on startup)
- [x] `v2/server/app/routers/recon.py` + `v2/server/app/services/recon.py` — port
      `Server_codes/utils.py`'s `reconrun`/`fastrun`/`infantrun`/`write_a_*cmd` logic (unchanged
      shell-outs) behind `POST /subjects/{id}/recon`; added `GET /subjects/{id}/recon/result`
- [x] `v2/server/app/services/ct_register.py` — port `Server_codes/eePipeline.py`'s `eep()` pipeline
- [x] Smoke test: 5 passing pytest tests with mocked subprocess cover all job state transitions
      (`queued→running→finished`), artifact registration, cancel, download, and artifact filtering;
      real FreeSurfer/FSL smoke test deferred to Phase (c) once the Docker image exists

### Phase (b) — Numeric pipeline port
- [x] `v2/server/app/services/electrodes.py` — port `utils/elec_utils.py` (hough3dlines subprocess,
      GMM clustering, `ElectrodeSeg`), split into `detect()` / `segment()` per plan §2.7. Ad-hoc
      verification against real S1 data (not a committed golden test, per the no-automated-harness
      decision above): `segment()`'s contact coordinates exactly match the legacy `chnXyzDict.npy`;
      a full `detect()` run with the real `hough3dlines.exe` reproduces the identical 822-voxel
      detection mask, with only expected GMM cluster-boundary nondeterminism (the legacy code also
      uses `random_state=None`) in how boundary voxels split between adjacent electrodes.
- [x] `v2/server/app/routers/electrodes.py` — `register-ct`, `detect`, `labels` (PUT), `segment`,
      `chn-xyz`, `contacts/{label}`
- [x] `v2/server/app/services/ictal.py` — port `client_ictal.py`'s `compute_hfer`/`compute_ei_index`/
      `compute_full_band` near-verbatim. Ran end-to-end against the real S1 ictal edf: normalized EI
      in [0,1], no NaNs, plausible channel ranking.
- [x] `v2/server/app/routers/ictal.py` — `POST .../ei`, `GET .../ei-result`
- [x] `v2/server/app/services/interictal.py` — port `utils/HI_apis.py` + `utils/interictal_utils.py`.
      Ran end-to-end against the real S1 interictal edf (5 min, 140 channels): completed cleanly with
      progress callbacks firing correctly.
- [x] `v2/server/app/routers/interictal.py` — `POST .../hfo`, `GET .../hfo-result`
- [x] `v2/server/app/services/soz.py` — port `soz_result.py`'s fusion/ranking logic (drop the mayavi
      `plot_3d` call — that stays client-side). Ran end-to-end fusing the real S1 electrode
      coordinates with the EI/HFO results above; produced a plausible ranked contact table.
- [x] `v2/server/app/routers/soz.py` — `POST .../fuse`, `GET .../result`
- [ ] User manually runs each ported service against the legacy app on the S1 dataset and confirms the
      results match (or that any deviation is understood/acceptable) before moving on

### Phase (c) — Docker image
- [x] `v2/docker/Dockerfile` — deviated from the original "`FROM freesurfer/freesurfer`" idea (see below):
      `FROM ubuntu:22.04`, FreeSurfer **7.4.1** (not 8.2.0 -- matches the version the legacy `BrainQuake/`
      app was built against, per explicit user decision; upgrade to latest FreeSurfer is a deliberately
      deferred follow-up once v2 is otherwise validated) installed from the official Ubuntu22 tarball,
      FSL 6.0.7 via the official `fslinstaller.py`, `hough-3d-lines` built from source
      (github.com/cdalitz/hough-3d-lines.git, `libeigen3-dev` headers land exactly where its Makefile
      already expects them on Ubuntu, no Makefile edits needed), `v2/server`'s Python deps + app in a venv.
      **Deviation from §2.6/checklist wording above**: not based on the official `freesurfer/freesurfer`
      Docker Hub image -- built FreeSurfer's own Ubuntu22 tarball onto a plain `ubuntu:22.04` base instead,
      per explicit user decision. DEV-only wrinkle: the FreeSurfer step currently reads the ~9.5GB tarball
      from a local cache dir via a buildx additional build-context (`fsdist`, pointed at
      `/media/data/opt/freesurfer` in `docker-compose.yml`) instead of `wget`-ing it on every rebuild --
      the commented-out `wget` block in the Dockerfile is the real, portable path to restore before
      anyone else builds this image.
- [x] `v2/docker/docker-compose.yml` — `api` + `worker` services sharing a `subjects_data` volume + the
      SQLite file; `FS_LICENSE` mounted read-only via `FS_LICENSE_HOST` (`.env`, gitignored), never baked
      into the image. Fixed a real startup race found during validation: the worker polled the `jobs`
      table before `api`'s `Base.metadata.create_all()` had run, so `worker` now `depends_on: api:
      condition: service_healthy` (api's HTTP healthcheck only passes after tables exist); the image's
      HEALTHCHECK (HTTP GET on :8000) is disabled for the worker service since it runs no HTTP server.
- [x] Validate: `docker compose up` brings up both containers healthy; confirmed inside the built image
      that `recon-all`/`mri_convert` (FreeSurfer 7.4.1), `flirt` (FSL 6.0.7), `hough3dlines`, and all
      server Python deps are present and runnable, and did a live `POST/GET /subjects` round trip against
      the SQLite DB on the shared volume. **Not done as part of this validation** (deferred to the user,
      needs a real `FS_LICENSE` + a multi-hour run): an actual end-to-end `recon-all` job on real T1 data.

### Phase (d) — Client REST integration  ✅ DONE (done before Phase (c); Docker was skipped at the time)
- [x] `v2/client/api_client.py` — thin `requests`-based HTTP client wrapping every endpoint from §3,
      plus a blocking `wait_for_job()` poll helper for use inside QThreads
- [x] `v2/client/local_store.py` — per-subject local cache dir (edf copies, downloaded/unzipped recon
      zip for `.pial`/`.mgz` files) replacing the legacy user-picked `subject_dir` convention, since
      subjects are now server-managed rows instead of arbitrary folders
- [x] `v2/client/client_main.py` — launcher rewired to `api_client`: server-URL field replaces
      host/port, plus a new subject picker/creator (the legacy app never had a subject list -- v2
      requires one to exist before any job can be queued)
- [x] Rewired `client_surf.py`-equivalent recon tab to `api_client` instead of
      `utils/surfer_utils.py`'s socket protocol (upload -> `POST .../recon` -> poll -> `download.zip`)
- [x] Rewired electrode tab's buttons (`detect`/`labels`/`segment`) to `api_client` instead of local
      `utils/elec_utils.py` calls. Two legacy steps collapse: "Preprocess" and "Label Gen" were separate
      threads client-side, but the v2 `detect` job does both server-side in one call, so both buttons
      now trigger the same `detect()`. `OptimizeParams_thread`'s grid-search tuner has no v2 server
      endpoint (out of scope per this phase's router checklist) -- its button now just explains that.
- [x] Rewired ictal/interictal "compute" buttons to `api_client` instead of local `compute_*`/`HI_apis`
      calls; the matplotlib range-selector/trace-viewer UI is untouched (still 100% local -- it never
      needed server data, just the edf file already on disk). Two ictal buttons have no v2 endpoint:
      "Compute HFER" needs the full time-resolved channels x time matrix, but `ei-result` only returns
      the per-channel scalar summary; "Full band" (`compute_full_band`) was ported to
      `services/ictal.py` but never wired to a router endpoint. Both now explain the gap instead of
      silently doing nothing.
- [x] Rewired SOZ tab to fetch `GET .../soz/result` instead of calling `soz_result.py` locally; the
      mayavi `plot_3d` rendering code is unchanged, just fed by REST rows instead of a local computation
      and reading `.pial` files out of `local_store`'s downloaded recon dir.
- [x] Verified: full offscreen PyQt5 instantiation of all 5 tabs + the launcher against a live v2
      server (`QT_QPA_PLATFORM=offscreen`), which caught and fixed a real bug (gui_forms/elec_form.py
      wires `lineEdit_1`/`lineEdit_2`/`lineEdit_3`/`lineEdit_4`/`doubleSpinBox_1` signals to
      `patientName`/`hospitalName`/`numberK`/`numberEro`/`threSel`, which the initial port had dropped).
      Also ran a full live round trip (detect -> labels -> segment -> chn-xyz -> contacts) against real
      S1 CT/mask data through a running server+worker, confirming every request/response shape matches.

### Phase (e) — Unified Qt UI redesign ✅ DONE (see PHASE_E_PLAN.md for the detailed plan/checklist)
- [x] `v2/client/main_window.py` — single `QMainWindow`, tabs in the requested order (MRI/CT Surface
      Reconstruction / Electrodes Extraction / Ictal module / Interictal module / Visualization
      Results), reusing existing `gui_forms/*.py`-style widget content per tab; retired
      `client_main.py` (fully superseded, deleted per user confirmation)
- [x] `v2/client/patients_panel.py` — new dockable Patients (subjects) widget: list/search/create/
      delete, background-thread refresh (`SubjectPollThread`), broadcasts the selected subject to
      all 5 tabs via `app_state.py`'s `AppState.subjectChanged`
- [x] `v2/client/jobs_panel.py` — new dockable Jobs/Logs widget: background-thread-polled job list
      (2s cycle while anything's in flight, 10s idle), one `QProgressBar` + cancel button + log-tail
      dialog per job
- [x] `v2/client/connection_monitor.py` — background health-check thread driving a status-bar
      connection indicator + inline "can't refresh" banners on both docks; verified live (killed the
      server mid-session) that the UI stays responsive (tab switch ~39ms) while red
- [x] `v2/client/mayavi_view.py` — embeddable mayavi scene (TraitsUI `SceneEditor`) used by the
      recon/electrodes/SOZ tabs instead of legacy pop-out `mlab.show()` windows, per explicit user
      decision (higher-risk option, see PHASE_E_PLAN.md's Risks section). **Confirmed real
      trade-off during smoke-testing**: the embedded pattern segfaults on Python/VTK teardown at
      process exit on this dev machine (a bare pop-out `mlab.show()` does not); mitigated with
      `os._exit(0)` after `app.exec_()` in `main_window.py`'s entry point instead of
      `sys.exit(app.exec_())` -- flagged for re-verification on the actual target machine.
- [x] Live smoke-test against a real `v2/server` instance: subject creation/selection propagates
      into all 5 tabs' `.subject`; a fresh `AttributeError` in `PatientsPanel.__init__`
      (`poll_thread` referenced before construction) was caught and fixed this way.

### Phase (f) — Integration/E2E testing
- [ ] One full pipeline run (recon → CT-reg → electrode-seg → EI → HI → SOZ) via `v2/client` talking
      only to the Dockerized `v2/server`, on the S1 dataset, no manual filesystem intervention
- [ ] Visual comparison of trace views and mayavi 3D views against the old `BrainQuake/` app's output
      on the same dataset

## Critical Files Referenced

- `Server_codes/utils.py` — flat-file task queue + all FSL/FreeSurfer/FastSurfer subprocess calls to port into the jobs table/worker
- `Server_codes/task_utils.py`, `Server_codes/server.py`, `utils/surfer_utils.py` — socket protocol to replace entirely with REST
- `utils/elec_utils.py` — electrode segmentation core (hough3dlines, GMM, `ElectrodeSeg`) — needs decomposition into detect/labels/segment job endpoints
- `client_ictal.py` — EI/HFER pure-numpy compute functions (portable near-verbatim); range-select UI stays as-is
- `utils/HI_apis.py` + `utils/interictal_utils.py` — HFO/HI detection to port into an interictal job endpoint
- `soz_result.py` — already framework-agnostic fusion logic; mayavi call stays client-side
- `Server_codes/eePipeline.py` — CT→MRI registration pipeline to wrap as a job
- `client_main.py`, `gui_forms/*.py` — current window/widget layouts to consolidate into the unified QMainWindow
- `requirements.txt` (repo root) — current dependency set, to be split between server (numeric/native-tool deps) and client (Qt/viz/http deps)
- `tutorials/supplementary_steps_for_elec_module.txt` — documents the local C++ toolchain pain this change eliminates for end users
- `https://hub.docker.com/r/freesurfer/freesurfer` — official base image for the Docker build (confirmed 13.3GB, full FreeSurfer distribution, `FS_LICENSE` env-var convention, tag `8.2.0` latest at time of writing — pin explicitly, don't track `latest`)

## How to Validate This Plan (once/if execution starts)

- **Phase (b) done-criteria**: the user has manually run each ported service against the legacy app on
  the bundled S1 dataset (EI values, HFO event counts/timestamps, electrode contact coordinates) and
  confirmed the results match, or that any deviation is understood and accepted. No automated diff test.
- **Phase (c) done-criteria**: ✅ image builds (`FROM ubuntu:22.04` + FreeSurfer 7.4.1 + FSL 6.0.7 +
  hough-3d-lines, not the originally-planned `FROM freesurfer/freesurfer:<pinned>` -- see checklist
  deviation note above); `docker compose up` brings up API+worker healthy, confirmed all native binaries
  present/runnable and a live DB round trip, no host FreeSurfer/FSL install required. Still open: the
  user running an actual `recon-all` end-to-end inside the container with a real `FS_LICENSE` (multi-hour,
  not exercised in this validation pass).
- **Phase (f) done-criteria**: one full pipeline run (recon → CT-reg → electrode-seg → EI → HI → SOZ)
  completes end-to-end on the S1 dataset via the redesigned Qt client talking only to the Dockerized
  REST API, with visual output (trace views, 3D mayavi views) matching the pre-change baseline.
