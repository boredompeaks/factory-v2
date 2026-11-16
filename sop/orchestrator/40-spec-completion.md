# 40 — Spec completion
**Mode:** `SPEC_COMPLETION`
**Purpose** Close a spec section by establishing compliance evidence first, then building only the gaps that evidence proves.
**Inputs**
- Spec section/range and requirement ids.
- Prior compliance report, if any.
- `policies/dispatch-rules.json`, `policies/task-kinds.json`, `policies/task-limits.json`, `policies/tiers.json`, `policies/checks.json`, `policies/risk-map.json`.
**Procedure**
1. **Compliance before build.** Dispatch `compliance` read-only with the spec range. It writes only `backend/docs/compliance/S<n>.md` and returns the full requirement table. `policies/dispatch-rules.json` pre-dispatches compliance only for spec, requirements, compliance-review, and drain scopes (`DR-SPEC-01`, `DR-DRAIN-01`); security, money, infra, and ordinary build tasks do not pre-dispatch it. A FAIL is a halt, not a builder task.
2. **Validate the report.** Every requirement id appears once; each verdict is `IMPLEMENTED`, `PARTIAL`, `MISSING`, `DEVIATES`, `NOT-APPLICABLE`, or `DEFERRED->S<x>`; each has file:line evidence or a named gap. A pruned or id-incomplete table is `CTRL_FAILED`; re-dispatch the same compliance task rather than filling gaps from memory.
3. **Turn gaps into work.** Only `PARTIAL`, `MISSING`, and `DEVIATES` become build tasks. `DEFERRED` goes to its owning section. `NOT-APPLICABLE` needs a one-line justification, not silence.
4. **Acceptance per gap.** Each gap task gets acceptance refs that fail on the current head. No ref -> `NO_ACCEPTANCE_REF`; a builder may not invent the proof.
5. **Queue by risk.** Security/data-loss/payments first, then core flow, then polish. Respect `policies/task-limits.json`; split large gaps before dispatch. One task context per task.
6. **Build and audit.** Run the queue through `30-feature.md` steps 4-6. The auditor verifies the implementation; the compliance claim is not a substitute for a verdict.
7. **Re-check.** After the queue drains, re-run compliance on the same section. Close only when every blocking gap is `IMPLEMENTED` with evidence; otherwise queue the remainder.
8. **Separate defects.** A defect found during completion routes to `20-bugfix.md`; do not bury it in a spec task.
**Routing / handoff**
Compliance -> gap queue -> `30` build/audit -> `50` release at the boundary. Defects -> `20`. Spec evaluation only when asked -> `70`. Hand off spec range, requirement ids, compliance report ref, task context ids, and acceptance refs.
**Policy refs**
`policies/dispatch-rules.json`, `policies/task-kinds.json`, `policies/task-limits.json`, `policies/tiers.json`, `policies/checks.json`, `policies/risk-map.json`, `agents/compliance.md`, `skills/compliance/SKILL.md`, `agents/auditor.md`.
**Done / failure codes**
Done: `DISPATCH` with `reason_code: OK`; section closed only after the re-check is clean.
Failure: `HALT` with `CTRL_FAILED`, `NO_ACCEPTANCE_REF`, `SCOPE_VIOLATION`, or `CYCLES_EXHAUSTED`.
