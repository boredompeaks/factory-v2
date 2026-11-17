# Release-engineer 15 — Release-train hacker campaign
**Mode:** `RELEASE`

## Purpose
Run the one hacker campaign for the whole release train at the BUILD->GATE boundary, in parallel with the release checks, and fold its single result into the release record. The campaign attacks every task promoted together and the seams between them, not one task, flow, or subsystem at a time. It is evidence about the integrated head; it is never a gate predicate and never overrides one.

## Inputs
- The whole release train at the pinned release head/tree: every task promoted together, with its task context and lane verdicts.
- Train membership and the BUILD->GATE boundary from `policies/phases.json`; the campaign contract from `policies/attack-catalog.json#/campaign`.
- `policies/attack-catalog.json` (items, `#/campaign`, `#/probe_counting`, `#/engine_policy`), `policies/tiers.json[tier].hacker_variant`, `policies/models.json.roles`, `policies/concurrency.json`.
- Campaign envelope per `policies/schemas/hacker-envelope.json`; result schema `policies/schemas/hacker-result.json`; the campaign budget from the task context.
- The release checks scheduled by `10-promote.md` and `30-ci.md`.

**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:release-engineer`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

## Procedure
1. **One campaign, one prompt.** Dispatch exactly one `subagent` call with agent `hacker` for the whole release train. The prompt carries the train scope, the task contexts, the campaign envelope, the return schema, and the budget; model = `policies/models.json.roles["hacker-"+hacker_variant]`. Do not pre-cut the train by task, flow, or batch, do not add a growing item list, and do not dispatch a second campaign for the same train.
2. **Provision the campaign runtime.** Bring up one K1 run for the campaign within `policies/concurrency.json` and tear it down on the finally path. A slot conflict or failed start is `BLOCKED K1_UP_FAILED`: record the campaign as `NOT_RUN` with the fault ref; the release predicates still evaluate.
3. **Run the release checks in parallel.** Start the campaign, then evaluate the release predicates and release checks while it works. Do not make a gate predicate wait for the campaign, and do not make the campaign wait for a predicate that can be evaluated independently.
4. **Engine guard.** Before probing, the campaign reads `policies/attack-catalog.json#/engine_policy`. Any class whose eligible engines are postgres-only must be executed against a runtime whose `DATABASE_URL` is PostgreSQL — the K1 runner's internal pg or the lane's assigned pg2a/pg2b. Never start a concurrent-writer probe against SQLite. If the runtime is SQLite, the campaign records the class `NOT_RUN` with `engine_fault`/`skip_reason` and puts it in the next-probe plan; it does not spend budget trying it. A SQLite `OperationalError` or debug 500 is an engine fault, not a finding and not `REFUTED`. Cite `policies/attack-catalog.json#/engine_policy`; do not restate the class list here.
5. **Await one schema-valid result.** The result covers every catalog item the campaign touched across the train, with per-item attempts, probes, outcome, and evidence. A missing item is `NOT_RUN`, never clean. A timeout or error is `NOT_RUN` with the fault ref, not a release refusal.
6. **Audit coverage before folding.** Run `bin/factory probe audit --records <evidence/probe> --variant <tier> --json`; per item, run `bin/factory probe audit --records <evidence/probe> --variant <tier> --item <ID> --json`. This command is the only coverage count; never hand-count probes. Return the audit's `next_probe_plan`; an engine fault is recorded `NOT_RUN`/`error` and carried into the plan, never counted and never upgraded to `REFUTED`. The campaign result is `PARTIAL` when the plan is non-empty, never `COMPLETE`. A result that fails the audit is folded as `NOT_RUN` for the failing item, not as `REFUTED`.
7. **Fold, do not overwrite.** Append the campaign ref, per-item outcomes, and findings to the release record. Leave every predicate field (`Q-SIG`, `Q-POLICY`, `Q-VERDICT`, `Q-LANEHEADS`, `Q-CLEAN`, `Q-PROT`, `Q-CHECKS`, `Q-METHOD`, `Q-GUARD`) exactly as evaluated. A campaign `REFUTED`/`REPRODUCED` outcome never rewrites a predicate result.
8. **Route findings once.** Campaign findings go to the orchestrator as findings, clustered by class and locus. A finding may open a bounded repair loop on a later turn; it does not change this turn's release decision and is never sent directly to a builder.
9. **Respect the budget and tear down.** Stop the campaign at its budget; remaining items stay `NOT_RUN`. Always tear K1 down, including on timeout or error.

## Handoff
Return the release result plus the campaign ref to the orchestrator only. The release-engineer is the campaign parent for the release train at GATE; the auditor is the campaign parent only in a `BUG_FINDING` sweep. No peer messaging; the hacker result returns to this role and is folded into the release record.

## Policy refs
`policies/phases.json`, `policies/attack-catalog.json`, `policies/tiers.json`, `policies/models.json`, `policies/concurrency.json`, `policies/schemas/hacker-envelope.json`, `policies/schemas/hacker-result.json`, `policies/release-predicates.json`, `policies/schemas/release-result.json`.

## Done / failure codes
DONE = one campaign result recorded and folded into the release record, with the release verdict computed from its predicates, and the probe audit's `next_probe_plan` empty.
PARTIAL = the probe audit's `next_probe_plan` is non-empty (including `engine_fault`/`skip_reason` classes); folded as PARTIAL coverage, never COMPLETE and never REFUTED.
NOT_RUN = campaign skipped, timed out, or errored (variant none, K1 unavailable, budget); the fault ref is recorded and the gate still evaluates its predicates.
REFUSED = invalid campaign envelope; the campaign runs nothing.
The campaign never changes a gate predicate and never converts a red predicate to green.
