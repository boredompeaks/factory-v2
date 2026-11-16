# Host test services (pg2a, pg2b, redis)

**Dev/test stack only. Never use in production.** These containers hold
disposable data in tmpfs, run with `fsync=off`, publish unauthenticated
loopback ports, and use fixed local-only credentials. Everything is lost
when a container is removed or restarted.

## Purpose

Give agents (auditor/builder) a fast, scripted, loopback-only backend pair
and cache without touching the K1 run containers:

| Service | Image (pinned) | Host port | Database / user | Password (local only) |
| --- | --- | --- | --- | --- |
| `pg2a` | `postgres:17-alpine@sha256:b0f9560a...b24` | `127.0.0.1:5432` | `perfume_store` / `perfume` | `pg2a_local_test_only` |
| `pg2b` | `postgres:17-alpine@sha256:b0f9560a...b24` | `127.0.0.1:5433` | `perfume_store` / `perfume` | `pg2b_local_test_only` |
| `redis` | `redis:7-alpine@sha256:7b1753b4...4b7` | `127.0.0.1:6379` | db `0`, no auth | (none) |

Connection URLs:

- `postgresql://perfume:pg2a_local_test_only@127.0.0.1:5432/perfume_store`
- `postgresql://perfume:pg2b_local_test_only@127.0.0.1:5433/perfume_store`
- `redis://127.0.0.1:6379/0`

`scripts/audit_test_runner.sh` uses the pg2a URL unchanged for
`engine=postgres`; that exact contract must not change.

Pins live in `files/docker/services/image-pins.json`. Compose runs with
`--pull never`; images must already be present locally.

## Commands

All scripts are POSIX `sh`, take service names as argv (no shell, no
`eval`), and default to all three services when none are named.

```sh
# Start/ensure (idempotent; fails closed if an image is missing)
./scripts/services_up.sh
./scripts/services_up.sh pg2a redis
./scripts/services_up.sh --start-engine pg2a

# Owner-only explicit pull for provisioning a new host, then start
./scripts/services_up.sh --pull
./scripts/services_up.sh --pull pg2b

# Probe without creating/starting anything (exit non-zero if unhealthy)
./scripts/services_health.sh
./scripts/services_health.sh pg2a pg2b redis

# Stop and remove only these containers (tmpfs data is discarded)
./scripts/services_down.sh
./scripts/services_down.sh pg2a

# Reap orphaned containers: remove only when no live run lease exists
./scripts/services_reap.sh
./scripts/services_reap.sh pg2a redis
```

### Scoped test-runner lifecycle: `services_run.sh`

Test runners must not leave containers idle. `services_run.sh` starts only
the relevant services, runs one command, and tears down exactly what it
started on EXIT/INT/TERM (including failures and timeouts):

```sh
./scripts/services_run.sh --services pg2a -- sh -c 'pytest -q'
./scripts/services_run.sh --services pg2a,redis -- ./run_suite.sh
./scripts/services_run.sh --services pg2a --pull -- sh -c 'pytest -q'
```

- Ensures the Docker engine (starts it via `systemctl`/`service` if down;
  the engine stays running).
- Takes an atomic per-service lease before it starts or recreates a service
  (`$CARBON_SERVICES_STATE_DIR`, default `/tmp/carbon-services`; each lease
  is a `<service>.lease` directory holding `pid` and `started`).
- A running container with a **live** lease belongs to another run: this run
  refuses instead of joining it. Sharing a container under someone else's
  lease lets that owner tear it down mid-run, so there is no join path.
- A running container whose lease is missing, stale (owner PID gone), or
  expired (older than 6h) is orphaned: it is stopped, removed, and recreated
  fresh under this run. This is what cleans up after a killed or interrupted
  run whose trap never fired.
- Containers it created (or restarted from stopped) are stopped and
  removed in a trap; leases are released only after the container is gone.
- Never starts, removes, or joins a service while another live lease owns it;
  a live lease with a missing/stopped container is an error (refuses to race).
- Waits for `pg_isready` / redis `PONG` before running the command.
- Prints `SERVICES_STARTED=...` and `SERVICES_STOPPED=...`.
- Default selection when `--services` is omitted is `pg2a`.
- `--pull` applies only when a selected service is absent locally: it
  calls `services_up.sh --pull` for the pinned images. Without `--pull`
  the fail-closed `IMAGE_MISSING` path is kept (no implicit pull). Test
  runners use `--pull` when the pinned image is absent on a fresh host.

### Leases, stale reaping, and the manual reaper

Test runs create leases; `services_up.sh` (owner debug path) does not, so
containers it starts are unleased and may be reaped. The reaper takes a lease
only for the duration of a removal. Lease TTL defaults to 6h and can be
shortened only for testing with `CARBON_SERVICES_LEASE_TTL`; all scripts share
`CARBON_SERVICES_STATE_DIR`.

`services_reap.sh` is idempotent and safe to run manually (for example after
an factory CLI timeout, reload, or any job killed before its trap ran). It prints
one line per service and a `services_reap: removed=N kept=M` summary:

- live lease → `keep <svc>: live lease (pid=... age=...)`
- stale lease → removes the container under a per-service reap lock, then
  clears the lease
- no container → `absent <svc>: no container (ok)`
- with nothing running it exits 0 with `removed=0 kept=0`

The reaper takes a `<service>.reap.lock` (same state dir) before its recheck,
then acquires the service lease for the duration of the removal. A live owner
can neither hold the lease nor start the container between the recheck and the
removal. A held lock makes that service `keep ... reap lock busy`; a stale lock
is broken after `CARBON_SERVICES_LOCK_STALE` seconds.
`CARBON_SERVICES_LOCK_TRIES` bounds acquisition attempts. The lease is
released only after the container is gone (or was already absent).

