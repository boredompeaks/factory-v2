# 40 — Precheck
**Mode:** `BUILD`
**Purpose** Run the tier's builder-side checks before handoff, excluding checks owned by the auditor or by the release-train campaign, and return a truthful block when they do not pass.
**Inputs**
- The committed or staged tree from `30-tests.md`.
- `policies/tiers.json[tier]`.
- `policies/checks.json`, `policies/check-runners.json`, `policies/gate.json`.
**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:builder`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

**Procedure**
1. Read `required_checks` for the task context tier. Do not take the list from memory or from the task text.
2. Subtract the tier's `builder_precheck_exclude` entries. Those checks belong to the auditor or the runtime lane; builder neither runs nor reports them.
3. Run `SCOPE` first. A path outside `task context` fails the precheck; do not continue with a wider set.
   If the only way to satisfy a gate is an out-of-scope path, stop and return `BLOCKED SCOPE_VIOLATION` with the forcing check and the base-state evidence. Do not widen the scope yourself.
**Refactor-class diffs.** Splitting a function is legal: `ARCH` reads public surface by name (a `_`-private helper is internal, a relocated view is not a new export) and `MUTATION` mutates only added, non-relocated lines and deals its budget round-robin across hunks. Do not add an architecture allowance or a mutation pragma to make a split pass; if a genuinely new public symbol or a new hot spot is involved, that is a real finding.

**Deviations and debt.** Before step 4, run `factory deviation --task-id <id>`. It lists the obligations the hierarchy creates: pre-existing rank-C+ blocks your hunks touch, and out-of-scope paths. Prefer the smallest in-scope change that removes the obligation. If the debt is real and cannot be improved in this task, write `accepted_debt` in the task evidence with an owner, one sentence of reasoning, an ISO expiry inside the 30-day ceiling, and a `follow_up` naming where it is paid down. Never author a `deviation-verdict.json` and never declare `forced_refactor` to justify work you chose to do; that declaration is an auditor decision and needs a verdict only the auditor may write. `DEVIATION` is excluded from the builder precheck for this reason; the obligation still reaches the gate.

4. Run each remaining check with `factory check <CHECK>` at the task context tree. Use the engine the check definition or condition requires; use `none` only where the definition allows it.
5. Parse the check's own summary and result fields (`Ran N tests` + `OK|FAILED`, or the check's structured verdict). The shell exit code corroborates; it is never the verdict. A missing summary is not a pass.
6. On failure, make the smallest fix, create a new commit, and re-run the failed check plus any check whose surface the fix touched. Repeat up to `builder_precheck_rounds`.
7. Do not run a check outside the tier set, do not add a bypass, and do not convert `BLOCKED` or `NOT_RUN` into `PASS`. If a required runner is unavailable, return the exact tool error; do not substitute a shell command.
8. Reuse a result only for the identical `(tree, check_id, check_impl_sha, engine)` key. A changed tree invalidates the old result.
9. If a required check is red because the product workflow, CI config, dependency pin, or check definition is missing/defective and the builder is forbidden to edit it, populate `builder-result.ci_request` with kind, reason, paths, and evidence_ref and return `BLOCKED PRECHECK_UNRESOLVED`. Never edit `.github/**`, a required-set policy, or a check to make it green.
10. If failures remain after the allowed rounds, return `BLOCKED PRECHECK_UNRESOLVED` with `failing_checks`, the `ci_request` when applicable, and the last evidence refs.
**Routing / handoff**
All green -> `50-commit.md`. A fixable failure -> `60-repair.md` only when the failure came from an admitted finding; otherwise stay in the implement/test loop. Return only to the orchestrator.
**Policy refs**
`policies/tiers.json`, `policies/checks.json`, `policies/check-runners.json`, `policies/gate.json`, `policies/schemas/builder-result.json`, `skills/builder/SKILL.md`.
**Done / failure codes**
Done: every non-excluded required check is green at the committed tree and the evidence refs are recorded.
Failure: `BLOCKED PRECHECK_UNRESOLVED` with the exact check ids; never a partial green.
