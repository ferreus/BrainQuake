# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

BrainQuake is a self-hosted research platform for SEEG analysis: electrode localization, brain surface reconstruction (FreeSurfer recon jobs), and seizure-focus computation (EI/HFO/SOZ, neural fragility via external tools). It began as a reproduction of the BrainQuake paper (PMC8782204) and became a personal research platform — the motivating case and real goals are in **[docs/project-direction.md](docs/project-direction.md)**; read it before making prioritization decisions. The paper's algorithms are one replaceable plugin, not the project's purpose.

## Repository layout

```
v2/
  server/       # FastAPI + SQLite REST service
  web/          # React + Vite web UI (Mantine, react-three-fiber); the only client.
                # The earlier v2/client/ PyQt5 desktop client was removed 2026-08-06.
datasets/       # LOCAL ONLY, gitignored — S1 (public Zenodo) and Bella (T1, CT, EDF)
data/           # LOCAL ONLY, gitignored — working data (e.g. Bella FreeSurfer subject)
docs/           # project-direction.md (goals/roadmap), cleanup-plan.md, analysis notes
```

The legacy v1 client/server (`BrainQuake/`, `tutorials/`) was removed 2026-08-09 — see [docs/cleanup-plan.md](docs/cleanup-plan.md). Its full history and final state are preserved at git tag `legacy-final`; check it out (`git checkout legacy-final`) if you need to read v1 source.

**Data privacy**: `datasets/`, `data/`, and `v2/server/data/` are gitignored and must stay that way — they contain patient imaging and EEG. Never commit files from these directories, never weaken those ignore rules, and never add sample/fixture data containing real recordings to the tree.

All work goes under `v2/`. Current roadmap: [docs/project-direction.md](docs/project-direction.md) (validation roadmap, evidence state) and [docs/cleanup-plan.md](docs/cleanup-plan.md) (legacy removal + v2 review plan).

## Commands

### v2 server

```bash
cd v2/server

# First-time setup
python -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"

# Run the API server
uvicorn app.main:app --reload --port 8000

# Run the background job worker (separate terminal)
python -m app.workers.jobs_worker

# Database migrations
alembic upgrade head
alembic revision --autogenerate -m "description"

# Run all tests
pytest

# Run a single test
pytest tests/test_api.py::test_full_e2e_flow
pytest tests/test_api.py::test_subject_crud
```

### v2 server environment variables (via `.env` file or shell)

| Variable | Default | Notes |
|---|---|---|
| `SUBJECTS_DIR` | `./data/subjects` | FreeSurfer subject directory root |
| `FREESURFER_HOME` | `./data/freesurfer` | Path to FreeSurfer installation |
| `DATA_ROOT` | `./data` | Root for DB, logs, recv folder |
| `DB_URL` | `sqlite:///./data/brainquake.db` | SQLite in WAL mode |
| `FS_LICENSE` | `""` | Path to FreeSurfer license.txt — must be set to run recon jobs |

## Architecture

### v2 (FastAPI + SQLite)

**Server** (`v2/server/app/`):
- `main.py` — FastAPI app with CORS; mounts routers for subjects, jobs, recon, electrodes
- `config.py` — single pydantic-settings source of truth for all env vars (replaces scattered reads across legacy files)
- `db.py` — SQLAlchemy engine + `get_db` dependency; SQLite in WAL mode
- `models/` — SQLAlchemy ORM: `Subject`, `Job`, `Artifact`
- `schemas/` — Pydantic request/response models
- `routers/` — one file per resource group; each job-creating endpoint inserts a `queued` row and returns it
- `services/` — ported numeric modules: `recon.py`, `ct_register.py`, `electrodes.py`, `anatomy.py` (names the FreeSurfer structure each contact sits in), `ictal.py`, `interictal.py`, `soz.py`, `edf.py`/`edf_common.py`, `signal_filters.py`, `freebrowse.py`, `fastsurfer_client.py`, `job_control.py`
- `workers/jobs_worker.py` — polls `jobs` table for `queued` rows, claims one, runs it, writes a per-job log file to `DATA_ROOT/logs/job_{id}.log`. On startup, fails any stale `running` rows from a previous crash.

**Job state machine**: `queued → running → finished | failed | cancelled`

**Job types implemented so far**: `recon`, `ct_register`, `elec_detect`, `elec_segment`, `ei_compute`, `hfo_compute`, `soz_fuse`.

**File storage**: server disk under `SUBJECTS_DIR` (FreeSurfer convention) + `DATA_ROOT/recv/{subject}/` for raw uploads. DB records artifact kind + relative path; the files themselves are not in the DB.

