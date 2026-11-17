# Auditor 30 — Bug-finding campaign relay (sweep only)
**Mode:** `BUG_FINDING`

## Purpose
In a `BUG_FINDING` sweep, dispatch exactly one hacker campaign over the whole target, then fold its one schema-valid result into the one bug report. This SOP is sweep-only: a normal `AUDIT` or `DELTA_AUDIT` dispatches no hacker. The release-train campaign at GATE belongs to `sop/release-engineer/15-campaign.md` and is never dispatched from here.

## Inputs
Sweep task context (task_id, base, head, tier, hacker_variant, budget); target and whole-target scope; `policies/tiers.json[tier].hacker_variant/hacker_mode`; `policies/models.json.roles`; `policies/attack-catalog.json#/campaign`, `policies/attack-catalog.json#/probe_counting`, `policies/attack-catalog.json#/engine_policy`; `policies/phases.json`; the harness attack plan; closure_ref; authz_matrix_ref; `policies/concurrency.json`.

**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:auditor`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

## Procedure
1. **Entry check.** Only `sop/orchestrator/80-bug-finding.md` invokes this SOP. If the mode is not `BUG_FINDING`, stop: run no campaign and dispatch no hacker.
2. **A-CAP.** `task context` must equal `policies/tiers.json[tier].hacker_variant`. When it is `none`, skip the relay and the catalog entirely.
3. **K1 up.** `factory services ensure` then `factory k1 up` before any child call. One run serves the whole sweep. `policies/concurrency.json` permits one K1 per lane and a host-wide total, with no queue; a second concurrent run on the same lane is refused with `K1_SLOT_BUSY`. Failure → `BLOCKED K1_UP_FAILED`; dispatch nothing.
4. **One campaign, one prompt.** Call the native `subagent` tool exactly once with agent `hacker`, a prompt carrying task_id, the task context verbatim, the whole-target scope, the attack plan, the return schema, and the budget; model = `policies/models.json.roles["hacker-"+hacker_variant]`. Do not add hints, check outcomes, or a growing item list. Do not re-prompt for more work: if the child asks for direction, answer once inside the existing envelope or let it return `NOT_RUN`. No per-flow, per-batch, or per-item children and no retry loop that extends the envelope.
5. **Timing.** `hacker_mode` is timing only. On `parallel`, call the campaign immediately after K1 is up and run this auditor's own checks while it works; on `after_checks`, dispatch after those checks return.
6. **Await the one schema-valid result.** On timeout or error, still run `factory k1 down` on the finally path. The result is `NOT_RUN`/`INCONCLUSIVE`, never `PASS`.
7. **K1 down.** `factory k1 down({run_id})` removes only `k1-<id>-*` containers/network; never stop Docker, touch pg2a/pg2b, or prune.
8. **Verify coverage.** Evaluate applicability per `policies/attack-catalog.json`; a `NOT_APPLICABLE` claim on an applicable item becomes `NOT_RUN`. Every returned `probe-record/1` hash must resolve in `evidence/probe/`; `REFUTED` needs at least one blocked probe in every listed probe class and at least `min_probes[variant]` distinct blocked probes (a reproduced outcome is a finding, not a refutation; overall campaign coverage counts blocked or reproduced). A cached outcome counts only on key match. A concurrent-writer probe that hit a `sqlite` `OperationalError` is `ENGINE_FAULT`/`NOT_RUN` per `policies/attack-catalog.json#/engine_policy`, never coverage and never `REFUTED`.
9. **Admit and report once.** Admit only reproduced, self-verified findings; severity comes from `policies/severity.json`. De-duplicate by class + locus + normalised symbol, merge duplicates, keep the strongest repro, and write one bug report for the sweep. There is no second campaign and no second report for the same target.

## Handoff
The campaign returns to this auditor only: one JSON object per `policies/schemas/hacker-result.json`. The auditor folds it into the one bug report and returns to the orchestrator. The auditor is the campaign parent only in `BUG_FINDING`; the release-engineer is the campaign parent at GATE. The orchestrator never dispatches the hacker; no other role spawns one except the release-engineer at GATE. No peer messaging.

## Policy refs
`policies/tiers.json`, `policies/phases.json`, `policies/models.json`, `policies/attack-catalog.json`, `policies/gate.json`, `policies/concurrency.json`, `policies/schemas/hacker-envelope.json`, `policies/schemas/hacker-result.json`, `policies/schemas/bug-report.json`, `policies/schemas/fault-codes.json`.

## Done / failure codes
DONE = one campaign relayed and folded into one bug report, or skipped for variant none.
BLOCKED K1_UP_FAILED. REFUSED invalid task context on variant/session mismatch or invocation outside `BUG_FINDING`. Child timeout/error → NOT_RUN/INCONCLUSIVE, never PASS. ENGINE_FAULT → NOT_RUN, never coverage or REFUTED.
