# Test-runner runbook — scoped test and probe execution

Canonical paths: `scripts/audit_test_runner.sh`,
`scripts/services_run.sh`, `scripts/services_health.sh`,
`scripts/test_services_lease.sh`.
Contract: `files/policies/gate.json` (engines, test-infra paths),
`files/policies/ci.json` (timeout), `files/policies/concurrency.json`
(pool and probe caps); escalation in `files/policies/escalation.json`.

## Purpose

Run one labelled suite or probe batch with a scoped service lifecycle: start
only what the run needs, keep the runner's own summary intact, and tear down
only what this run started. Never manufacture a green by piping the output.

## Commands

```sh
bash scripts/audit_test_runner.sh -Workdir <tree> -Label <tests> -Engine sqlite
bash scripts/audit_test_runner.sh --workdir <tree> --label all --engine postgres
./scripts/services_run.sh --services pg2a,redis -- ./run_suite.sh
./scripts/services_health.sh --app-url http://127.0.0.1:8000/health
sh scripts/test_services_lease.sh
```

## Inputs

- worktree and label; engine (`sqlite`/`postgres`) from the gate policy
- selected services and optional `--pull` for a fresh host
- app health URL (optional; a 500 is unhealthy)

## Outputs

- runner output plus its exact summary line and `AUDIT_RUNNER_EXIT=<code>`
- `SERVICES_STARTED=` / `SERVICES_STOPPED=` for the scoped lifecycle
- health lines with separate `infra=` and `app=` fields (no credentials)

## Failure modes

- Docker engine down → `services_run.sh` tries `systemctl`/`service`; if it
  still fails, exit non-zero; do not start containers by hand.
- Missing pinned image without `--pull` → fail closed; owner provisions it.
- Live lease held by another run → refusal; never kill or join it.
- Missing/unparsed summary → `UNMEASURED`; do not treat the exit code as green.
- `500` from the app probe or a transport error → unhealthy, exit 1.
- A probe whose evidence write fails is a failed probe: `factory probe` refuses
  rather than returning records that were not persisted. One
  `evidence/probe/NNNN-*.json` record exists per distinct execution.

## Escalate

`quality-lead` owns acceptance/flaky-suite decisions, `agent-platform` owns
runner infrastructure, per `files/policies/escalation.json`. Capacity values
are linked, not repeated: `files/policies/concurrency.json`.
