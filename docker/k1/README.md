# K1 app image build context (canonical: files/docker/k1/)

Small, hardening-oriented runtime for one K1 sandbox run. The image contains
**no application code**: the sealed repo is mounted read-only at `/repo/base`
and `/repo/head` by the typed `k1.up` tool, and app code is imported from
`/repo/head/backend` (`PYTHONPATH`).

Contents:

| File | Purpose |
|---|---|
| `Dockerfile` | multi-stage, pinned `python:3.14-slim@sha256:0514bc…bee9b1e1`, non-root uid/gid 10001, read-only-rootfs runtime |
| `compose.yaml` | hardened runtime shape; pg pinned `postgres:17-alpine@sha256:b0f956…2b24`, internal network, no published ports, all app writes on tmpfs (`/tmp`,`/run`,`/work`,`/home/k1`), `/work` exec |
| `image-pins.json` | both digests + local-inspect source; F-13 evidence |
| `zap-baseline` | non-interactive ZAP baseline wrapper installed at `/usr/local/bin/zap-baseline` (passive scan, JSON report, no network/updates) |
| `probe_client.py` | baked into image at `/opt/k1/probe_client.py` (stdlib only, 127.0.0.1:8081) |
| `HARDENING.md` | F-13 reconciliation, canonical-path note |
| `fake_provider.py` | stdlib fake payment provider on `127.0.0.1:8081`, deterministic ids, call log at `/work/provider-calls.jsonl` |
| `entrypoint.sh` | starts the provider, waits for `/health`, `exec "$@"` |
| `seed.py` | idempotent PII-free SQLite/Postgres fixture seeder |

## Build (owner/FDE only)

From the repo root, with no pull and no network:

```
K1_OWNER_BUILD=1 scripts/k1_build.sh
# equivalent:
docker build --pull=false -t k1-app:latest files/docker/k1
```

The build stage installs the pinned runtime dependencies once; the runtime
stage copies the result and never installs anything. The typed `k1.up` tool,
`entrypoint.sh`, and `compose.yaml` never run pip, apt, npm, or a compose
build. See `files/runbooks/k1.md` for the no-install guarantee and probes.

The auditor and hacker never build or pull `k1-app:latest`. If it is absent,
`k1.up` refuses with `IMAGE_MISSING` and prints this command.

`FROM python:3.14-slim@sha256:0514bc76712d64f8bb48adcb95b1ab0d88c69fee95d62fe4c18ee948bee9b1e1`
is pinned; verified 2026-11-08 against the local image (Id = RepoDigest, 178 MB).
The Postgres sidecar is pinned in `compose.yaml` to
`postgres:17-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24`
(local Id = RepoDigest, 423 MB). See `image-pins.json`.

Validated: `python:3.14-slim` pin was already done in the 6B-7 worktree
(`docker/k1/Dockerfile` + `image-pins.json` python entry) and is now unified
into this canonical `Dockerfile`.

## Runtime

Started only by the typed `k1.up` tool as `k1-<RunId>-app` on the internal
`k1-<RunId>-net`, with a read-only rootfs, non-root uid/gid 10001, cap-drop
ALL, no-new-privileges, no published ports, and tmpfs for `/tmp` (32m),
`/run` (8m), `/work` (128m, exec), and `/home/k1` (8m). The only writable bind
is `/repo/artifacts` (host `files/quality/k1/<run-id>/`) for tests and
evidence that must survive teardown; `/repo/base` and `/repo/head` are sealed
read-only git archives, never a worktree. Limits: app 768 MB / 1.0 CPU / 256
pids; postgres 256 MB / 0.5 CPU with tmpfs PGDATA at 96m. Or
`docker compose -f files/docker/k1/compose.yaml up` (manual; `k1.up` is the
audited path). The default `CMD` is `sleep infinity`; probes run through
the typed `k1.exec` tool.

Postgres runs with `-c fsync=off -c synchronous_commit=off -c
full_page_writes=off -c shared_buffers=32MB -c max_connections=40` in both
`compose.yaml` and the typed `k1.up` handler, because PGDATA is disposable tmpfs.
See `files/runbooks/k1.md`.

`seed.py` defaults to SQLite at `/work/sqlite/k1.sqlite3`. Postgres seeding
(`--database-url postgresql://...`) uses the pinned `psycopg[binary]` already
present in `requirements.lock` and baked into the image.

## Scanner toolchain (K1-DEPS)

Everything below is installed at **image build time only** (no pip/apt/npm at
runtime) and resolved through `PATH`, so `files/tools/gate_run.py` never needs
an absolute path:

| Tool | Path | Pinned version | Gate check |
|---|---|---|---|
| `schemathesis` (`st`) | `/usr/local/bin/schemathesis` | 4.33.4 (+ hypothesis 6.170.15) | SCHEMA_FUZZ |
| `zizmor` | `/usr/local/bin/zizmor` | 1.32.0 | ZIZMOR |
| `zap` / `zap.sh` | `/usr/local/bin/zap` → `/opt/zap/zap.sh` | ZAP 2.17.0 | DAST_BASELINE |
| `zap-baseline` | `/usr/local/bin/zap-baseline` | build-context wrapper | DAST_BASELINE |
| Java | `/opt/java/bin/java` | Temurin JRE 21.0.13+9 | ZAP runtime |
| `bandit` | `/usr/local/bin/bandit` | 1.9.4 | security scan |
| `radon` | `/usr/local/bin/radon` | 6.0.1 | smell (also importable) |

ZAP and the JRE are downloaded at build time and every archive is checked
against a pinned sha256 in the `Dockerfile`; the image build fails on mismatch.
ZAP's writable home is `/work/zap` (`ZAP_WORK_DIR`), because the runtime rootfs
is read-only; update checks and add-on downloads are disabled, so DAST needs no
egress. `zap-baseline` runs `zap.sh -cmd -quickurl <target> -quickout <json>`
(spider + passive scan) and accepts the official `-t`/`-J`/`-I` flags.

Exact commands `gate_run.py` runs, with cwd at the sealed tree:

```
schemathesis run <spec> --url <base_url> --max-examples N --no-color
zizmor <workflow.yml...> --format json
zap-baseline -t <target> -J <report.json> -I
```

Missing binaries are not a pass: each executor returns a named `NOT_RUN` fault
(`TOOL_ABSENT`) when the tool is absent from `PATH`.

The app container limit is 768 MB / 1 CPU / 256 pids and `/work` is 128 MB
(tmpfs) so a ZAP scan (`-Xmx320m` default) fits beside the app.


## Path note

Canonical path is `files/docker/k1/` per `files/K1-CONTAINER-PLAN.md` §8 and
`files/policies/policy-manifest.json` (`docker:files/docker/k1/*`).
`docker/k1/` at the repo root is now a stub README only.
