#!/usr/bin/env bash
# Builds the slow-changing brainquake-base image (FreeSurfer + FSL +
# hough-3d-lines) from base.Dockerfile. Run this once, whenever FS_VERSION
# bumps, or whenever base.Dockerfile itself changes -- NOT as part of the
# normal app dev loop (docker compose build only rebuilds ../Dockerfile).
#
# base.Dockerfile wgets the FreeSurfer .deb straight from
# surfer.nmr.mgh.harvard.edu during the build, so no local cache/build-context
# setup is needed here.
#
# Usage: ./build-base.sh
set -euo pipefail
cd "$(dirname "$0")"

FS_VERSION="${FS_VERSION:-8.2.0}"
TAG="${BASE_IMAGE_TAG:-brainquake-base:fs${FS_VERSION}-fsl-flirt}"

docker build \
    -f base.Dockerfile \
    --build-arg "FS_VERSION=${FS_VERSION}" \
    -t "${TAG}" \
    -t brainquake-base:latest \
    ..

echo "Built ${TAG} (and tagged brainquake-base:latest)"
