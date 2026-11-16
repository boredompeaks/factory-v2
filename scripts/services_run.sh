#!/bin/sh
# Run one command with a scoped host test-service lifecycle.
# Usage: services_run.sh [--services pg2a,pg2b,redis] [--pull] [--start-engine] -- <command...>
#   --services    comma-separated subset of pg2a,pg2b,redis (default: pg2a)
#   --pull        pull pinned images for absent selected services (owner provisioning)
#   --start-engine try systemctl/service docker when the engine is down
#                 (the engine is always ensured; the engine is left running)
#
# Every service this run uses is protected by an atomic per-service lease
# under CARBON_SERVICES_STATE_DIR (default /tmp/carbon-services). One lease
# protocol, shared with services_down.sh/services_reap.sh:
#   * acquire the lease before starting, restarting, or reusing a container;
#   * never join, stop, remove, or reap a container whose lease is live;
#   * clear a lease only after the container it protected is gone.
# A running container whose lease is missing, stale (owner PID gone), or
# expired (>6h) is considered orphaned: it is removed and recreated under this
# run. A running container with a live lease belongs to another run, so this
# run refuses instead of sharing it. Only containers started by this invocation
# are stopped/removed on EXIT/INT/TERM, and only this invocation's leases are
# released. Emits SERVICES_STARTED= and SERVICES_STOPPED=.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

STATE_DIR=${CARBON_SERVICES_STATE_DIR:-/tmp/carbon-services}
LEASE_TTL=${CARBON_SERVICES_LEASE_TTL:-21600}
LEASE_INIT_GRACE=10

usage() {
    echo "usage: services_run.sh [--services pg2a,pg2b,redis] [--pull] [--start-engine] -- <command...>" >&2
}

die() {
    echo "services_run: $*" >&2
    exit 1
}

# --- per-service leases -----------------------------------------------------
# A lease is a directory $STATE_DIR/<svc>.lease holding "pid" and "started".
# mkdir is the atomic acquisition primitive (fails when the lease exists).

lease_dir() {
    printf '%s/%s.lease\n' "$STATE_DIR" "$1"
}

lease_pid() {
    _lease_ld=$(lease_dir "$1")
    [ -f "$_lease_ld/pid" ] || return 0
    sed -n '1p' "$_lease_ld/pid" 2>/dev/null || true
}

lease_age() {
    _lease_ld=$1
    _lease_now=$(date +%s)
    _lease_ts=""
    if [ -f "$_lease_ld/started" ]; then
        _lease_ts=$(sed -n '1p' "$_lease_ld/started" 2>/dev/null || true)
    fi
    case "$_lease_ts" in
        ''|*[!0-9]*)
            _lease_ts=$(stat -c %Y "$_lease_ld" 2>/dev/null || printf '%s' "$_lease_now")
            ;;
    esac
    printf '%s\n' "$((_lease_now - _lease_ts))"
}

lease_describe() {
    _lease_ld=$(lease_dir "$1")
    _lease_pid=$(lease_pid "$1")
    _lease_age=$(lease_age "$_lease_ld")
    printf 'pid=%s age=%ss' "${_lease_pid:-?}" "$_lease_age"
}

# Live = owner PID is alive and the lease has not exceeded LEASE_TTL.
# A lease younger than LEASE_INIT_GRACE with no pid yet is still being
# initialised by its owner, so it counts as live (do not reap it).
lease_live() {
    _lease_svc=$1
    _lease_ld=$(lease_dir "$_lease_svc")
    [ -d "$_lease_ld" ] || return 1
    _lease_age=$(lease_age "$_lease_ld")
    _lease_pid=$(lease_pid "$_lease_svc")
    case "$_lease_pid" in
        ''|*[!0-9]*)
            [ "$_lease_age" -le "$LEASE_INIT_GRACE" ] && return 0
            return 1
            ;;
    esac
    [ "$_lease_pid" -gt 0 ] || return 1
    [ "$_lease_age" -gt "$LEASE_TTL" ] && return 1
    if [ -d "/proc/$_lease_pid" ]; then
        return 0
    fi
    if kill -0 "$_lease_pid" 2>/dev/null; then
        return 0
    fi
    return 1
}

