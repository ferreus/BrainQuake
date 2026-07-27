# Standalone sidecar image: wraps FastSurfer's own official image with a
# small HTTP API (v2/fastsurfer-worker/app) that the main worker polls to
# run FastSurfer jobs, instead of the main worker shelling out to FastSurfer
# directly. Kept as its own image/Dockerfile (not layered onto
# brainquake-base/brainquake-server) so this heavy, PyTorch-based dependency
# set is never pulled/built unless the fastsurfer compose profile is used,
# and so changes to app/ code here never trigger a rebuild of the main
# api/worker image or vice versa.
# deepmi/fastsurfer tags are named <variant>-v<version>, not a bare version
# -- see https://hub.docker.com/r/deepmi/fastsurfer/tags. FASTSURFER_VARIANT
# must match FASTSURFER_DEVICE (cpu/cuda) set on the worker service in
# docker-compose.yml/.env -- one picks which image gets pulled/built here,
# the other picks the --device flag passed to run_fastsurfer.sh at runtime.
ARG FASTSURFER_VARIANT=cpu
ARG FASTSURFER_VERSION=v2.5.4
FROM deepmi/fastsurfer:${FASTSURFER_VARIANT}-${FASTSURFER_VERSION}

# The base image ends its own build as `USER nonroot` (a system user with no
# home dir) -- switch back to root for our COPY/RUN layers, since nonroot
# has no write access to /venv (pip install would fail with EACCES).
#
# Stays root as the image's own default (no USER switch back at the end) --
# but docker-compose.yml's fastsurfer-worker service overrides this at
# runtime with `user: "${APP_UID:-1000}:${APP_GID:-1000}"`, the same
# non-root UID/GID api/worker run as (see Dockerfile). That's what actually
# runs day to day: matching UIDs is what makes the shared subjects_data
# volume writable from both sides (a fast-surfer job's output needs to be
# writable by the now-non-root `worker` container's post-processing step
# right after), and it happens to be exactly FastSurfer's own recommended
# pattern (`-u $(id -u):$(id -g)`) for avoiding its check_allow_root refusal
# (recon_surf/functions.sh) of the baked-in "nonroot" placeholder user.
# `--allow_root` stays in runner.py's constructed command only as a harmless
# fallback for anyone running this image directly (outside compose, or with
# the user: override removed) as the image's default root user -- it's a
# no-op once the effective UID is non-zero.
USER root

WORKDIR /worker
COPY fastsurfer-worker/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY fastsurfer-worker/app ./app

EXPOSE 8000

# Not wget/curl -- the base image's runtime layer never installs either (only
# `aria2` in an earlier, discarded build stage), so a wget-based healthcheck
# would fail outright. python3 + stdlib urllib is already on PATH (/venv/bin
# is prepended for every user, not just nonroot).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=5)" || exit 1

# The base deepmi/fastsurfer image sets its own ENTRYPOINT
# (tools/Docker/entrypoint.sh + run_fastsurfer.sh, confirmed against their
# stable-branch Dockerfile). Without resetting it here, CMD below would be
# appended as *arguments to run_fastsurfer.sh* instead of running as its own
# command -- the API would silently never start.
ENTRYPOINT []
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
