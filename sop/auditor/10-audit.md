# Auditor 10 — Audit entry
**Mode:** `AUDIT`

## Purpose
Verify one build against one task context: pin task context/head/tree, resolve the tier's required checks, and produce evidence-backed outcomes. A normal audit and a delta audit dispatch no hacker. The auditor's only child edge is one whole-target campaign in a `BUG_FINDING` sweep; the release-engineer's only child edge is the whole-release-train campaign at GATE (`sop/release-engineer/15-campaign.md`).

## Inputs
- Orchestrator dispatch: task_id, lineage_id, task_ctx_id, evidence_head, done.output_schema.
- task context (`schemas/task.json`): role, base/head/tree, tier, hacker_variant, budget, scope, acceptance refs.
- Live repo state via `git status`.
- `policies/*.json` are authoritative; no value comes from task text.

**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:auditor`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

## Procedure
1. Validate A-CAP: envelope task context role, tier→variant agreement, session rule, no post-task context HALT. Session rule: a fresh auditor session and task context for a new task, a new head outside an audited repair, or a raised tier; a delta audit reuses the same auditor session under a new delta task context with `parent_task_id`. Fail → REFUSED invalid task context/STATE_MOVED.
2. Sample `git status` head/tree/branch/clean. Require equality with task context/tree; re-check at end. Drift → REFUSED STATE_MOVED.
3. Load the auditor role prompt and this SOP set; read the tier entry in `policies/tiers.json` and definitions in `policies/checks.json`.
4. **Recall prior verdicts and findings.** Query `basemind_memory` read-only for earlier verdicts, admitted findings, and owner decisions on this locus before building the run plan. Use them only to seed the prior-finding checks (`50-verdict.md` step 2); a recalled finding is a pointer, never evidence — re-run its reproducer to clear it.
5. Build the run plan as exactly `policies/tiers.json[tier].required_checks`; no extra checks.
6. Execute each via factory check/`factory gate` at the git object id; record result ref + sha256 per outcome. Builder code never runs under this role.
7. Dispatch the hacker campaign only in a `BUG_FINDING` sweep, after K1 is up; see `30-hacker-relay.md`. Normal `AUDIT`/`DELTA_AUDIT` dispatches no hacker; the release-train campaign belongs to `sop/release-engineer/15-campaign.md`. The orchestrator never spawns the hacker.
8. Admit findings only via the admission rules; compute the verdict per 50-verdict.md.
9. Append verdict/findings/attack logs via `factory evidence`; never write quality/** directly.
10. Return the verdict object.

## Handoff
Return one JSON object validating `policies/schemas/auditor-verdict.json` to the orchestrator. Do not message builder, compliance, release-engineer, crouton-alpha, or the hacker outside the sanctioned `BUG_FINDING` campaign call. The campaign result returns to this auditor; the auditor returns to the orchestrator.

## Policy refs
`policies/tiers.json`, `policies/checks.json`, `policies/models.json`, `policies/gate.json`, `policies/negative-controls.json`, `policies/schemas/fault-codes.json`.

## Done / failure codes
DONE = verdict returned. REFUSED invalid task context/STATE_MOVED. INCONCLUSIVE TOOL_FAULT/BUDGET_EXHAUSTED. BLOCKED K1_UP_FAILED at relay. Never invent PASS; NOT_RUN requires a real fault ref.
