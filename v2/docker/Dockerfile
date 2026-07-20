# BrainQuake v2 server image.
#
# Base: Ubuntu 22.04 LTS, with FreeSurfer 7.4.1 (matching the version the legacy
# BrainQuake/ app was built against -- see CLAUDE.md) installed from the official
# Ubuntu22-built tarball, FSL installed via the official fslinstaller.py, and
# hough-3d-lines built from source (per PLAN.md Phase (c)).
#
# FS_LICENSE must be mounted at runtime (never baked into the image) -- see
# docker-compose.yml.
FROM ubuntu:22.04

ARG FS_VERSION=7.4.1
ARG FS_TARBALL=freesurfer-linux-ubuntu22_amd64-${FS_VERSION}.tar.gz

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

# --- OS packages -------------------------------------------------------
# bc/tcsh/perl/libgomp1: required by FreeSurfer's own scripts/binaries
# libgl1/libxext6/libsm6/libxrender1/libxmu6: linked by some FS binaries
# build-essential/libeigen3-dev/git: build hough-3d-lines from source
# python3/venv/pip: run the FastAPI app + worker
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates wget curl tar unzip \
        bc tcsh perl libgomp1 \
        libgl1 libxext6 libsm6 libxrender1 libxmu6 \
        build-essential libeigen3-dev git \
        python3 python3-venv python3-pip \
    && rm -rf /var/lib/apt/lists/*

# --- FreeSurfer ----------------------------------------------------------
# DEV: reading the tarball straight from a local cache dir on the host via a buildx
# additional build-context named "fsdist" (see docker-compose.yml), to avoid
# re-downloading ~9.5GB on every rebuild while iterating on this Dockerfile.
# Switch back to the wget block below (and drop the --mount line) once validated.
RUN --mount=type=bind,from=fsdist,target=/mnt/fsdist \
    tar -C /usr/local -xzf /mnt/fsdist/${FS_TARBALL}
# RUN wget -q "https://surfer.nmr.mgh.harvard.edu/pub/dist/freesurfer/${FS_VERSION}/${FS_TARBALL}" -O /tmp/freesurfer.tar.gz \
#     && tar -C /usr/local -xzf /tmp/freesurfer.tar.gz \
#     && rm /tmp/freesurfer.tar.gz

ENV FREESURFER_HOME=/usr/local/freesurfer \
    FS_LICENSE=/usr/local/freesurfer/license.txt \
    PATH=/usr/local/freesurfer/bin:/usr/local/freesurfer/fsfast/bin:/usr/local/freesurfer/tktools:/usr/local/freesurfer/mni/bin:$PATH

# --- FSL (flirt/fnirt for CT->MRI registration) -------------------------
RUN wget -q https://fsl.fmrib.ox.ac.uk/fsldownloads/fslconda/releases/fslinstaller.py -O /tmp/fslinstaller.py \
    && python3 /tmp/fslinstaller.py -d /usr/local/fsl -q \
    && rm /tmp/fslinstaller.py

ENV FSLDIR=/usr/local/fsl \
    FSLOUTPUTTYPE=NIFTI_GZ \
    PATH=/usr/local/fsl/share/fsl/bin:$PATH

# --- hough-3d-lines, built from source (not vendored/baked as a binary) -
RUN git clone --depth 1 https://github.com/cdalitz/hough-3d-lines.git /opt/hough-3d-lines \
    && make -C /opt/hough-3d-lines \
    && install -m 0755 /opt/hough-3d-lines/hough3dlines /usr/local/bin/hough3dlines \
    && rm -rf /opt/hough-3d-lines

ENV HOUGH3DLINES_BIN=/usr/local/bin/hough3dlines

# --- app -----------------------------------------------------------------
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

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD wget -q -O /dev/null http://localhost:8000/ || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
