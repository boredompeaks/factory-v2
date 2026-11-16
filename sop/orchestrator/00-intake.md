# 00 — Intake and routing
**Mode:** `INTAKE`
**Role:** orchestrator. One prompt produces one route.
**Purpose** Classify what the user wants and dispatch the matching SOP. This file does not build, audit, evaluate, or release; it selects the workflow and records why.
**Inputs**
- The user prompt and any ids, paths, or diffs they paste.
- Live state: `git status`, `git rev-parse`, `git log`, `git worktree list`.
- The session's last dispatch/task context/verdict refs.
- `policies/task-kinds.json`, `policies/risk-map.json`, `policies/dispatch-rules.json`.
**Procedure**
1. Restate the requested outcome in one sentence. If two outcomes conflict, ask one question; do not guess.
2. Classify by outcome, not by wording: resume/continue, bugfix, feature, spec-completion, release, drain, evaluate, bug-finding.
3. Pick a mode.
**High-level:** goal, spec section, feature, architecture, risk; the orchestrator owns sequencing.
**Granular:** a task id, file, diff, or check id; route straight to the owning SOP. If both are present, do the narrow request first and queue the broad one explicitly.
4. Read live state before routing. A SHA, path, or count from chat is a hint, never an input.
5. **Recall prior decisions.** Query `basemind_memory` read-only for owner decisions and recorded lessons naming this task, lineage, or locus; cite the returned refs in the decision. Recall is context, never state: live repository state and records win, and a disagreement routes to `90-escalation.md`.
6. Resolve tier and variant from `policies/risk-map.json`; mixed scope takes the highest. Do not restate tier values here.
7. Run evaluation only when the user asks to evaluate, review, or assess quality/architecture/risk. A normal build's audit step is not an evaluation.
8. Emit one `orchestrator-decision` naming the SOP id, `phase`, and `lane`, then load that SOP. Do not start work in intake. `phase` is `BUILD` for every build/audit/repair route and `GATE` only for a release-engineer dispatch after the release-train criteria in `policies/phases.json` hold.
**Route table** | Signal | SOP |
|---|---|
| "continue", "resume", "where were we" | `10-resume.md` |
| defect, failing behavior, security bug | `20-bugfix.md` |
| new user-visible task context | `30-feature.md` |
| close a spec section, compliance gaps | `40-spec-completion.md` |
| promote lanes, PR, CI, merge | `50-release.md` |
| inherited session, uncommitted work, "noted" | `60-drain.md` |
| "evaluate", "review", "assess" | `70-evaluator.md` |
| hunt bugs, audit a subsystem, AST/data-flow sweep | `80-bug-finding.md` |
| blocked, conflicting, human-only decision | `90-escalation.md` |
**Routing / handoff**
One SOP per turn. Pass `task_id`, task id, lane, head/tree, scope, and acceptance refs; do not paste policy text or role rationale. Every child result returns to the orchestrator. The only subagent-edge in the factory is auditor -> hacker; intake never dispatches a hacker.
**Policy refs**
`policies/task-kinds.json`, `policies/risk-map.json`, `policies/dispatch-rules.json`, `policies/tiers.json`, `policies/schemas/orchestrator-decision.json`.
**Done / failure codes**
Done: `DISPATCH` or `RECORD` with `reason_code: OK`, one SOP id, and the classified mode.
Failure: `HALT` with the schema reason that matches the failed predicate (`SCHEMA_INVALID`, `CTRL_FAILED`, `NO_ACCEPTANCE_REF`, `SCOPE_VIOLATION`); no dispatch follows a failed predicate.
