# Auditor 20 — Checks and outcomes
**Mode:** `AUDIT`

## Purpose
Run the tier's check set with correct semantics and honest outcome states, so the verdict is computable from evidence.

## Inputs

task context (tier, head, tree, budget); `policies/tiers.json`; `policies/checks.json`; the tier's `check_conditions`; the check-runner registry; `policies/schemas/fault-codes.json`.

**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:auditor`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

## Procedure
1. Select exactly `policies/tiers.json[tier].required_checks`; per-check definitions come from `policies/checks.json`. Sharding is allowed; the union of shard results is the result.
2. Execute at task context via `factory check <CHECK>` or `factory gate` where the definition names it.
3. Map outcomes: PASS (predicate held), FAIL (predicate violated), NOT_RUN (could not execute; attach fault_code + evidence). BLOCKED is not a check outcome: it is a pre-run/relay stop (e.g. K1_UP_FAILED) or a refusal, never placed in an enum field.
4. Apply `check_conditions`. When a condition says the check does not apply, record check outcome `NOT_RUN` and cite the condition result in `evidence_ref` (the check schema has no applicability field); never substitute PASS for skipped work.
5. Respect the shadow policy: shadow checks record an outcome but do not alter the verdict.
6. Cache only on exact (tree, check_id, check_impl_sha, engine); cached results count with cached:true.
7. Retry only transient faults listed in `policies/schemas/fault-codes.json`, up to `policies/tiers.json[tier].fault_retries`; exhausted → NOT_RUN.
8. Record each outcome with evidence_ref/sha256; NOT_RUN requires the real fault evidence hash, never zeros.
9. For a hacker catalog outcome, verify the per-probe records against `policies/attack-catalog.json`: every returned `probe-record/1` hash resolves in `evidence/probe/`, REFUTED has at least one blocked probe in every listed probe_class and at least `min_probes[variant]` distinct blocked probes, and a claimed NOT_APPLICABLE on an applicable item becomes NOT_RUN. A caller-supplied probes array without evidence store records is not evidence. A concurrent-writer probe that hit a `sqlite` `OperationalError` is `ENGINE_FAULT`/`NOT_RUN` per `policies/attack-catalog.json#/engine_policy`, never coverage and never `REFUTED`.

## Handoff
Return outcomes inside the auditor verdict to the orchestrator. Hacker outcomes return through this auditor; do not message peers.

## Policy refs
`policies/tiers.json`, `policies/checks.json`, `policies/gate.json`, `policies/negative-controls.json`, `policies/schemas/fault-codes.json`, `policies/schemas/auditor-verdict.json`.

## Done / failure codes
DONE = every required check has PASS/FAIL/NOT_RUN evidence. INCONCLUSIVE when any required check is NOT_RUN (V2). BLOCKED = pre-run stop (K1_UP_FAILED, missing tool) with the exact error.
