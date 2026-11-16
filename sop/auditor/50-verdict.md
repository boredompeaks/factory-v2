# Auditor 50 — Verdict
**Mode:** `AUDIT`

## Purpose
Turn admitted evidence into one verdict the orchestrator can route on, using the fixed rule rather than judgement.

## Inputs
All check/outcome/evidence refs from this run; prior clearances; the task context; `policies/schemas/auditor-verdict.json`; `policies/severity.json`; `policies/schemas/fault-codes.json`.

## Procedure
1. **Delta continuity.** A repair delta may continue in this auditor context and is judged by the same V0–V3 rule; `15-delta.md` governs the patch, exploit recheck, and variants. P2/P3 is the intended reuse; a P1 repair keeps the session but gets a new builder window. Context reuse never reuses a task context across a head or tier change: mint a delta task context at the new head/tree with `parent_task_id`. A new task, a new head outside an audited repair, or a raised tier gets a fresh auditor task context and session.
2. Clear prior findings: run every earlier admitted reproducer on head; `prior_findings_cleared` is true only when none reproduces.
3. Apply V0–V3 in order: V0 refused (task context/state invalid), V1 BLOCK (FAIL check, causal P1/P2, or uncleared prior), V2 INCONCLUSIVE (required check/applicable item NOT_RUN; cause TOOL_FAULT only for listed fault codes, else BUDGET_EXHAUSTED), V3 PASS otherwise.
4. Finalise fields exactly as the schema requires; no prose fields. Findings carry class/locus/severity/source/repro/introduced.
5. A BLOCK carries every admitted finding from every check that returned; set `effective_on_block` per policy and force complete_hacker on the final cycle.
6. P3/preexisting findings go to `residual` only.
7. Append the same object via `factory evidence`, findings via finding, attacks via attack-log; never write quality/** directly.

## Handoff
Return the single JSON object to the orchestrator. Do not restate or reshape the schema here: the contract is `policies/schemas/auditor-verdict.json`. No peer messaging; the hacker's results return to this auditor only.

## Policy refs
`policies/schemas/auditor-verdict.json`, `policies/severity.json`, `policies/schemas/fault-codes.json`, `policies/tiers.json`, `policies/negative-controls.json`.

## Done / failure codes
DONE = verdict computed and returned. BLOCK/PASS per V1/V3. INCONCLUSIVE TOOL_FAULT/BUDGET_EXHAUSTED. REFUSED invalid task context/STATE_MOVED. A verdict is final for `(head, tree)`; re-audit the same tree only for a permitted fault re-audit (`20-checks.md`, `20-bugfix.md` step 7). A new head inside an audited repair may continue this context as a `15-delta` under a delta task context; a new task, a head outside an audited repair, or a raised tier gets a fresh task context and session.
