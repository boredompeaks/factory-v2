# K1 hardening — F-13 resolution (6B-7), unified 2026-11-08

**Status: PINNED (python + postgres), canonical path files/docker/k1/.**
`files/K1-CONTAINER-PLAN.md` says IMPLEMENTED and smoke-tested; this file
reconciles the F-13 pins. NO seal push was performed here — gate-control
record, SIGNING.md, and drain chain are unchanged; owner/FDE adds the MCP
server + registry + probe client to `gate_impl_digest` and records this pin
at the next seal (6C).

Canonical location (2026-11-08 unification):
- `files/K1-CONTAINER-PLAN.md` §8 lists `files/docker/k1/{Dockerfile,requirements.txt,fake_provider.py,seed.py,entrypoint.sh,README.md}`
  and build `docker build -t k1-app:latest files/docker/k1`.
- `files/policies/policy-manifest.json` records `docker:files/docker/k1/Dockerfile` etc.
- `docker/k1/` (6B-7 worktree) is now a stub; all 5 files preserved here.
- `files/FACTORY-POLICY-MAP.md` has no `docker/k1` mention; it points at
  `files/K1-CONTAINER-PLAN.md` for the K1 shape.

Evidence (F-13):

| F-13 item | Finding | Hardening |
| --- | --- | --- |
| Unpinned base | `FROM python:3.14-slim` tag-only | `Dockerfile` pinned to `python:3.14-slim@sha256:0514bc76712d64f8bb48adcb95b1ab0d88c69fee95d62fe4c18ee948bee9b1e1` (local Id = RepoDigest) |
| Postgres pin placeholder | `postgres:17-alpine@sha256:<pin>` placeholder | `compose.yaml` pg image pinned to `postgres:17-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24` (local Id = RepoDigest, 423 MB) |
| Postgres on 0.0.0.0 | pg2a/pg2b published `0.0.0.0:5432/5433` | K1 network is `internal: true` with **no published ports**; provider binds `127.0.0.1:8081`. pg2a/pg2b outside K1 and untouched |
| /work exec | `/work exec` in the plan | **exec retained**: `/work` must execute built artifacts. `compose.yaml` mounts `/work` `rw,exec,nosuid,nodev`; compensating controls are internal-only network, read-only rootfs, cap-drop ALL, no-new-privileges, non-root 10001. Probe client stays baked at `/opt/k1/probe_client.py` |
| Runtime installs | any per-run pip/apt/npm would mutate a read-only rootfs | Dependencies are baked once in the Dockerfile build stage; runtime stage does not install. `entrypoint.sh`, `compose.yaml`, and the typed `k1.up` tool run no package manager and no compose build. Build script: `scripts/k1_build.sh` (`--pull=false`) |
| Postgres durability cost | tmpfs PGDATA makes fsync pointless overhead | `-c fsync=off -c synchronous_commit=off -c full_page_writes=off -c shared_buffers=32MB -c max_connections=40` in `compose.yaml` and the typed `k1.up` handler; PGDATA tmpfs stays 96m |
| Resource drift | unbounded sandbox | app 768 MB / 1.0 CPU / 256 pids; pg 256 MB / 0.5 CPU; full tmpfs + artifact-bind layout in `files/runbooks/k1.md`. 768 MB and 128 MB `/work` are required for an in-image ZAP scan (`-Xmx320m`) beside the app |
| Docs drift | Plan says IMPLEMENTED, map says not ready | This file reconciles: IMPLEMENTED (runs) and PINNED (digests recorded 2026-11-08); gate seal still pending 6C (no seal push) |

Files (canonical, owned by 6B-7 + 2026-11-08 unification):
`files/docker/k1/Dockerfile`, `files/docker/k1/compose.yaml`,
`files/docker/k1/probe_client.py`, `files/docker/k1/image-pins.json`,
`files/docker/k1/HARDENING.md`, plus the 7 original context files
(`README.md`, `entrypoint.sh`, `fake_provider.py`, `seed.py`,
`requirements.txt`, `requirements.lock`).

`docker/k1/` is now a stub README only (preserves history pointer).
No factory worktrees or live agent files were touched; no pull/build run.
