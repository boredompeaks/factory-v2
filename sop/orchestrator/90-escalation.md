# 90 — Escalation **Modes:** `ESCALATION`, `PARK`
**Purpose** Decide when work must stop and a human owner must act. This SOP contains no codes, channels, owners, or SLAs; those live only in `policies/escalation.json`.
**Inputs**
- The failed predicate and the `reason_code` already produced by the decision or verdict.
- Evidence refs: evidence store record id plus hash, or a git object id.
- The attempted resolution and its result.
- `policies/escalation.json`, `policies/schemas/orchestrator-decision.json`.
**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:orchestrator`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

**Procedure**
1. **Detect.** Escalate when any of these is true: a predicate fails and cannot be fixed inside the role's authority; a evidence store write conflicts twice after fresh reads; policy or branch-protection drift makes the gate untrustworthy; the budget or cycle limit is exhausted with zero progress; a signature or schema is invalid; a worktree is dirty, unknown, or moved; CI fails or times out; a security or risk finding exceeds what the tier can accept; or the decision is human-only (scope change, destructive operation, policy edit, waiver).
2. **Do not improvise.** Do not retry unchanged, widen scope, bypass a permission, substitute a shell command, edit policy, or lower a tier. A refusal is terminal for the turn.
3. **Look up the code.** Read the `reason_code` from the failed record and look it up in `policies/escalation.json`. The owner, channel, and response time come from that file and nowhere else.
4. **Write HALT.** Append the HALT record with `reason_code`, evidence refs, live `base/head/tree`, what was tried, and what decision is needed. Stop all dispatch for the lineage.
5. **Hand off.** The harness routes the HALT to the owner named by policy. Do not message other roles or peers directly; the orchestrator has no peer channel.
6. **Resume only by authority.** Continue only with a new task context created from a human-authored evidence store record. The orchestrator cannot author that record.
7. **Keep the SOP thin.** When asked "who do I tell?", cite the policy entry, not a remembered channel. When asked "how long?", cite the policy, not an estimate.
8. **Non-HALT questions.** Use the question tool for clarification; do not manufacture a halt for a missing preference.
**Routing / handoff**
HALT ends the turn. The harness owner route is the only notification path. A resumed task returns to `10-resume.md` with the authorizing record ref.

## Park `PARK` is the deliberate, non-failure stop. It uses the same `HALT` decision with `reason_code: PARKED`, sets the task status to `parked`, and leaves no active task context, auditor, K1 run, or builder session. It is not `blocked` (which needs a fix) and not `done`. The orchestrator cannot unpark its own decision; resume only in `10-resume.md` with a human-authored record and a fresh task context at the live head/tree.
**Policy refs**
`policies/escalation.json` (sole source for reason codes, owners, channels, and SLAs), `policies/schemas/orchestrator-decision.json`, `policies/schemas/fault-codes.json`, `policies/lessons.json`.
**Done / failure codes**
Done: HALT appended and the policy owner route triggered.
Failure: if the HALT write itself conflicts, re-read state and re-evaluate once; if it still fails, report the exact tool error to the human. Never invent a reason code or a contact.
