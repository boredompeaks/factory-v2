#!/bin/sh
# k1_build.sh -- owner/FDE only: build the K1 app image once.
#
# Builds files/docker/k1 into k1-app:latest with --pull=false. No application
# code is installed here beyond the pinned runtime dependencies in the image's
# Dockerfile build stage; runtime never installs anything (see
# files/runbooks/k1.md).
#
# This script never tags, pushes, commits, or runs a container. The typed
# k1.up tool, auditor, builder, hacker, and CI must never call it: they
# refuse with IMAGE_MISSING when the image is absent.
#
# Guard: set K1_OWNER_BUILD=1 to confirm you are the owner/FDE.
# Rebuild: set K1_FORCE_REBUILD=1 to rebuild when k1-app:latest already exists.
#
# Usage:
#   K1_OWNER_BUILD=1 scripts/k1_build.sh
#   K1_OWNER_BUILD=1 K1_FORCE_REBUILD=1 scripts/k1_build.sh
set -eu

if [ "${K1_OWNER_BUILD:-}" != "1" ]; then
    echo "k1_build: refused: owner/FDE only; re-run with K1_OWNER_BUILD=1" >&2
    exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "k1_build: refused: docker not found on PATH" >&2
    exit 2
fi

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
    echo "k1_build: refused: not inside a git work tree" >&2
    exit 2
}
cd "$repo_root"

if [ ! -f files/docker/k1/Dockerfile ]; then
    echo "k1_build: refused: missing files/docker/k1/Dockerfile in $repo_root" >&2
    exit 2
fi

if docker image inspect k1-app:latest >/dev/null 2>&1; then
    if [ "${K1_FORCE_REBUILD:-}" != "1" ]; then
        echo "k1_build: k1-app:latest already present; set K1_FORCE_REBUILD=1 to rebuild" >&2
        exit 0
    fi
fi

echo "k1_build: building k1-app:latest from files/docker/k1 (--pull=false)"
exec docker build --pull=false -t k1-app:latest files/docker/k1 "$@"