# 0 acquired, 1 held by a live owner, 2 unusable state dir.
lease_acquire() {
    _lease_svc=$1
    _lease_ld=$(lease_dir "$_lease_svc")
    mkdir -p "$STATE_DIR" 2>/dev/null || return 2
    _lease_try=0
    while [ "$_lease_try" -lt 3 ]; do
        if mkdir "$_lease_ld" 2>/dev/null; then
            printf '%s\n' "$$" > "$_lease_ld/pid"
            printf '%s\n' "$(date +%s)" > "$_lease_ld/started"
            LEASED="$LEASED $_lease_svc"
            return 0
        fi
        if lease_live "$_lease_svc"; then
            return 1
        fi
        rm -rf "$_lease_ld" 2>/dev/null || true
        _lease_try=$((_lease_try + 1))
    done
    return 2
}

lease_release() {
    _lease_svc=$1
    _lease_ld=$(lease_dir "$_lease_svc")
    [ -d "$_lease_ld" ] || return 0
    _lease_pid=$(lease_pid "$_lease_svc")
    case "$_lease_pid" in
        ''|"$$") rm -rf "$_lease_ld" 2>/dev/null || true ;;
    esac
    return 0
}

acquire_for_run() {
    _lease_svc=$1
    if ! lease_acquire "$_lease_svc"; then
        if lease_live "$_lease_svc"; then
            die "$_lease_svc is held by another live run ($(lease_describe "$_lease_svc"))"
        fi
        die "cannot acquire lease for $_lease_svc under $STATE_DIR"
    fi
}

clear_stale_lease() {
    _lease_svc=$1
    _lease_ld=$(lease_dir "$_lease_svc")
    if [ -d "$_lease_ld" ]; then
        echo "services_run: clearing stale lease for $_lease_svc ($(lease_describe "$_lease_svc"))" >&2
        rm -rf "$_lease_ld" 2>/dev/null || true
    fi
}

command -v docker >/dev/null 2>&1 || die "docker not found on PATH"

SERVICES_RAW=""
START_ENGINE=0
PULL=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --services)
            shift
            [ "$#" -gt 0 ] || { usage; exit 2; }
            SERVICES_RAW=$1
            shift
            ;;
        --services=*)
            SERVICES_RAW=${1#*=}
            shift
            ;;
        --start-engine)
            START_ENGINE=1
            shift
            ;;
        --pull)
            PULL=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        -*)
            die "unknown flag: $1"
            ;;
        *)
            break
            ;;
    esac
done

[ "$#" -gt 0 ] || die "no command given (use -- <command...>)"

SELECTED=""
if [ -n "$SERVICES_RAW" ]; then
    old_ifs=$IFS
    IFS=,
    for svc in $SERVICES_RAW; do
        case "$svc" in
            pg2a|pg2b|redis) ;;
            *) IFS=$old_ifs; die "unknown service: $svc" ;;
        esac
        case " $SELECTED " in
            *" $svc "*) ;;
            *) SELECTED="$SELECTED $svc" ;;
        esac
    done
    IFS=$old_ifs
else
    SELECTED=" pg2a"
fi

STARTED=""
STOPPED=""
LEASED=""

