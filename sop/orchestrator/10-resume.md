# 10 — Resume or continue a session
**Mode:** `RESUME`
**Purpose** Re-enter an existing lineage at the exact task and step it stopped on. Resume is a state reconstruction, not a restart and not a new task.
**Inputs**
- Session checkpoint and the last handoff.
- refs: last task context, dispatch, verdict, and finding records.
- Live `git status`, `git rev-parse`, `git log`, `git worktree list`.
- The task list with ids and statuses.
**Procedure**
1. Load check: `factory verify`; then `factory gate`. Stop on FAIL.
2. Read live state for the named worktree: branch, head, tree, clean. Then read the evidence store position from the last refs. Direct reads under `evidence/**` are denied; use returned record refs, not filesystem paths.
3. **Recall prior decisions.** Query `basemind_memory` read-only for the decisions that shaped this lineage, keyed to `lineage_id` and the task ids just recovered; cite each returned ref in the task table. Recall never overrides live state or a record: a disagreement is `HALT EVIDENCE_CONFLICT` or a route to `90-escalation.md`, never a silent override.
4. **Brownfield recovery.** If the worktree is dirty, do not reset, clean, stash, checkout, or switch branches. Find the owning task id from the branch, checkpoint, and diff; map every dirty path (tracked, untracked, ignored) to that task id and its scope. A path with no mapping is left exactly as found and reported; it is never deleted, reset, checked out, or overwritten. Read the mapped diff against acceptance and scope before reuse or commit: a mismatch is `SCOPE_VIOLATION` or `BUILDER_BLOCKED`, not cleanup. The orchestrator does not commit; validate first, then route mapped work to `60-drain.md` or the owning SOP.
5. Rebuild the task table from checkpoint plus evidence store refs: task id, kind, owner, lane, task context, head/tree, status (`done`, `in-flight`, `queued`, `blocked`, `parked`), and next step.
6. Check continuity: same `lineage_id`; tier may rise, never fall. If the live head differs from the task context head, do not reuse the task context; re-mint from live state.
7. Select exactly one task: the in-flight task with no final verdict; otherwise the next queued task in priority order. A `parked` task is not selected without a human-authored resume record; do not invent, split, or rename a task to make progress.
8. Check task context freshness against `policies/phases.json.task_lifecycle` (idle TTL, 6 tasks, 6-hour expiry) and `policy_digest`. Re-mint with `parent_task_id` when stale; keep the remaining allowance and scope.
9. Emit `RECORD` with the recovered refs and the selected task id, then dispatch the owning SOP at its recorded step (`20`-`80`).
10. If state cannot be reconciled, `HALT`; never guess, reset, clean, or restart from scratch.
**Routing / handoff**
Resume returns to the SOP that owns the task kind: bugfix `20`, feature `30`, spec `40`, release `50`, session drain `60`, evaluation `70`, bug-finding `80`. Hand off `task_id`, task id, lane, head/tree, remaining budget, and the next step. All child results return to the orchestrator.
**Policy refs**
`schemas/task.json`, `policies/tiers.json`, `policies/dispatch-rules.json`, `policies/escalation.json`, `policies/worktree-map.json`, `agents/orchestrator.md`, `skills/orchestrator/SKILL.md`.
**Done / failure codes**
Done: `RECORD` or `DISPATCH` with `reason_code: OK` and the exact resumed task/step.
Failure: `HALT` with `HEAD_MOVED`, `DIRTY_TREE`, `EVIDENCE_CONFLICT`, `SIG_INVALID`, or `CTRL_FAILED`; a evidence store conflict is followed by a fresh read, never an unchanged retry.
