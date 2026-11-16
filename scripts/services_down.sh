#!/bin/sh
# Stop and remove only the named host test-service containers.
# Usage: services_down.sh [pg2a] [pg2b] [redis]
#   no service names -> all three
# Never touches unrelated containers, the compose network, or tmpfs data
# outside these three names. One lease protocol, shared with
# services_run.sh/services_reap.sh: a live lease is never stopped or removed
# (refused, non-zero), a stale lease is cleared only after the container is
# gone, a failed removal keeps the lease in place, and the same per-service
# reap lock plus a transient lease cover the live-lease check and the removal.
set -eu

STATE_DIR=${CARBON_SERVICES_STATE_DIR:-/tmp/carbon-services}
LEASE_TTL=${CARBON_SERVICES_LEASE_TTL:-21600}
LEASE_INIT_GRACE=10
LOCK_TRIES=${CARBON_SERVICES_LOCK_TRIES:-5}
LOCK_STALE=${CARBON_SERVICES_LOCK_STALE:-120}
LOCKS=""

usage() {
    echo "usage: services_down.sh [pg2a|pg2b|redis ...]" >&2
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

lease_describe() {
    _lease_ld=$(lease_dir "$1")
    _lease_pid=$(lease_pid "$1")
    _lease_age=$(lease_age "$_lease_ld")
    printf 'pid=%s age=%ss' "${_lease_pid:-?}" "$_lease_age"
}

# Acquire the lease for the duration of the removal (same protocol as the
# reaper), so a run cannot start or join the service between check and remove.
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
# Same lock file as services_reap.sh: the live-lease check and the removal are
# one critical section, so a run cannot acquire the lease in between.

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
    rm -rf "$(lock_dir "$1")" 2>/dev/null || true
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

command -v docker >/dev/null 2>&1 || {
    echo "services_down: docker not found on PATH" >&2
    exit 3
}

SELECTED=""
for arg in "$@"; do
    case "$arg" in
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "services_down: unknown flag: $arg" >&2
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
            echo "services_down: unknown service: $arg" >&2
            usage
            exit 2
            ;;
    esac
done

if [ -z "${SELECTED# }" ]; then
    SELECTED=" pg2a pg2b redis"
fi

# Validated allowlist only; see services_up.sh.
# shellcheck disable=SC2086
set -- $SELECTED

failures=0
for svc in "$@"; do
    if ! lock_acquire "$svc"; then
        echo "container $svc: reap lock busy (another reaper); refusing to remove" >&2
        failures=$((failures + 1))
        continue
    fi
    if lease_live "$svc"; then
        echo "container $svc: live lease ($(lease_describe "$svc")); refusing to remove" >&2
        failures=$((failures + 1))
        lock_release "$svc"
        continue
    fi
    # Take the lease for the check+remove critical section; a concurrent run's
    # acquire now fails instead of starting a container this down will remove.
    if ! lease_acquire "$svc"; then
        echo "container $svc: lease acquired by another run; refusing to remove" >&2
        failures=$((failures + 1))
        lock_release "$svc"
        continue
    fi
    if ! docker inspect "$svc" >/dev/null 2>&1; then
        echo "container $svc: absent (ok)"
        lease_release "$svc"
        lock_release "$svc"
        continue
    fi
    if docker stop -t 10 "$svc" >/dev/null 2>&1; then
        echo "container $svc: stopped"
    else
        echo "container $svc: stop failed (continuing)" >&2
    fi
    if docker rm "$svc" >/dev/null 2>&1 && ! docker inspect "$svc" >/dev/null 2>&1; then
        echo "container $svc: removed"
        # Clear only after the container is successfully gone.
        lease_release "$svc"
    else
        echo "container $svc: remove failed; lease kept" >&2
        failures=$((failures + 1))
    fi
    lock_release "$svc"
done
if [ "$failures" -ne 0 ]; then
    echo "services_down: incomplete ($failures service(s) not cleaned)" >&2
    exit 1
fi
echo "services down: $*"