`services_down.sh` refuses to stop or remove a service with a live lease
(non-zero, lease kept). Under the same per-service reap lock it takes a
transient lease for the removal, so a concurrent run cannot start or join the
container mid-teardown. It clears the lease only after the container is
successfully gone; a failed removal keeps both container and lease.

Health output is one machine-readable line per service. Credentials are never
printed; infra and app are separate fields:

```
pg2a host=127.0.0.1 port=5432 database=perfume_store state=healthy infra=ok app=ok
```

`state` is `healthy`, `starting`, `unhealthy`, `stopped`, or `missing`.
`infra` is `ok`/`fail` for the container, loopback, `pg_isready`/`PONG`
checks. `app` is `ok`/`fail`/`skip` for the optional HTTP probe
(`--app-url` or `CARBON_APP_HEALTH_URL`): a 500 (or any non-2xx/3xx or
transport error) is `fail`, and `skip` means no URL was configured. The
command exits non-zero unless every selected service is `healthy` and the app
probe is not `fail`.

Use `factory services ensure` to prepare required services:

```json
{"action": "ensure", "services": ["pg2a", "pg2b", "redis"], "wait_health": 60}
{"action": "health", "services": ["pg2a"]}
```

`ensure`/`start` verifies the engine (starting it if needed via
`systemctl`/`service`) and explicitly starts only the requested host
services, waiting up to `wait_health` seconds (default 60). They return
`engine_ready`, `started`, `services[]` (name, host, port, database,
user, password, url, state, started_by_tool) plus argv evidence.
`health` probes only and never creates or starts anything. Test runners
should use `services_run.sh` so containers are torn down after the run;
`factory services ensure` leaves explicitly requested services up for owner/debug
use. Those containers have no lease, so `services_reap.sh` will remove them
unless an owner debug session is still needed. K1 `app`/`pg` semantics
remain separate.

## Compose details

`files/docker/services/compose.yaml`:

- Loopback-published only: `127.0.0.1:<port>:<port>`; no `0.0.0.0`.
- tmpfs only, no volumes or named volumes:
  - pg: `/var/lib/postgresql/data:rw,nosuid,nodev,size=128m`
  - redis: `/data:rw,nosuid,nodev,size=64m`
- Postgres flags: `fsync=off`, `synchronous_commit=off`,
  `full_page_writes=off`, `shared_buffers=32MB`, `max_connections=40`,
  `effective_cache_size=128MB`, `work_mem=4MB`,
  `maintenance_work_mem=16MB`.
- Redis flags: `--save "" --appendonly no --maxmemory 64mb
  --maxmemory-policy allkeys-lru`.
- `restart: "no"`; `cap_drop: ALL` plus only the caps each image needs;
  `no-new-privileges`; log driver `local` capped at `1m` x 1 file.

### Why tmpfs + fsync off

Both postgres instances and redis store everything in RAM-backed tmpfs.
With `fsync=off`/`synchronous_commit=off`/`full_page_writes=off` (and
redis persistence disabled), writes avoid disk sync entirely. That makes
test suites and migrations much faster on a laptop and guarantees no
stale database survives the container. The trade-off is explicit:
**any crash or `services_down.sh` destroys all data**, which is the
intended behavior for scratch test databases.

### Resource limits

| Service | Memory | CPUs | shm | Other |
| --- | --- | --- | --- | --- |
| `pg2a` | 256 MB | 0.5 | 32 MB | pid/log caps via compose defaults |
| `pg2b` | 256 MB | 0.5 | 32 MB | pid/log caps via compose defaults |
| `redis` | 96 MB | 0.25 | default | maxmemory 64 MB, allkeys-lru |

## Health probes

- Postgres: container healthcheck `pg_isready -U perfume -d perfume_store`
  plus the script re-runs `docker exec <name> pg_isready ...` and checks
  the published port is `127.0.0.1:*`.
- Redis: container healthcheck `redis-cli ping`; script requires `PONG`.
- App (optional): `curl` against `--app-url`/`CARBON_APP_HEALTH_URL`; a 500 or
  any non-2xx/3xx response is unhealthy and exits non-zero.
- Missing/stopped containers report `missing`/`stopped`; the script exits
  non-zero when any selected service is not `healthy`.

Capacity for app pools and probe concurrency is one contract:
`files/policies/concurrency.json` (Postgres `max_connections` stays the
server-side cap). Do not restate or duplicate those numbers here.

Manual probes:

```sh
docker exec pg2a pg_isready -U perfume -d perfume_store
docker exec pg2b pg_isready -U perfume -d perfume_store
docker exec redis redis-cli ping
```

## Cleanup

```sh
# Manual stop/remove (stale leases for these services are cleared)
./scripts/services_down.sh

# Safe orphan reaper: removes only services with no live lease
./scripts/services_reap.sh
```

`services_down.sh` stops and removes only `pg2a`, `pg2b`, and `redis`; tmpfs
content is gone. It refuses a service with a live lease (non-zero, nothing
stopped) and clears a stale lease only after removal. It does not touch K1
containers or unrelated containers. `services_reap.sh` is the recovery path
when a test run was killed before its trap could run: it takes the per-service
reap lock, clears stale leases, and removes unleased containers, while never
removing a container with a live lease. The compose bridge network is left in
place (it is inert without the containers). To check state afterwards:

```sh
docker ps -a --filter name=pg2a --filter name=pg2b --filter name=redis
```

## Security notes

- Ports bind to loopback only; never change to `0.0.0.0`.
- Credentials are fixed and non-secret; they are local dev/test values.
- `no-new-privileges` and `cap_drop: ALL` are set; only the minimum caps
  needed for postgres/redis startup are added back.
- Do not put real data or production credentials in these containers.
