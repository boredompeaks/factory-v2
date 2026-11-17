# Auditor 15 — Delta audit
**Mode:** `DELTA_AUDIT`

## Purpose
Re-audit a repair at a new head without re-litigating the whole lineage: reuse the same auditor context, pin the prior and new head/tree, compare the patch, prove the old exploit no longer reproduces, and emit a delta verdict. A delta audit is narrower in scope, not weaker in evidence.

## Inputs
- The prior auditor verdict and every admitted reproducer/finding ref it carries.
- A delta task context at the new head/tree with `parent_task_id` set to the prior task context; same lineage, tier may rise, never fall.
- `git diff` from the prior head to the new head, the builder result, and the harness closure for the patch.
- `policies/tiers.json`, `policies/checks.json`, `policies/severity.json`, `policies/gate.json`, `policies/schemas/auditor-verdict.json`.

**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:auditor`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

## Procedure
1. **Reuse the same task context, never a stale tree.** The same auditor session may continue a repair delta; a P2/P3 repair is the intended reuse, and a P1 repair keeps the session but gets a new builder window (see `20-bugfix.md`). The session mints a delta task context bound to the new head/tree with `parent_task_id`; it never mutates the old task context and never audits an unpinned working tree. A new task, a new head outside an audited repair, or a raised tier gets a fresh auditor task context and session (see `50-verdict.md`).
2. **Pin both ends.** Read `git status` and `git status`; require the live head/tree to equal `task context/tree` and compare with the prior verdict's head/tree. Re-read at the end. Drift is `REFUSED STATE_MOVED`; do not audit whatever happens to be checked out.
3. **Choose one run mode and name it in the delta report.**
- **full** — run exactly `policies/tiers.json[tier].required_checks` at the new tree; or
- **delta** — run every required check over its delta slice: targeted `SUITE`, changed-hunk `MUTATION`, changed-path `SECRETS`/`SMELL`/`CONVENTION`, closure-scoped `ARCH`/`AUTHZ_MATRIX`/`GUARD_DELTA`, and so on; name the scope per check. A delta audit is not a licence to skip a required check. Every required check is run, in full or over its delta slice; a required check that cannot be run is `NOT_RUN` with its condition/fault evidence, making the delta verdict `INCONCLUSIVE`, never `PASS`.
4. **Compare the patch.** Read every hunk from the prior head to the new head. Each hunk must belong to the admitted finding's locus or the declared scope. Unrelated edits, test edits, added skips or xfails, generated files, dependency/config changes, and comment-only churn are `BLOCK SCOPE_VIOLATION` or `TEST_SET`, not notes. Confirm the builder did not edit, weaken, skip, or xfail the returned probe.
5. **Re-run the old exploit.** Run the prior reproducer at the new tree with `factory repro` for `repro_runs`. It must no longer reproduce. A still-reproducing exploit is `BLOCK` on the original finding id with the new evidence; a reproducer that errors for an unrelated reason is `NOT_RUN`, not cleared.
6. **Try input variants.** Change one dimension per variant of the original input: tenant/owner, id encoding, HTTP method, engine, ordering, or a boundary value. They must fail closed the same way the exploit now does. A variant that still breaks is a new finding, not a pass. The variant count, when policy sets one, comes from policy; this SOP names no number.
7. **Look for silent regressions.** Re-run `SUITE` at the delta head; compare status codes, error shapes, amounts, ownership, ordering, and side effects on the touched closure. Apply `40-quality.md` good taste and mutation-style review to the patch, including new smells, architecture edges, and maintainability costs. A regression is a finding even when the original exploit is dead.
8. **Emit the delta verdict.** Use the ordinary `auditor-verdict` under the V0–V3 rule. The delta report — mode (`full` or `delta`), prior/new head and tree, patch summary, exploit recheck, variants, regression/smell notes — is the evidence artifact referenced by `attack_log_ref` and the check `evidence_ref`s. No new schema and no prose-only verdict.
9. Append the verdict and any new findings via `factory evidence`; never write `quality/**` directly. A delta verdict is final for `(head, tree)`.

## Handoff
Return to the orchestrator: verdict, mode, prior/new head and tree, exploit recheck, variants, findings, residual. The same auditor context may have produced it; the orchestrator does not open a second auditor for a P2/P3 delta.

## Policy refs
`policies/tiers.json`, `policies/checks.json`, `policies/severity.json`, `policies/gate.json`, `policies/schemas/auditor-verdict.json`, `policies/schemas/fault-codes.json`.

## Done / failure codes
DONE = delta verdict computed and returned. BLOCK = exploit still reproduces, scope/test-set violation, or a regression. INCONCLUSIVE = a required check or the old-exploit recheck is `NOT_RUN`. REFUSED `invalid task context`/`STATE_MOVED`.
