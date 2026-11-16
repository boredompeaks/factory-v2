#!/bin/sh
# Reap orphaned host test-service containers (pg2a, pg2b, redis).
# Usage: services_reap.sh [--services pg2a,pg2b,redis]
#   no service names -> all three
#
# Idempotent and safe to run manually (e.g. after a killed MCP/test run).
# One lease protocol, shared with services_run.sh/services_down.sh:
#   * a live lease (owner PID alive, lease younger than the TTL, default 6h)
#     is never stopped, removed, or cleared;
#   * a per-service reap lock is taken before the recheck, and the reaper then
#     acquires the service lease for the duration of the removal, so a
#     concurrent owner can neither acquire the lease nor start the container
#     between recheck and removal;
#   * a stale lease is cleared only after its container is successfully gone.
# Live services are reported and left untouched.
# Prints one line per service plus "services_reap: removed=N kept=M".
set -eu

STATE_DIR=${CARBON_SERVICES_STATE_DIR:-/tmp/carbon-services}
LEASE_TTL=${CARBON_SERVICES_LEASE_TTL:-21600}
LEASE_INIT_GRACE=10
LOCK_TRIES=${CARBON_SERVICES_LOCK_TRIES:-5}
LOCK_STALE=${CARBON_SERVICES_LOCK_STALE:-120}
KNOWN_SERVICES="pg2a pg2b redis"
LOCKS=""

usage() {
    echo "usage: services_reap.sh [pg2a|pg2b|redis ...]" >&2
}

die() {
    echo "services_reap: $*" >&2
    exit 1
}

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

SELECTED=""
for arg in "$@"; do
    case "$arg" in
        -h|--help)
            usage
            exit 0
            ;;
        -*)
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
            echo "services_reap: unknown service: $arg" >&2
            usage
            exit 2
            ;;
    esac
done
[ -n "${SELECTED# }" ] || SELECTED=" $KNOWN_SERVICES"

# Acquire the lease for the duration of a reap so a concurrent run cannot
# start or join the service while it is being removed. 0 acquired, 1 live
# owner, 2 unusable state dir.
lease_acquire() {
    _lease_svc=$1
    _lease_ld=$(lease_dir "$_lease_svc")
    mkdir -p "$STATE_DIR" 2>/dev/null || return 2
    _lease_try=0
    while [ "$_lease_try" -lt 3 ]; do
        if mkdir "$_lease_ld" 2>/dev/null; then
            printf '%s\n' "$$" > "$_lease_ld/pid"
            printf '%s\n' "$(date +%s)" > "$_lease_ld/started"
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

# -- per-service reap lock ---------------------------------------------------
# mkdir is the atomic acquisition primitive. The lock is separate from the
# lease: it only serialises reapers and lease acquisitions during recheck, so
# a live owner that acquires the lease after our first look cannot be removed.

lock_dir() {
    printf '%s/%s.reap.lock\n' "$STATE_DIR" "$1"
}

lock_age() {
    _lock_ld=$1
    _lock_now=$(date +%s)
    _lock_ts=""
    if [ -f "$_lock_ld/started" ]; then
        _lock_ts=$(sed -n '1p' "$_lock_ld/started" 2>/dev/null || true)
    fi
    case "$_lock_ts" in
        ''|*[!0-9]*)
            _lock_ts=$(stat -c %Y "$_lock_ld" 2>/dev/null || printf '%s' "$_lock_now")
            ;;
    esac
    printf '%s\n' "$((_lock_now - _lock_ts))"
}

lock_acquire() {
    _lock_svc=$1
    _lock_ld=$(lock_dir "$_lock_svc")
    mkdir -p "$STATE_DIR" 2>/dev/null || return 1
    _lock_try=0
    while [ "$_lock_try" -lt "$LOCK_TRIES" ]; do
        if mkdir "$_lock_ld" 2>/dev/null; then
            printf '%s\n' "$$" > "$_lock_ld/pid"
            printf '%s\n' "$(date +%s)" > "$_lock_ld/started"
            LOCKS="$LOCKS $_lock_svc"
            return 0
        fi
        if [ "$(lock_age "$_lock_ld")" -gt "$LOCK_STALE" ]; then
            rm -rf "$_lock_ld" 2>/dev/null || true
        fi
        sleep 1
        _lock_try=$((_lock_try + 1))
    done
    return 1
}

lock_release() {
    _lock_ld=$(lock_dir "$1")
    rm -rf "$_lock_ld" 2>/dev/null || true
    return 0
}

release_locks() {
    for _lock_svc in $LOCKS; do
        lock_release "$_lock_svc"
    done
}
trap release_locks EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

command -v docker >/dev/null 2>&1 || die "docker not found on PATH"
docker info >/dev/null 2>&1 || die "docker engine unavailable; refusing to reap"

removed=0
kept=0
for svc in $SELECTED; do
    if ! lock_acquire "$svc"; then
        echo "keep $svc: reap lock busy (another reaper); not removed" >&2
        kept=$((kept + 1))
        continue
    fi
    if lease_live "$svc"; then
        echo "keep $svc: live lease ($(lease_describe "$svc"))"
        lock_release "$svc"
        kept=$((kept + 1))
        continue
    fi
    # Take the lease for the duration of the removal. This is stronger than
    # the recheck alone: a concurrent run's lease_acquire now fails instead of
    # starting a container that this reap is about to remove.
    if ! lease_acquire "$svc"; then
        echo "keep $svc: lease acquired by another run"
        lock_release "$svc"
        kept=$((kept + 1))
        continue
    fi
    if ! docker inspect "$svc" >/dev/null 2>&1; then
        echo "absent $svc: no container (ok)"
        lease_release "$svc"
        lock_release "$svc"
        continue
    fi
    docker stop -t 10 "$svc" >/dev/null 2>&1 || true
    docker rm "$svc" >/dev/null 2>&1 || true
    if docker inspect "$svc" >/dev/null 2>&1; then
        lease_release "$svc"
        lock_release "$svc"
        die "failed to remove $svc"
    fi
    # Clear only after the container is successfully gone.
    lease_release "$svc"
    echo "removed $svc: no live lease"
    lock_release "$svc"
    removed=$((removed + 1))
done

echo "services_reap: removed=$removed kept=$kept"
exit 0
