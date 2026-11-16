#!/bin/sh
# K1 app entrypoint: start the fake payment provider, wait for /health, then
# exec the container command. POSIX sh; no bash required.
#
# Leanness: this script installs nothing. Dependencies (Django, gunicorn,
# psycopg, ...) are baked into the image at build time by the Dockerfile build
# stage. It never calls pip, apt, npm, or any package manager, and it writes
# only under the tmpfs-backed /work and /tmp.
set -eu

PROVIDER=/opt/k1/fake_provider.py

mkdir -p /work/artifacts /work/sqlite

python "$PROVIDER" &
provider_pid=$!

stop_provider() {
    if kill -0 "$provider_pid" 2>/dev/null; then
        kill -TERM "$provider_pid" 2>/dev/null || true
        wait "$provider_pid" 2>/dev/null || true
    fi
}
trap stop_provider TERM INT

ready=0
i=0
while [ "$i" -lt 100 ]; do
    if python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8081/health')" >/dev/null 2>&1; then
        ready=1
        break
    fi
    if ! kill -0 "$provider_pid" 2>/dev/null; then
        echo "k1-entrypoint: fake provider exited during startup" >&2
        exit 1
    fi
    i=$((i + 1))
    sleep 0.1
done

if [ "$ready" -ne 1 ]; then
    echo "k1-entrypoint: fake provider health timeout" >&2
    stop_provider
    exit 1
fi

# exec keeps the app as PID 1. The trap above covers the startup window; after
# exec, container teardown (docker rm -f) reaps the background provider.
exec "$@"
