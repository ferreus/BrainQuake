# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

BrainQuake is a pre-surgical epilepsy planning tool for SEEG electrode localization, brain surface reconstruction, and seizure-focus computation (EI/HFO/SOZ). It is used by researchers and clinicians to process MRI/CT scans and intracranial EEG data.

## Repository layout

```
BrainQuake/     # Legacy client/server — do NOT modify; kept as golden-output baseline
v2/
  server/       # FastAPI + SQLite REST service (phases a+b done)
  client/       # PyQt5 client rewired to REST (phase d done; still 5 separate windows, no unified UI yet)
datasets/       # Bundled S1 and Bella sample data (T1, CT, EDF)
tutorials/      # Jupyter tutorial + electrode module install notes
```

The re-architecture plan lives in [PLAN.md](PLAN.md). All new work goes under `v2/` — the `BrainQuake/` directory stays runnable as the verification baseline throughout.

## Commands

### Legacy app (v1 — reference only)

```bash
# Conda environment (recommended):
conda create -n bq_env -c conda-forge python=3.7 numpy scipy matplotlib=3.4.3 nb_conda vtk mayavi=4.6.2 mne nibabel scikit-learn
conda activate bq_env

cd BrainQuake/BrainQuake
python client_main.py          # launches the main launcher window
```

The legacy server is a separate long-running process:
```bash
# On the server machine (requires FreeSurfer + FSL installed):
cd BrainQuake/BrainQuake/Server_codes
python server.py        # raw TCP on port 6669
python combine.py       # separate always-running recon-all poller
```

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

### Legacy (v1)

- **5 independent PyQt5 windows** launched from `client_main.py`: `client_surf`, `client_elec`, `client_ictal`, `client_inter`, `client_soz`
- **TCP socket server** on port 6669 (`Server_codes/server.py`), custom pickle-framed protocol (`utils_scs.py`), spawning per-request `multiprocessing.Process` jobs
- **`combine.py`** is a separate always-running 8s-cycle poller that shells out to `recon-all`, `flirt`/`fnirt`, `mri_convert`, and the vendored C++ `hough3dlines` binary
- **All numeric computation runs client-side**: EI/HFER (`client_ictal.py`), HFO/HI (`utils/HI_apis.py`), electrode segmentation (`utils/elec_utils.py`), SOZ fusion (`soz_result.py`)
- **Flat-file task queue**: `task_log.txt`/`task_done.txt`, 6 space-delimited fields, no locking

### v2 (FastAPI + SQLite)

**Server** (`v2/server/app/`):
- `main.py` — FastAPI app with CORS; mounts routers for subjects, jobs, recon, electrodes
- `config.py` — single pydantic-settings source of truth for all env vars (replaces scattered reads across legacy files)
- `db.py` — SQLAlchemy engine + `get_db` dependency; SQLite in WAL mode
- `models/` — SQLAlchemy ORM: `Subject`, `Job`, `Artifact`
- `schemas/` — Pydantic request/response models
- `routers/` — one file per resource group; each job-creating endpoint inserts a `queued` row and returns it
- `services/` — ported numeric modules (`recon.py`, `ct_register.py`); future: `electrodes.py`, `ictal.py`, `interictal.py`, `soz.py`
- `workers/jobs_worker.py` — polls `jobs` table for `queued` rows, claims one, runs it, writes a per-job log file to `DATA_ROOT/logs/job_{id}.log`. On startup, fails any stale `running` rows from a previous crash.

**Job state machine**: `queued → running → finished | failed | cancelled`

**Job types implemented so far**: `recon`, `ct_register`, `elec_detect`, `elec_segment`, `ei_compute`, `hfo_compute`, `soz_fuse`.

**File storage**: server disk under `SUBJECTS_DIR` (FreeSurfer convention) + `DATA_ROOT/recv/{subject}/` for raw uploads. DB records artifact kind + relative path; the files themselves are not in the DB.