**Tests** (`v2/server/tests/test_api.py`): use `fastapi.testclient.TestClient` + `unittest.mock.patch` on `subprocess.run` so tests run without FreeSurfer/FSL installed. The mock creates the expected output files so artifact-registration logic is fully exercised. Tests use an in-memory SQLite path (`./data/test_brainquake.db`) cleaned up in the `autouse` fixture.

### v1 → v2 porting provenance

All v1 numeric modules were ported (all "Done"); the v1 sources live at git tag `legacy-final` once `BrainQuake/` is removed:

| v1 source (at `legacy-final`) | What was ported | v2 home |
|---|---|---|
| `BrainQuake/Server_codes/utils.py` | `reconrun`/`fastrun`/`infantrun` shell-outs | `services/recon.py` |
| `BrainQuake/Server_codes/eePipeline.py` | CT→MRI FSL registration pipeline | `services/ct_register.py` |
| `BrainQuake/utils/elec_utils.py` | hough3dlines subprocess, GMM, `ElectrodeSeg` — split into `detect`/`segment` | `services/electrodes.py` |
| `BrainQuake/client_ictal.py` | `compute_hfer`, `compute_ei_index`, `compute_full_band` | `services/ictal.py` |
| `BrainQuake/utils/HI_apis.py` + `interictal_utils.py` | HFO/HI detection | `services/interictal.py` |
| `BrainQuake/soz_result.py` | SOZ fusion/ranking (mayavi call dropped) | `services/soz.py` |

## Status and roadmap

The old PLAN.md phase system is retired. Current roadmap: **[docs/project-direction.md](docs/project-direction.md)** (validation roadmap for the Bella case, multi-algorithm convergence strategy) and **[docs/cleanup-plan.md](docs/cleanup-plan.md)** (legacy removal, v2 review order, marking conventions, synthetic-test plan). Historical notes worth keeping:

- **Server + web app**: functional — subjects/jobs/recon/ct_register/electrodes/EI/HFO/SOZ routers + worker; tabbed web UI with Jobs drawer, FreeBrowse tab, 3D Slicer contact import.
- **Docker**: done — split into a hierarchical two-image build to keep the public image small and avoid rebuilding FreeSurfer/FSL on every app change: `v2/docker/base.Dockerfile` (→ `brainquake-base`, built separately via `v2/docker/build-base.sh`, not by compose) holds `FROM ubuntu:24.04` + FreeSurfer **8.2.0** (upgraded from 7.4.1 — 7.4.1's tarball didn't ship `infant_recon_all` at all, which 8.2.0 bundles; 8.x is only distributed as a `.deb` built against Ubuntu 24.04, `wget`ed straight from `surfer.nmr.mgh.harvard.edu` during the build and installed via `apt-get install <local .deb>` so apt resolves its declared deps — the base image moved off 22.04 to match, since resolving a noble-targeted package against a jammy repo risks broken dependency versions; the Dockerfile symlinks wherever the `.deb` actually installs `SetUpFreeSurfer.sh` to `/usr/local/freesurfer`, since every hardcoded path elsewhere in the repo still expects that exact location) + FSL's `fsl-flirt` conda package only (installed via micromamba from FSL's own conda channel — `flirt` is the only FSL binary the codebase calls; this replaced a full `fslinstaller.py` install that cost ~10.5GB for that one binary) + `hough-3d-lines` built from source in a discarded builder stage. `v2/docker/Dockerfile` (→ `brainquake-server`, `api`+`worker` in `docker-compose.yml`) is `FROM brainquake-base` and adds only the Python venv + app code — this is the file to edit for a new apt/pip dependency, since it never touches the base layers. `docker-compose.yml` also has a `web` service (`v2/web/Dockerfile`, multi-stage node build → nginx) so `docker compose up --build` serves the browsable UI alongside `api`/`worker`. FS_LICENSE mounted at runtime, never baked in. Validated on 7.4.1/22.04: both containers build/boot healthy, all native binaries (`recon-all`, `flirt`, `hough3dlines`) run, DB round-trip works. The 8.2.0/24.04 base image itself has not yet been built/validated — that's the next thing to confirm on whichever machine picks this up. A real end-to-end `recon-all` run (needs the user's own `FS_LICENSE` + hours) is still open.

**Key constraint**: numeric-service correctness remains the highest-risk item — zero tests exist on the math today, and no v1 golden-output harness is being built (per explicit user decision; EI/HFO depended on manual GUI inputs the legacy app never persisted). The strategy is now (a) synthetic-signal characterization tests per `docs/cleanup-plan.md`, and (b) cross-validation against independent implementations (ezfragility, AnyWave/epycom EI, independent HFO detectors) per `docs/project-direction.md` — not comparison against the legacy app.
