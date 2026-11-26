# Gates and shared test resources

This is the operational answer to two questions: what actually runs a gate, and
how the shared PostgreSQL/SQLite services are kept from colliding.

For what a failing check *means* — invariants versus hygiene deltas versus
declared debt and audited exceptions — see `docs/GATE-HIERARCHY.md`.

## What triggers a gate

There is no daemon, cron job, or CI hook that runs the factory gate. A gate is
an **agent-invoked CLI command**:

```
bin/factory gate --task-id <TASK> [--release] [--json]
```

1. `factory/gate.py::run_gate` loads the task (`~/.factory/tasks/<task>/task.json`).
2. It resolves the gate set from `policies/gate.json` (`gate_sets_by_kind[kind]`,
   or the `release` set with `--release`). The in-code tables in `gate.py` are a
   fallback only; the JSON file is authoritative.
3. Checks run **sequentially in one process**, one OS process per check:
   `factory.proof.run_check` invokes `python -m factory.checks <CHECK>` with a
   JSON context (task, worktree, base/head, scope, tier, env).
4. Each proof is written to `~/.factory/evidence/<task>/proofs/<CHECK>.json`,
   then the gate computes `proof_root` and writes
   `~/.factory/evidence/<task>/gate.json`.
5. A check in `policies/check-runners.json` with `"shadow": true` is recorded
   but not required. `--strict-shadow` makes shadows blocking; release gates
   are not narrowed (`--checks` is refused with `--release`).
6. `factory verify --task-id <TASK>` re-hashes the proofs and the live policy
   and refuses if either moved. `factory release` additionally requires
   `phase: GATE`, `mode: RELEASE`, a PASS verdict, and the deletion guard.

Who calls it: the auditor during a BUILD audit (`sop/auditor/10-audit.md`), and
the release-engineer/orchestrator at the release boundary
(`sop/release-engineer/10-promote.md`, `sop/orchestrator/50-release.md`). The
orchestrator never computes proofs itself; it reads them.

Product CI (`.github/workflows/**` in the product repo) is a separate,
pre-merge signal. A green factory gate does not replace `gh pr checks`, and a
green CI run does not replace a proof.

## Shared services

| Service | Port | Data | Limit (after this change) |
| --- | --- | --- | --- |
| `pg2a` | 127.0.0.1:5432 | tmpfs, fsync off | 1 GB, 2.0 CPU, 1.5 GB swap cap, 512 MB data tmpfs, 128 MB shm, `max_connections=100` |
| `pg2b` | 127.0.0.1:5433 | tmpfs, fsync off | same |
| `redis` | 127.0.0.1:6379 | tmpfs | 96 MB, 0.25 CPU |
| K1 app | internal only | read-only rootfs | 768 MB, 1.0 CPU |
| K1 pg | internal only | tmpfs, fsync off | 512 MB, 1.0 CPU, 768 MB swap cap, `max_connections=100`, `work_mem=2MB` |

Lane assignment (`policies/concurrency.json`):

- `lane/a` -> pg2a (:5432)
- `lane/b` -> pg2b (:5433)
- another lane/task -> a stable hash picks one, so it is deterministic per task

K1 is the exception: each K1 run has its own container network and its own
PostgreSQL, so a K1 campaign never shares a server with a host suite.

## Why executions used to collide

Three independent causes, all now closed:

1. **One fixed test database name.** Both Django (`LockedDiscoverRunner`) and
   pytest-django derived `test_perfume_store` from the same `DATABASE_URL`.
   Django took a file lock keyed on that database; pytest-django does not use
   `TEST_RUNNER`, so it created/dropped the same database outside the lock. One
   run's `DROP DATABASE` killed the other run's connections (`FATAL: database
   "test_perfume_store" does not exist`, `CREATE DATABASE ... already exists`).

   Fix: every harness invocation now names its own base database
   (`factory_<token>`), so Django derives `test_factory_<token>` and
   pytest-django derives a different one. The names cannot meet.

2. **Every run landed on pg2a.** `_postgres_target` probed :5432 first and only
   fell back to :5433, so pg2b sat idle while pg2a serialized every suite.

   Fix: lane-to-server assignment, with pg2b used for `lane/b`.

3. **0.5 CPU / 256 MB / 40 connections / no swap.** A 2,600-test suite plus
   concurrent-writer probes on that server produced connection failures and
   OperationalErrors that looked like application bugs.

   Fix: the limits above, plus a per-server slot lease.

## The slot lease

`factory/checks/__init__.py::resource_lease` takes an OS advisory lock on
`~/.factory/locks/postgres-<port>.slot<N>.lock` before a DB-backed check starts
a server-side run. `policies/concurrency.json` sets `slots_per_server: 1` and
`wait_s: 1800`: a second run **waits** for the slot instead of colliding, and a
caller that waits past the deadline fails with a named `RESOURCE_BUSY` rather
than corrupting a database. The lock is released by the kernel when the process
exits, so a killed run cannot wedge the pool.

## Why SQLite race probes errored

SQLite allows one writer. A probe that opens N>1 concurrent writers raises
`OperationalError: database is locked`, which the app reports as a debug 500.
That is an **engine fault, not an application gate**. Concurrent-writer classes
are therefore PostgreSQL-only in `policies/attack-catalog.json#/engine_policy`;
`tools/probe_audit.py` refuses to count an execution on an ineligible engine and
prints the classes that must move to PostgreSQL.

## Inspecting it

```bash
docker ps --format '{{.Names}} {{.Status}} {{.Ports}}'
bin/factory services status
python tools/probe_audit.py --records evidence/probe --variant max --item RACE-ORDERS
ls ~/.factory/locks
```

`docker/k1/compose.yaml` and `docker/services/compose.yaml` are the only
definitions of the limits; do not restate them in a SOP.
