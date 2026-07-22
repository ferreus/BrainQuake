#!/usr/bin/env bash
# Builds the slow-changing brainquake-base image (FreeSurfer + FSL +
# hough-3d-lines) from base.Dockerfile. Run this once, whenever FS_VERSION
# bumps, or whenever base.Dockerfile itself changes -- NOT as part of the
# normal app dev loop (docker compose build only rebuilds ../Dockerfile).
#
# Usage: ./build-base.sh [fsdist-dir]
#   fsdist-dir defaults to $FSDIST_DIR or /media/data/opt/freesurfer, and
#   must contain freesurfer-linux-ubuntu22_amd64-<FS_VERSION>.tar.gz -- see
#   the DEV note in base.Dockerfile for why this is a local bind mount
#   instead of a wget download.
set -euo pipefail
cd "$(dirname "$0")"

FS_VERSION="${FS_VERSION:-7.4.1}"
FSDIST_DIR="${1:-${FSDIST_DIR:-/media/data/opt/freesurfer}}"
TAG="${BASE_IMAGE_TAG:-brainquake-base:fs${FS_VERSION}-fsl-flirt}"

docker build \
    -f base.Dockerfile \
    --build-context fsdist="${FSDIST_DIR}" \
    --build-arg "FS_VERSION=${FS_VERSION}" \
    -t "${TAG}" \
    -t brainquake-base:latest \
    ..

echo "Built ${TAG} (and tagged brainquake-base:latest)"