**Tests** (`v2/server/tests/test_api.py`): use `fastapi.testclient.TestClient` + `unittest.mock.patch` on `subprocess.run` so tests run without FreeSurfer/FSL installed. The mock creates the expected output files so artifact-registration logic is fully exercised. Tests use an in-memory SQLite path (`./data/test_brainquake.db`) cleaned up in the `autouse` fixture.

### Critical files for porting work (v1 → v2)

| File | What to port | Status |
|---|---|---|
| `BrainQuake/Server_codes/utils.py` | `reconrun`/`fastrun`/`infantrun` shell-outs, file task queue | Done (`services/recon.py`) |
| `BrainQuake/Server_codes/eePipeline.py` | CT→MRI FSL registration pipeline | Done (`services/ct_register.py`) |
| `BrainQuake/utils/elec_utils.py` | hough3dlines subprocess, GMM, `ElectrodeSeg` — split into `detect`/`segment` | Done (`services/electrodes.py`) |
| `BrainQuake/client_ictal.py` | `compute_hfer`, `compute_ei_index`, `compute_full_band` | Done (`services/ictal.py`) |
| `BrainQuake/utils/HI_apis.py` + `interictal_utils.py` | HFO/HI detection | Done (`services/interictal.py`) |
| `BrainQuake/soz_result.py` | SOZ fusion/ranking (mayavi call stays client-side) | Done (`services/soz.py`) |

## Current plan (PLAN.md phases)

See [PLAN.md](PLAN.md) for the full itemized checklist. Summary:

- **Phase (a) — FastAPI+SQLite skeleton**: done (subjects, jobs, recon, ct_register, artifacts routers + worker; 5 passing tests)
- **Phase (b) — Numeric pipeline port**: services + routers done (electrode-seg detect/segment/labels/chn-xyz/contacts, EI, HFO, SOZ fusion — see `v2/server/app/services/{electrodes,ictal,interictal,soz}.py`); still pending the user's own manual comparison against the legacy app on the S1 dataset (no automated golden-output harness — EI/HFO depend on manual GUI inputs the legacy app never persists, so captured outputs aren't reproducible from scratch)
- **Phase (c) — Docker image**: done — `v2/docker/Dockerfile` + `docker-compose.yml` (`api`+`worker`). Deviates from the original plan: `FROM ubuntu:22.04` (not the official `freesurfer/freesurfer` image) with FreeSurfer **7.4.1** (matching the legacy app's version, not latest 8.2.0 — upgrade deferred until v2 is otherwise validated) installed from its official Ubuntu22 tarball, FSL 6.0.7 via `fslinstaller.py`, `hough-3d-lines` built from source. FS_LICENSE mounted at runtime, never baked in. Validated: both containers build/boot healthy, all native binaries (`recon-all`, `flirt`, `hough3dlines`) run, DB round-trip works. A real end-to-end `recon-all` run (needs the user's own `FS_LICENSE` + hours) is still open. DEV note: the Dockerfile currently reads the FreeSurfer tarball from a local cache dir via a buildx additional-context instead of `wget`, to speed up iteration — see the commented-out block in the Dockerfile to restore the portable path.
- **Phase (d) — Client REST integration**: done — `v2/client/` has its own copy of every legacy GUI module (`client_main/surf/elec/ictal/inter/soz.py` + `gui_forms/`) rewired to `api_client.py` instead of sockets/local compute; `BrainQuake/` itself untouched. A few buttons have no v2 server endpoint yet and say so instead of doing nothing: electrodes' threshold/erosion auto-tuner, ictal's HFER heatmap and full-band clustering
- **Phase (e) — Unified Qt UI**: single `QMainWindow` with tabs per stage + dockable Jobs/Logs panel with `QProgressBar` + log tail
- **Phase (f) — Integration/E2E**: full S1 pipeline end-to-end via Docker server + redesigned client

**Key constraint**: Phase (b) correctness is the highest-risk item — zero tests exist on the numeric code today, and no automated golden-output harness is being built (per explicit user decision). The user manually verifies each ported service (EI values, HFO event counts, electrode contact coordinates) against the unmodified legacy app on the S1 dataset.
