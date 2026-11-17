# Hacker 30 — Blast radius **Modes:** `ESCALATION`, `PARK`

## Purpose
Find silent regressions in the neighbourhood of the change, not only the reported defect: the diff's dependency closure is the attack surface.

## Inputs
closure_ref (harness-computed dependency closure), task context diff, authz_matrix_ref, catalog items, `policies/attack-catalog.json`, `policies/invariants.json`.

**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:hacker`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

## Procedure
1. Map closure paths and symbols to entry points (urls, views, serializers, services, tasks, models) using the closure, not guesswork.
2. For each reachable surface, ask what changed for callers: auth, tenancy, money, idempotency, ordering, error shape, caching.
3. Target items whose `applies_when` matches the closure; an applicable item cannot be dismissed as NOT_APPLICABLE.
4. Probe side effects around the change: pre-existing routes touching the same model/service, background tasks, shared caches, permission boundaries.
5. Look for regressions the diff did not intend: an unchanged input now returns a different status, amount, owner, or ordering.
6. Report only reproduced regressions with a reproducer; record proximity/preexisting separately from causal. The auditor decides `introduced`.
7. Attack the whole release-train closure as one campaign; stop when the closure is covered or the campaign budget ends, recording remaining items NOT_RUN.

## Handoff
Return catalog outcomes and findings to the campaign parent only per `policies/schemas/hacker-result.json`. The campaign parent is the release-engineer for the release-train campaign at GATE and the auditor in a `BUG_FINDING` sweep; no peer messaging, no spawning, and the parent returns the folded result to the orchestrator.

## Policy refs
`policies/attack-catalog.json`, `policies/tiers.json`, `policies/invariants.json`, `policies/schemas/hacker-result.json`, `policies/severity.json`.

## Done / failure codes
DONE = closure covered with REFUTED/REPRODUCED evidence. NOT_RUN = remaining items at budget. REFUSED = invalid envelope. No silent PASS for unprobed paths.
