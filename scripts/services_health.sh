#!/bin/sh
# Probe the host test services without creating or starting anything.
# Usage: services_health.sh [--app-url URL] [pg2a] [pg2b] [redis]
#   no service names -> all three
#   --app-url    optional app-level HTTP health URL (also $CARBON_APP_HEALTH_URL)
#
# Prints one machine-readable line per service with infra and app as separate
# fields:
#   <name> host=.. port=.. database=.. state=.. infra=.. app=..
# state is healthy|starting|unhealthy|stopped|missing; infra is ok|fail (the
# pg_isready/PONG and loopback checks); app is ok|fail|skip (HTTP probe, any
# non-2xx/3xx response such as 500 is fail; skip when no URL is configured).
# Credentials are never printed. Exits non-zero unless every selected service
# is healthy and the app probe (when configured) is ok.
set -eu

usage() {
    echo "usage: services_health.sh [--app-url URL] [pg2a|pg2b|redis ...]" >&2
}

command -v docker >/dev/null 2>&1 || {
    echo "services_health: docker not found on PATH" >&2
    exit 3
}

APP_URL=${CARBON_APP_HEALTH_URL:-}
SELECTED=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --app-url)
            shift
            [ "$#" -gt 0 ] || { usage; exit 2; }
            APP_URL=$1
            shift
            ;;
        --app-url=*)
            APP_URL=${1#*=}
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "services_health: unknown flag: $1" >&2
            usage
            exit 2
            ;;
        pg2a|pg2b|redis)
            case " $SELECTED " in
                *" $1 "*) ;;
                *) SELECTED="$SELECTED $1" ;;
            esac
            shift
            ;;
        *)
            echo "services_health: unknown service: $1" >&2
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

emit() {
    svc=$1
    state=$2
    app_state=$3
    case "$svc" in
        pg2a)
            host="127.0.0.1"; port="5432"; database="perfume_store"
            ;;
        pg2b)
            host="127.0.0.1"; port="5433"; database="perfume_store"
            ;;
        redis)
            host="127.0.0.1"; port="6379"; database="0"
            ;;
    esac
    case "$state" in
        healthy) infra="ok" ;;
        *) infra="fail" ;;
    esac
    printf '%s host=%s port=%s database=%s state=%s infra=%s app=%s\n' \
        "$svc" "$host" "$port" "$database" "$state" "$infra" "$app_state"
}

check_state() {
    svc=$1
    if ! docker inspect "$svc" >/dev/null 2>&1; then
        echo "missing"
        return 0
    fi
    running=$(docker inspect -f '{{.State.Running}}' "$svc" 2>/dev/null || echo "false")
    if [ "$running" != "true" ]; then
        echo "stopped"
        return 0
    fi
    health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$svc" 2>/dev/null || echo "none")
    case "$health" in
        starting)
            echo "starting"
            return 0
            ;;
        unhealthy)
            echo "unhealthy"
            return 0
            ;;
        none|healthy)
            ;;
        *)
            echo "unhealthy"
            return 0
            ;;
    esac
    case "$svc" in
        pg2a|pg2b) cport=5432 ;;
        redis) cport=6379 ;;
    esac
    published=$(docker port "$svc" "$cport" 2>/dev/null | head -n 1 || true)
    case "$published" in
        127.0.0.1:*) ;;
        *)
            echo "unhealthy"
            return 0
            ;;
    esac
    case "$svc" in
        pg2a|pg2b)
            if ! docker exec "$svc" pg_isready -U perfume -d perfume_store >/dev/null 2>&1; then
                echo "unhealthy"
                return 0
            fi
            ;;
        redis)
            pong=$(docker exec "$svc" redis-cli ping 2>/dev/null || true)
            if [ "$pong" != "PONG" ]; then
                echo "unhealthy"
                return 0
            fi
            ;;
    esac
    echo "healthy"
}

# App-level probe: a reachable endpoint must answer 2xx/3xx. Any 4xx/5xx
# (including 500) or a transport error is fail. The URL is never printed.
check_app() {
    if [ -z "$APP_URL" ]; then
        echo "skip"
        return 0
    fi
    if ! command -v curl >/dev/null 2>&1; then
        echo "fail"
        return 0
    fi
    code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "$APP_URL" 2>/dev/null || true)
    case "$code" in
        2??|3??) echo "ok" ;;
        *) echo "fail" ;;
    esac
}

app_state=$(check_app)
failures=0
for svc in "$@"; do
    state=$(check_state "$svc")
    emit "$svc" "$state" "$app_state"
    if [ "$state" != "healthy" ]; then
        failures=$((failures + 1))
    fi
done

if [ "$app_state" = "fail" ]; then
    failures=$((failures + 1))
fi

if [ "$failures" -ne 0 ]; then
    exit 1
fi