cleanup() {
    rc=$?
    trap - EXIT INT TERM
    for svc in $STARTED; do
        docker stop -t 10 "$svc" >/dev/null 2>&1 || true
        docker rm "$svc" >/dev/null 2>&1 || true
        if ! docker inspect "$svc" >/dev/null 2>&1; then
            STOPPED="$STOPPED $svc"
        fi
    done
    for svc in $LEASED; do
        # Clear a lease only after its container is actually gone; otherwise
        # leave it for the reaper (a live owner must never protect a dead one).
        if docker inspect "$svc" >/dev/null 2>&1; then
            echo "services_run: $svc still present after cleanup; lease kept" >&2
        else
            lease_release "$svc"
        fi
    done
    printf 'SERVICES_STARTED=%s\n' "${STARTED# }"
    printf 'SERVICES_STOPPED=%s\n' "${STOPPED# }"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

ensure_engine() {
    if docker info >/dev/null 2>&1; then
        return 0
    fi
    echo "services_run: docker engine down; trying to start it" >&2
    if command -v systemctl >/dev/null 2>&1; then
        systemctl start docker >/dev/null 2>&1 || true
    fi
    if ! docker info >/dev/null 2>&1 && command -v service >/dev/null 2>&1; then
        service docker start >/dev/null 2>&1 || true
    fi
    i=0
    while [ "$i" -lt 60 ]; do
        if docker info >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
        i=$((i + 1))
    done
    die "docker engine not ready after 60s (tried systemctl/service)"
}

wait_ready() {
    svc=$1
    case "$svc" in
        pg2a|pg2b) probe="pg" ;;
        redis) probe="redis" ;;
    esac
    i=0
    while [ "$i" -lt 60 ]; do
        if [ "$probe" = "pg" ]; then
            if docker exec "$svc" pg_isready -U perfume -d perfume_store >/dev/null 2>&1; then
                return 0
            fi
        else
            pong=$(docker exec "$svc" redis-cli ping 2>/dev/null || true)
            if [ "$pong" = "PONG" ]; then
                return 0
            fi
        fi
        sleep 1
        i=$((i + 1))
    done
    die "$svc did not become ready within 60s"
}

if [ "$START_ENGINE" -eq 1 ]; then
    ensure_engine
else
    docker info >/dev/null 2>&1 || ensure_engine
fi

ABSENT=""
add_absent() {
    if [ -z "$ABSENT" ]; then
        ABSENT=$1
    else
        ABSENT="$ABSENT $1"
    fi
}
# shellcheck disable=SC2086
for svc in $SELECTED; do
    if ! docker inspect "$svc" >/dev/null 2>&1; then
        if lease_live "$svc"; then
            die "$svc has a live lease ($(lease_describe "$svc")) but no container; refusing to race"
        fi
        clear_stale_lease "$svc"
        acquire_for_run "$svc"
        add_absent "$svc"
        continue
    fi
    running=$(docker inspect -f '{{.State.Running}}' "$svc" 2>/dev/null || echo "false")
    if [ "$running" = "true" ]; then
        if lease_live "$svc"; then
            # Owned by another live run: reuse would require acquiring its
            # lease, and a live lease must never be killed or shared.
            die "$svc is held by another live run ($(lease_describe "$svc")); refusing to reuse it"
        fi
        echo "services_run: $svc running without a live lease; reaping stale container" >&2
        clear_stale_lease "$svc"
        docker stop -t 10 "$svc" >/dev/null 2>&1 || true
        docker rm "$svc" >/dev/null 2>&1 || true
        if docker inspect "$svc" >/dev/null 2>&1; then
            die "failed to remove stale container $svc"
        fi
        acquire_for_run "$svc"
        add_absent "$svc"
        continue
    fi
    if lease_live "$svc"; then
        die "$svc is stopped but leased by a live owner ($(lease_describe "$svc")); refusing to race"
    fi
    clear_stale_lease "$svc"
    acquire_for_run "$svc"
    docker start "$svc" >/dev/null 2>&1 || die "failed to start existing container $svc"
    STARTED="$STARTED $svc"
done

if [ -n "$ABSENT" ]; then
    STARTED="$STARTED $ABSENT"
    if [ "$PULL" -eq 1 ]; then
        # shellcheck disable=SC2086
        "$SCRIPT_DIR/services_up.sh" --pull $ABSENT
    else
        # shellcheck disable=SC2086
        "$SCRIPT_DIR/services_up.sh" $ABSENT
    fi
fi

# shellcheck disable=SC2086
for svc in $SELECTED; do
    wait_ready "$svc"
done

set +e
"$@"
rc=$?
set -e
exit "$rc"
