# BrainQuake v2 base image: OS deps + FreeSurfer + FSL + hough-3d-lines.
#
# This is the slow-changing layer (FreeSurfer 7.4.1 install alone is ~19GB,
# see CLAUDE.md) -- build and tag it once, push it to a registry, and the app
# image (../Dockerfile) just does `FROM brainquake-base:<tag>`. Adding a new
# apt package or bumping a pip dep for the app never touches this file, so it
# never re-downloads/re-extracts FreeSurfer or rebuilds FSL.
#
# Build with docker/build-base.sh (handles the FreeSurfer tarball context).
#
# FS_LICENSE must still be mounted at runtime by the app image/compose file
# -- never baked in here.

# --- hough-3d-lines, built from source in its own stage so the build
# toolchain (build-essential/libeigen3-dev/git, several hundred MB) never
# lands in the final image -- only the compiled binary gets copied out. ---
FROM ubuntu:22.04 AS hough-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git build-essential libeigen3-dev \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/cdalitz/hough-3d-lines.git /opt/hough-3d-lines \
    && make -C /opt/hough-3d-lines

# --- base runtime image ---------------------------------------------------
FROM ubuntu:22.04

ARG FS_VERSION=7.4.1
ARG FS_TARBALL=freesurfer-linux-ubuntu22_amd64-${FS_VERSION}.tar.gz

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

# bc/tcsh/perl/libgomp1: required by FreeSurfer's own scripts/binaries
# libgl1/libxext6/libsm6/libxrender1/libxmu6: linked by some FS binaries
# libgomp1: also needed by hough3dlines' OpenMP runtime
# libgfortran5/libquadmath0: needed by gauss_4dfp and other Fortran-compiled
# FS binaries (talairach_avi's mpr2mni305 step) -- libquadmath0 is not pulled
# in transitively, gauss_4dfp links it directly
# curl: used to fetch micromamba below
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates wget curl tar bzip2 zip unzip \
        bc tcsh perl libgomp1 libgfortran5 libquadmath0 \
        libgl1 libxext6 libsm6 libxrender1 libxmu6 \
    && rm -rf /var/lib/apt/lists/*

# --- FreeSurfer ----------------------------------------------------------
# DEV: reading the tarball straight from a local cache dir on the host via a buildx
# additional build-context named "fsdist" (see build-base.sh), to avoid
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

# --- FSL: flirt only -------------------------------------------------------
# v2/server/app/services/ct_register.py -- the only FSL-dependent code in the
# repo -- calls exactly one binary, `flirt`, for CT->MRI registration. FSL
# publishes every tool as its own conda package on its own channel, so
# installing just fsl-flirt (via micromamba, a ~10MB static conda-compatible
# installer) avoids fslinstaller.py's full-distribution install, which pulls
# down FEAT/MELODIC/atlases/etc. and costs ~10.5GB for a single binary we use.
# Pin a version with `fsl-flirt=X.Y.Z` if reproducibility becomes a concern;
# channel order (FSL channel before conda-forge) matters, per FSL's own docs.
# Add more `fsl-<tool>` packages here if the app grows to need e.g. fnirt.
RUN curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xj -C /tmp bin/micromamba \
    && /tmp/bin/micromamba create -y -p /usr/local/fsl \
        -c https://fsl.fmrib.ox.ac.uk/fsldownloads/fslconda/public/ -c conda-forge \
        fsl-flirt \
    && /tmp/bin/micromamba clean -a -y \
    && rm -rf /tmp/bin /root/.mamba /root/.conda

ENV FSLDIR=/usr/local/fsl \
    FSLOUTPUTTYPE=NIFTI_GZ \
    PATH=/usr/local/fsl/bin:$PATH

# --- hough-3d-lines --------------------------------------------------------
COPY --from=hough-builder /opt/hough-3d-lines/hough3dlines /usr/local/bin/hough3dlines

ENV HOUGH3DLINES_BIN=/usr/local/bin/hough3dlines
