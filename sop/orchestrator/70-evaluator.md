# 70 — Evaluation
**Mode:** `EVALUATION`
**Purpose** Evaluate only when the user asks to evaluate, review, or assess. Produce evidence-backed findings, then route them; do not fix, build, or release inside evaluation.
**Inputs**
- The evaluation question and scope.
- The exact revision, diff, file set, or check id.
- Prior compliance reports, verdicts, graphs, and risk records.
- `policies/tiers.json`, `policies/checks.json`, `policies/risk-map.json`, `policies/severity.json`, `policies/invariants.json`.
**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:orchestrator`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

**Procedure**
1. **Confirm the ask.** If the user asked to build, fix, or release, do not evaluate; return to `00-intake.md`. Evaluation is not a default post-build step.
2. **Choose the mode.** -
**High-level:** spec, feature, architecture, or risk. Ask what property is being judged: compliance, cohesion, boundary integrity, threat exposure, or residual risk. -
**Granular:** a diff, file, or check. Pin the revision and the exact paths or check id.
3. **State the questions and required evidence before dispatch.** A useful evaluation has 3-7 answerable questions, each tied to a file:line, graph edge, verdict ref, or check result.
4. **Dispatch the right role.** Spec-vs-code -> `compliance`; architecture -> `auditor`; UX -> `crouton-alpha`; quality, risk, and check evidence -> `auditor`. Read `basemind_graph` for architecture, `git diff`/`git log` for history. Evaluation dispatches no builder.
5. **Runtime confirmation, when needed.** If a finding requires a live exploit check, dispatch the auditor with that question; the auditor verifies it with its own probes. Hacker campaigns exist only at GATE (release-engineer) and in `BUG_FINDING` (auditor); the orchestrator never dispatches the hacker, and a campaign result returns through its parent.
6. **Consolidate.** Group by severity and class. Separate fact (tool result, file:line) from judgment. Record coverage and what was not examined; absence of evidence is not a clean result.
7. **Route findings.** Confirmed defect -> `20-bugfix.md`; spec gap -> `40-spec-completion.md`; feature gap -> `30-feature.md`; architecture issue -> `auditor` then `30`; policy or risk decision -> `90-escalation.md`; test gap -> `30` or `40`.
8. **Stop at the questions.** No opportunistic refactor, no drive-by fixes, no verdict from prose.
**Routing / handoff**
One evaluation record with mode, scope, evidence refs, findings, and the routed SOP per finding. All child results return to the orchestrator; builder, compliance, release, and crouton never message peers.
**Policy refs**
`policies/severity.json`, `policies/checks.json`, `policies/risk-map.json`, `policies/tiers.json`, `policies/invariants.json`, `policies/schemas/finding.json`, `policies/schemas/auditor-verdict.json`, `agents/compliance.md`, `agents/auditor.md`.
**Done / failure codes**
Done: `RECORD` with `reason_code: OK`, mode, evidence refs, and routed findings.
Failure: `HALT` with `SIG_INVALID`, `SCHEMA_INVALID`, or `CHECKS_INCOMPLETE`; do not turn missing evidence into a PASS.
