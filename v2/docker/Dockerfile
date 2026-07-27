# BrainQuake v2 server app image.
#
# FROM the slow-changing brainquake-base image (Ubuntu + FreeSurfer + FSL +
# hough-3d-lines -- see base.Dockerfile). This file is the one to iterate on
# day to day: adding an apt package or a pip dependency here never rebuilds
# or re-downloads FreeSurfer/FSL, since those live in a separate image.
#
# Build brainquake-base first with docker/build-base.sh, or pull it from a
# registry, then build this normally (docker compose build / up --build).
ARG BASE_IMAGE=brainquake-base:latest
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

# python3/venv/pip: run the FastAPI app + worker.
# Add any other app-level OS packages this project needs to this list --
# it's the layer meant to change often.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /app/server
COPY server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY server/ ./

ENV SUBJECTS_DIR=/data/subjects \
    DATA_ROOT=/data \
    DB_URL=sqlite:////data/brainquake.db

# Non-root runtime user. /data (the subjects_data volume's mount point) and
# /app/server are chowned here, before USER switches away from root, so
# Docker seeds a *fresh* subjects_data volume with this ownership the first
# time it's populated (a newly-created named volume's initial content --
# including ownership -- is copied from whatever already exists at that path
# in the image). An *already-used* volume needs a one-time manual
# `chown -R app:app /data` migration instead (see docker-compose.yml's
# comments), since Docker only auto-seeds ownership on a volume's first-ever
# population.
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd -g ${APP_GID} app \
    && useradd -u ${APP_UID} -g app -M -s /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown app:app /data /app/server
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD wget -q -O /dev/null http://localhost:8000/ || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
