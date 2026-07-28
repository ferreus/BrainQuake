#!/usr/bin/env bash
# Builds the slow-changing brainquake-base image (FreeSurfer + FSL +
# hough-3d-lines) from base.Dockerfile. Run this once, whenever FS_VERSION
# bumps, or whenever base.Dockerfile itself changes -- NOT as part of the
# normal app dev loop (docker compose build only rebuilds ../Dockerfile).
#
# base.Dockerfile fetches the FreeSurfer .deb into a BuildKit cache mount, so
# repeat builds (including --no-cache ones) reuse it instead of re-downloading
# multiple GB -- no local cache/build-context setup needed here, just a
# BuildKit-enabled `docker build` (the default since Docker 23+).
#
# Usage: ./build-base.sh
set -euo pipefail
cd "$(dirname "$0")"

FS_VERSION="${FS_VERSION:-8.2.0}"
TAG="${BASE_IMAGE_TAG:-brainquake-base:fs${FS_VERSION}-fsl-flirt}"

docker build \
    --progress=plain \
    -f base.Dockerfile \
    --build-arg "FS_VERSION=${FS_VERSION}" \
    -t "${TAG}" \
    -t brainquake-base:latest \
    ..

echo "Built ${TAG} (and tagged brainquake-base:latest)"
