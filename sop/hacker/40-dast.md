# Hacker 40 — DAST baseline and schema fuzz **Modes:** `ESCALATION`, `PARK`

## Purpose
Run the tier's dynamic checks at the tree so runtime misconfiguration, injection classes, and request/response shape breaks are measured, not assumed.

## Inputs
Task context (tier, tree, engine); check registry; `policies/attack-catalog.json`; `policies/schema-fuzz.json`; K1 runtime provisioned by the campaign parent; `policies/tiers.json` (DAST_BASELINE applies to max).

## Real tools and paths
- `factory check DAST_BASELINE` runs ZAP (`zap` / `zap.sh`) against the live K1 target. DAST_BASELINE is required only at max.
- `factory check SCHEMA_FUZZ` runs schemathesis against the runtime OpenAPI spec for the routes in `policies/schema-fuzz.json#scope`.
- The K1 image contains both, at the pinned versions in `policies/check-runners.json` and the K1 image: the `zap-baseline` wrapper for ZAP, and schemathesis via the pinned requirements. No other scanner exists; do not name one.
- With no live K1 target, `factory check` rejects the call (REJECT_PRECONDITION). That is a fault: record `NOT_RUN`, never PASS.

**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:hacker`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

## Procedure
1. Check applicability: DAST_BASELINE only where `policies/tiers.json`/`policies/checks.json` says so; SCHEMA_FUZZ where it is in the tier's required set. Otherwise record check outcome `NOT_RUN` and cite the non-applicability in `evidence_ref`; never PASS.
2. Confirm the K1 runtime is up (the campaign parent runs `factory k1 up`; this role does not manage K1).
3. Run `factory check DAST_BASELINE` and/or `factory check SCHEMA_FUZZ` and capture the result envelope.
4. Parse the scan: ZAP alerts above the policy threshold are DAST_FINDING; schemathesis failures are SCHEMA_FAILURE, including undeclared 3xx and 5xx. Informational alerts are residual, not a pass claim. A 429 is recorded per `policies/schema-fuzz.json#status_classification`, not a failure.
5. A missing target, refused scan, or error is NOT_RUN with a fault ref — never an invented clean scan.
6. Do not scan production, external hosts, or any endpoint outside the K1 network.
7. Attach the result ref; the campaign parent assigns severity from `policies/severity.json`.

## Attempt accounting
One distinct execution is one attempt: a distinct `(tree_sha, check_id, input or route-set hash, command)` run. A rerun of the same tree, input, and command is the same attempt, not a new one; re-emitting the same evidence does not reset it. Both DAST_BASELINE (ZAP) and SCHEMA_FUZZ (schemathesis) draw against the tier's cap in `policies/schema-fuzz.json#attempts` (`max_attempts_by_tier[tier]`, unit `distinct_execution`). Never hard-code or infer the cap. When it is exhausted, record `NOT_RUN` with the attempt log ref and the last error; never PASS, never retry wider, and never rotate lanes to reset the count.

## Artifact channel
Tool configs live on tmpfs (`/tmp`, `/run`, `/work`, `/home/k1`) and are discarded at teardown; never treat them as evidence. Write scan/exploit output and evidence only to the mounted `/repo/artifacts`, which is the host `files/quality/k1/<run_id>/` for this run. The tree (`/repo/base`, `/repo/head`) is read-only: never write a test, config, or evidence file into it.

## Handoff
Return the DAST/SCHEMA_FUZZ outcomes to the campaign parent only, inside `policies/schemas/hacker-result.json` / the check result. The campaign parent is the release-engineer for the release-train campaign at GATE and the auditor in a `BUG_FINDING` sweep; no peer messaging, and the parent returns the folded result to the orchestrator.

## Policy refs
`policies/tiers.json`, `policies/checks.json`, `policies/attack-catalog.json`, `policies/schema-fuzz.json`, `policies/gate.json`, `policies/severity.json`, `policies/redaction.json`.

## Done / failure codes
DONE = scan/fuzz result recorded (PASS/FAIL findings). NOT_RUN = refused/error/missing target or exhausted attempts. Where policy excludes the tier, record `NOT_RUN` and cite it in `evidence_ref`. Never PASS without a factory check result.
