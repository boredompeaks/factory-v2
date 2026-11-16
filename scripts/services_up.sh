#!/bin/sh
# Start (or create) the loopback-only host test services.
# Usage: services_up.sh [--pull] [--start-engine] [pg2a|pg2b|redis ...]
#   no service names  -> all three
#   --pull            -> explicitly pull the pinned images first (owner provisioning)
#   --start-engine    -> try systemctl/service docker if the engine is down
# Never pulls implicitly: compose runs with --pull never.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
HARNESS_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
COMPOSE="$HARNESS_ROOT/files/docker/services/compose.yaml"

usage() {
    echo "usage: services_up.sh [--pull] [--start-engine] [pg2a|pg2b|redis ...]" >&2
}

command -v docker >/dev/null 2>&1 || {
    echo "services_up: docker not found on PATH" >&2
    exit 3
}
[ -f "$COMPOSE" ] || {
    echo "services_up: compose file missing: $COMPOSE" >&2
    exit 3
}

PULL=0
START_ENGINE=0
SELECTED=""
for arg in "$@"; do
    case "$arg" in
        --pull)
            PULL=1
            ;;
        --start-engine)
            START_ENGINE=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "services_up: unknown flag: $arg" >&2
            usage
            exit 2
            ;;
        pg2a|pg2b|redis)
            case " $SELECTED " in
                *" $arg "*) ;;
                *) SELECTED="$SELECTED $arg" ;;
            esac
            ;;
        *)
            echo "services_up: unknown service: $arg" >&2
            usage
            exit 2
            ;;
    esac
done

if [ -z "${SELECTED# }" ]; then
    SELECTED=" pg2a pg2b redis"
fi

# Turn the validated allowlisted names into positional parameters. No user
# text reaches a shell: only literal pg2a/pg2b/redis tokens are accepted.
# shellcheck disable=SC2086
set -- $SELECTED

if [ "$START_ENGINE" -eq 1 ]; then
    if ! docker info >/dev/null 2>&1; then
        echo "services_up: docker engine down; trying to start it" >&2
        if command -v systemctl >/dev/null 2>&1; then
            systemctl start docker >/dev/null 2>&1 || true
        fi
        if ! docker info >/dev/null 2>&1 && command -v service >/dev/null 2>&1; then
            service docker start >/dev/null 2>&1 || true
        fi
        i=0
        while [ "$i" -lt 60 ]; do
            if docker info >/dev/null 2>&1; then
                break
            fi
            sleep 1
            i=$((i + 1))
        done
        if ! docker info >/dev/null 2>&1; then
            echo "services_up: docker engine not ready after 60s" >&2
            exit 1
        fi
    fi
fi

if [ "$PULL" -eq 1 ]; then
    # Pull only what is absent locally; present pinned images stay untouched
    # (offline-safe, and no registry round-trip per test run).
    docker compose -f "$COMPOSE" pull --policy missing "$@"
fi

# Fail closed on a missing local image (the normal harness path never pulls).
for svc in "$@"; do
    case "$svc" in
        pg2a|pg2b) ref="postgres:17-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24" ;;
        redis) ref="redis:7-alpine@sha256:7b1753b4c5ad8458906c272a8132b8c15f5557b000379eb115fb239706a384b7" ;;
    esac
    if ! docker image inspect "$ref" >/dev/null 2>&1; then
        echo "services_up: IMAGE_MISSING: $ref (not pulling; run services_up.sh --pull as owner)" >&2
        exit 1
    fi
done

docker compose -f "$COMPOSE" up -d --no-build --pull never "$@"
echo "services up: $*"
