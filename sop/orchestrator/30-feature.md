# 30 — Feature
**Mode:** `FEATURE`
**Purpose** Take a feature from acceptance through a build queue and audit to release, without bypassing the verifier.
**Inputs**
- Feature brief and user-visible outcome.
- Acceptance refs and the acceptance author family.
- Scope globs, affected lanes, dependencies.
- `policies/task-kinds.json` (`FEATURE`), `policies/task-limits.json`, `policies/tiers.json`, `policies/checks.json`, `policies/risk-map.json`, `policies/dispatch-rules.json`.
**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:orchestrator`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

**Procedure**
1. **Acceptance first.** Require at least one acceptance ref that fails on the current head. No ref -> `HALT NO_ACCEPTANCE_REF`; do not let a builder author proof of its own feature.
2. **Decompose.** Split the feature into tasks that respect `policies/task-limits.json[FEATURE]`. One task context per task, one lane per builder, one scope glob set per task. A task that exceeds the limit is split before dispatch; a builder that receives one returns `BLOCKED SPLIT_REQUIRED`.
3. **Queue.** Order by dependency, then severity: security/data-loss/payments, core flow, polish. Two permanent lanes may run in parallel; one builder per lane. Record task ids and the queue in the dispatch record.
4. **Build.** Mint a task context per task from live head/tree and dispatch builders. On return validate `P-SIG`, `P-SCOPE`, `P-TIER`, `P-BUDGET`; a raised tier needs a new task context, never a widened old one.
5. **Audit.** For every task mint a fresh auditor task context and dispatch a new session. The auditor runs the tier's required checks and returns a verdict; the builder's "done" is a claim until then.
6. **Route.** PASS -> next queued task, or a section boundary when all active lanes pass. BLOCK -> `builder/60-repair` with the auditor's finding refs. INCONCLUSIVE/TOOL_FAULT -> one fresh re-audit if the fault code permits. REFUSED -> `90-escalation.md`.
7. **Release.** At the section boundary, stop dispatch and go to `50-release.md`. Do not release mid-feature.
8. **Evaluation is separate.** Architecture, spec, or risk review happens only when the user asks; route to `70-evaluator.md`.
**Routing / handoff**
Feature -> `30` build queue -> `20` for defects found during build -> `50` at the boundary. Hand off task context id, task id, lane, head/tree, scope, acceptance refs, and budget. Every child result returns to the orchestrator; no peer messaging.
**Policy refs**
`policies/task-kinds.json`, `policies/task-limits.json`, `policies/tiers.json`, `policies/checks.json`, `policies/risk-map.json`, `policies/dispatch-rules.json`, `policies/changelog.json`, `schemas/task.json`, `agents/builder.md`, `agents/auditor.md`.
**Done / failure codes**
Done: `DISPATCH` with `reason_code: OK`; all tasks PASS -> `50`.
Failure: `HALT` with `NO_ACCEPTANCE_REF`, `SCOPE_VIOLATION`, `CYCLES_EXHAUSTED`, `BUDGET_EXCEEDED`, or `BUILDER_BLOCKED`.
