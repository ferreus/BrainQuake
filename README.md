# BrainQuake

A self-hosted research platform for pre-surgical SEEG epilepsy analysis:
electrode localization, FreeSurfer brain-surface reconstruction, and
seizure-focus computation (EI, HFO, SOZ fusion), through a browser UI backed
by a job-queue server.

## Contents

- [Overview](#overview)
- [Project direction](#project-direction)
- [Repository layout](#repository-layout)
- [Running it](#running-it)
- [Provenance](#provenance)
- [License](#license)

## Overview

BrainQuake began as a reproduction of the [BrainQuake paper and its published
code](https://pmc.ncbi.nlm.nih.gov/articles/PMC8782204/) and grew into a
different thing: a self-hosted platform that runs the full SEEG pre-surgical
pipeline as background jobs — FreeSurfer reconstruction, CT↔MRI registration,
electrode contact detection/segmentation (including import from 3D Slicer),
and epileptogenicity computation (EI/HFO/SOZ) — with a React web UI to drive
it and inspect the results (including a FreeBrowse tab for browsing FreeSurfer
volumes/surfaces without a local install).

The current architecture is FastAPI + SQLite (`v2/server/`) with a React +
Vite + Mantine web client (`v2/web/`), both packaged as Docker images.

## Project direction

**[docs/project-direction.md](docs/project-direction.md)** is the canonical
statement of what this project is for and where it's headed — read it before
making prioritization calls. In short: the paper's own algorithms are one
replaceable component in a larger platform, not the point of the project.
**[docs/cleanup-plan.md](docs/cleanup-plan.md)** tracks the ongoing legacy
removal and code-quality review.

## Repository layout

```
v2/
  server/       # FastAPI + SQLite REST service, job worker
  web/          # React + Vite web UI (Mantine, react-three-fiber) -- the client
  docker/       # Dockerfiles + compose (base image w/ FreeSurfer+FSL, app image, web image)
datasets/       # LOCAL ONLY, gitignored -- sample/patient imaging + EEG
data/           # LOCAL ONLY, gitignored -- working data
docs/           # Project direction, cleanup plan, analysis notes
```

An earlier PyQt5 desktop client/server (`BrainQuake/`) was the project's
starting point; it was removed 2026-08-09 once the web app fully replaced it.
Its full history is preserved at git tag `legacy-final` — see
[Provenance](#provenance).

## Running it

### Docker (recommended)

```bash
# 1. Get a free FreeSurfer license: https://surfer.nmr.mgh.harvard.edu/registration.html
# 2. cp v2/docker/.env.example v2/docker/.env and point FS_LICENSE_HOST at it
# 3. Build the base image once (FreeSurfer + FSL + hough-3d-lines -- rarely changes):
v2/docker/build-base.sh
# 4. Build and start the app:
docker compose -f v2/docker/docker-compose.yml up --build
# 5. Open http://<host>:${WEB_PORT:-80}/
```

### Local development

Server:
```bash
cd v2/server
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
uvicorn app.main:app --reload --port 8000       # API
python -m app.workers.jobs_worker               # job worker, separate terminal
pytest                                           # tests
```

Web:
```bash
cd v2/web
npm install
npm run dev
```

See [CLAUDE.md](CLAUDE.md) for environment variables, architecture notes, and
job types.

## Provenance

The original BrainQuake desktop application and TCP server — the paper's
reference implementation — lived at `BrainQuake/` and is preserved in full at
git tag `legacy-final` (`git checkout legacy-final`). The v2 numeric services
under `v2/server/app/services/` are ports of that code; each module's header
comment records its source file. See
[docs/cleanup-plan.md](docs/cleanup-plan.md) for the removal record.

## License

This project is covered under the [Apache 2.0 License](LICENSE).
