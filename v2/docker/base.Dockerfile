# syntax=docker/dockerfile:1
# BrainQuake v2 base image: OS deps + FreeSurfer + FSL + hough-3d-lines.
#
# This is the slow-changing layer (the FreeSurfer install alone is several
# GB) -- build and tag it once, push it to a registry, and the app image
# (../Dockerfile) just does `FROM brainquake-base:<tag>`. Adding a new apt
# package or bumping a pip dep for the app never touches this file, so it
# never re-downloads/re-installs FreeSurfer or rebuilds FSL.
#
# Build with docker/build-base.sh.
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
# ubuntu:22.04 -- matches the "ubuntu22" .deb build FreeSurfer publishes for
# this version (see FS_DEB below), so apt resolves its declared deps against
# a repo the package was actually built against.
FROM ubuntu:22.04

ARG FS_VERSION=8.2.0
ARG FS_DEB=freesurfer_ubuntu22-${FS_VERSION}_amd64.deb

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

# bc/tcsh/perl/libgomp1: required by FreeSurfer's own scripts/binaries
# libgl1/libxext6/libsm6/libxrender1/libxmu6: linked by some FS binaries
# libgomp1: also needed by hough3dlines' OpenMP runtime
# libgfortran5/libquadmath0: needed by gauss_4dfp and other Fortran-compiled
# FS binaries (talairach_avi's mpr2mni305 step) -- libquadmath0 is not pulled
# in transitively, gauss_4dfp links it directly
# curl: used to fetch micromamba below
# aria2: used to fetch the multi-GB FreeSurfer .deb via parallel connections
# (single-stream curl was the slowest step in this build by a wide margin)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl aria2 tar bzip2 zip unzip \
        bc tcsh perl libgomp1 libgfortran5 libquadmath0 \
        libgl1 libxext6 libsm6 libxrender1 libxmu6 \
    && rm -rf /var/lib/apt/lists/*

# --- FreeSurfer ----------------------------------------------------------
# 8.2.0, not 7.4.1 (the version this image used previously): infant_recon_all
# was merged into the distribution as of this release -- it was entirely
# absent from the 7.4.1 tarball, which is why recon.py's infant-surfer step
# failed with "command not found" rather than some data/atlas error.
#
# 8.x is only distributed as a .deb (no more standalone tarball), installed
# via `apt-get install <local .deb>` so apt resolves its declared
# dependencies from the repos above instead of us guessing them (hence the
# ubuntu22 build, matched to the ubuntu:22.04 base above). The .deb
# installs under a version-named prefix (/usr/local/freesurfer/${FS_VERSION})
# rather than the flat /usr/local/freesurfer/ the old tarball produced
# directly -- every other hardcoded path in this repo (recon.py,
# fastsurfer_client.py's _SIDECAR_LICENSE_PATH, etc.) still expects
# /usr/local/freesurfer, so this looks up wherever SetUpFreeSurfer.sh
# actually landed and arranges for /usr/local/freesurfer to resolve there.
# Because the real install lives *inside* /usr/local/freesurfer (as the
# .../8.2.0 subdir), /usr/local/freesurfer already exists as a real directory
# by the time we get here -- `ln -s x /usr/local/freesurfer` would then treat
# it as a directory target and drop the link inside as .../8.2.0, colliding
# with the real subdirectory ("File exists"). So: if the versioned dir is
# nested under /usr/local/freesurfer, hoist it up a level instead of
# symlinking; only symlink for the (non-nested) case where the deb installed
# fully elsewhere.
# The .deb lands in a BuildKit cache mount (keyed on FS_DEB, so a version
# bump downloads fresh) rather than a normal image layer -- cache mounts
# persist across builds *including* `docker build --no-cache`, which a plain
# `rm`'d /tmp file would not. aria2c's own -c/--continue resumes a partial
# file left behind by an interrupted build; the `-f`/skip check below just
# short-circuits entirely once it's fully downloaded, so re-running this
# layer costs a stat instead of a multi-GB re-download.
RUN --mount=type=cache,target=/fscache,sharing=locked \
    apt-get update \
    && if [ ! -f "/fscache/${FS_DEB}" ]; then \
           aria2c -x 16 -s 16 -k 1M --file-allocation=none \
               --summary-interval=5 --console-log-level=notice --show-console-readout=true \
               -d /fscache -o "${FS_DEB}" \
               "https://surfer.nmr.mgh.harvard.edu/pub/dist/freesurfer/${FS_VERSION}/${FS_DEB}"; \
       else \
           echo "Using cached ${FS_DEB}"; \
       fi \
    && cp "/fscache/${FS_DEB}" /tmp/freesurfer.deb \
    && apt-get install -y /tmp/freesurfer.deb \
    && if [ ! -e /usr/local/freesurfer/SetUpFreeSurfer.sh ]; then \
           fs_pkg="$(dpkg-deb -f /tmp/freesurfer.deb Package)"; \
           fs_real_home="$(dirname "$(dpkg -L "$fs_pkg" | grep -m1 '/SetUpFreeSurfer\.sh$')")"; \
           case "$fs_real_home" in \
             /usr/local/freesurfer/*) \
               mv "$fs_real_home" /usr/local/freesurfer.new \
               && rm -rf /usr/local/freesurfer \
               && mv /usr/local/freesurfer.new /usr/local/freesurfer ;; \
             *) \
               rm -rf /usr/local/freesurfer \
               && ln -s "$fs_real_home" /usr/local/freesurfer ;; \
           esac; \
       fi \
    && rm -f /tmp/freesurfer.deb \
    && rm -rf /var/lib/apt/lists/*

ENV FREESURFER_HOME=/usr/local/freesurfer \
    FS_LICENSE=/usr/local/freesurfer/license.txt \
    PATH=/usr/local/freesurfer/bin:/usr/local/freesurfer/fsfast/bin:/usr/local/freesurfer/tktools:/usr/local/freesurfer/mni/bin:$PATH

# --- FSL: flirt + avwutils --------------------------------------------------
# v2/server/app/services/ct_register.py calls `flirt` for CT->MRI
# registration. fsl-avwutils is needed too: FreeSurfer 8.2.0's
# infant_recon_all (new in this release) internally shells out to
# `fslstats`/`fslmaths` (create_wm_surfaces_mprage_subject.csh) -- without it,
# infant_recon_all fails partway through (no surf/*.white ever gets written)
# but still exits 0, so the failure doesn't surface until a much later step
# (mri_annotation2label) that can't find the surfaces, with a misleading
# "could not read rh.white" error.
#
# FSL publishes every tool as its own conda package on its own channel, so
# installing just these two (via micromamba, a ~10MB static conda-compatible
# installer) avoids fslinstaller.py's full-distribution install, which pulls
# down FEAT/MELODIC/atlases/etc. and costs ~10.5GB for the few binaries we use.
# Pin versions with `fsl-flirt=X.Y.Z` if reproducibility becomes a concern;
# channel order (FSL channel before conda-forge) matters, per FSL's own docs.
# Add more `fsl-<tool>` packages here if the app grows to need e.g. fnirt.
RUN curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xj -C /tmp bin/micromamba \
    && /tmp/bin/micromamba create -y -p /usr/local/fsl \
        -c https://fsl.fmrib.ox.ac.uk/fsldownloads/fslconda/public/ -c conda-forge \
        fsl-flirt fsl-avwutils \
    && /tmp/bin/micromamba clean -a -y \
    && rm -rf /tmp/bin /root/.mamba /root/.conda

ENV FSLDIR=/usr/local/fsl \
    FSLOUTPUTTYPE=NIFTI_GZ \
    PATH=/usr/local/fsl/bin:$PATH

# --- hough-3d-lines --------------------------------------------------------
COPY --from=hough-builder /opt/hough-3d-lines/hough3dlines /usr/local/bin/hough3dlines

ENV HOUGH3DLINES_BIN=/usr/local/bin/hough3dlines
