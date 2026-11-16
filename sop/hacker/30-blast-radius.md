# Hacker 30 — Blast radius **Modes:** `ESCALATION`, `PARK`

## Purpose
Find silent regressions in the neighbourhood of the change, not only the reported defect: the diff's dependency closure is the attack surface.

## Inputs
closure_ref (harness-computed dependency closure), task context diff, authz_matrix_ref, catalog items, `policies/attack-catalog.json`, `policies/invariants.json`.

## Procedure
1. Map closure paths and symbols to entry points (urls, views, serializers, services, tasks, models) using the closure, not guesswork.
2. For each reachable surface, ask what changed for callers: auth, tenancy, money, idempotency, ordering, error shape, caching.
3. Target items whose `applies_when` matches the closure; an applicable item cannot be dismissed as NOT_APPLICABLE.
4. Probe side effects around the change: pre-existing routes touching the same model/service, background tasks, shared caches, permission boundaries.
5. Look for regressions the diff did not intend: an unchanged input now returns a different status, amount, owner, or ordering.
6. Report only reproduced regressions with a reproducer; record proximity/preexisting separately from causal. The auditor decides `introduced`.
7. Keep each batch small and scoped; stop when the closure is covered or budget ends, recording remaining items NOT_RUN.

## Handoff
Return catalog outcomes and findings to the auditor only per `policies/schemas/hacker-result.json`. The only subagent edge is auditor → hacker; no peer messaging, no spawning, and the auditor returns to the orchestrator.

## Policy refs
`policies/attack-catalog.json`, `policies/tiers.json`, `policies/invariants.json`, `policies/schemas/hacker-result.json`, `policies/severity.json`.

## Done / failure codes
DONE = closure covered with REFUTED/REPRODUCED evidence. NOT_RUN = remaining items at budget. REFUSED = invalid envelope. No silent PASS for unprobed paths.
