# Auditor 30 — Hacker relay
**Mode:** `AUDIT`

## Purpose
Provision one K1 run and relay a bounded envelope to the auditor's only child role, the hacker, then fold the returned outcomes into the verdict. Start the child as early as the task context allows, in parallel with this auditor's own checks. The only subagent edge is auditor → hacker; results return to the auditor, and the auditor returns to the orchestrator.

## Inputs

task context (task_id, base, head, tier, hacker_variant, budget, drain_stage); `policies/tiers.json[tier].hacker_variant/hacker_mode`; `policies/models.json.roles`; the harness attack plan; closure_ref; authz_matrix_ref.

## Procedure
1. A-CAP: `task context` must equal `policies/tiers.json[tier].hacker_variant`. When it is "none", skip the relay and catalog entirely.
2. K1 up: `factory services ensure` then `factory k1 up` before any child call. One run serves a normal audit and one bug-finding sweep; the sweep reuses that run for its bounded batches. `policies/concurrency.json` permits at most one K1 per lane and two host-wide, and there is no queue: a second concurrent run on the same lane is refused with `K1_SLOT_BUSY`. Failure → BLOCKED K1_UP_FAILED; dispatch nothing.
3. Timing: `hacker_mode` is timing only. On `parallel`, call the child immediately after K1 is up and run this auditor's own checks while it works; do not wait for checks before dispatching. On `after_checks`, dispatch after those checks return.
4. **One call, one bounded prompt.** Call the native subagent tool exactly once per child, with agent "hacker", a prompt carrying task_id, the task context verbatim, the attack plan, the return schema, and the budget; model = `policies/models.json.roles["hacker-"+hacker_variant]`. Do not add hints, check outcomes, or a growing item list. Do not re-prompt for more work: if the child asks for direction, answer once inside the existing envelope or let it return `NOT_RUN`. No drip-fed batches and no retry loop that extends the envelope.
5. Child count: a normal audit has exactly one hacker child. Only a bug-finding sweep (`sop/orchestrator/80-bug-finding.md`) may dispatch multiple children, one per bounded batch; every batch returns to this auditor. There is never one auditor per batch and never a second child for the same item.
6. Await the child's schema-valid result. On timeout or error, still run `factory k1 down` on the finally path.
7. `factory k1 down`({run_id})` removes only `k1-<id>-*` containers/network; never stop Docker, touch pg2a/pg2b, or prune.
8. Evaluate applicability per `policies/attack-catalog.json`; a NOT_APPLICABLE claim on an applicable item becomes NOT_RUN. Cached outcomes count only on key match.
9. Admit only reproduced, self-verified findings; severity comes from `policies/severity.json`.

## Handoff
Each hacker returns to this auditor only: one JSON object per `policies/schemas/hacker-result.json`. In a sweep the auditor merges every batch before writing the one bug report. The auditor folds outcomes into its verdict and returns to the orchestrator. The orchestrator never spawns the hacker; no other role spawns anything or messages a peer.

## Policy refs
`policies/tiers.json`, `policies/models.json`, `policies/attack-catalog.json`, `policies/gate.json`, `policies/concurrency.json`, `policies/schemas/hacker-envelope.json`, `policies/schemas/hacker-result.json`, `policies/schemas/fault-codes.json`.

## Done / failure codes
DONE = relay completed, or skipped for variant none. BLOCKED K1_UP_FAILED. REFUSED invalid task context on variant/session mismatch. Child timeout/error → NOT_RUN/INCONCLUSIVE, never PASS.
