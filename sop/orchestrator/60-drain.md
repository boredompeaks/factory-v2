# 60 — Drain an inherited session
**Mode:** `DRAIN`
**Purpose** Inherit a session that stopped with uncommitted work, recover its exact queue, validate the work, commit it only if it is sound, and continue. This is session drain, not the `DRAIN` task kind.
**Inputs**
- The user's handoff ("noted") and the session checkpoint.
- Task ids, task contexts, last verdicts, and claimed progress.
- `git status`, `git diff`, `git log`, and `git worktree list` for each named worktree.
**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:orchestrator`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

**Procedure**
1. **Freeze.** Acknowledge the handoff and stop new writes or dispatches until the inventory is complete. "Noted" means "I have read the handoff", not approval to discard work.
2. **Enumerate task ids.** Build a table from the checkpoint and refs: task id, kind, owner, lane, expected head/tree, scope, status (`done`, `in-flight`, `queued`, `blocked`). Include ids with no matching ref as `unknown`. If no task id is recoverable, search the branch, diff, and last dispatch; do not invent one.
3. **Read live state.** For each worktree record branch, head, tree, clean; run `git diff` name-only and stat. This is brownfield recovery: map every dirty path, including untracked and ignored, to a task id and its scope. A path with no mapping is not deleted, reset, checked out, stashed, or overwritten; stop and report it. A branch that does not match the mapped task is reported, never switched.
4. **Validate the work.** Read the diff against the task's acceptance and scope: no unrelated edits, no lowered assertions, no new skips or xfail, no secrets, no test-infrastructure changes outside scope. A mismatch is `BUILDER_BLOCKED` or `SCOPE_VIOLATION`, not a cleanup.
5. **Commit if valid.** The orchestrator does not commit. Mint a builder task context scoped to the mapped paths and dispatch the builder through `builder/10`-`50` against the dirty tree. `git commit` takes `task_id`, `paths`, and `tree`; after it returns, confirm the new head and clean state with `git status`.
6. **Confirm no regression.** Require the affected SUITE plus the tier's required checks on the committed head. Do not accept the builder's earlier green as proof of the inherited change.
7. **Continue the queue.** Resume at the first task with no commit and no verdict; follow its owning SOP. Do not redo committed tasks and do not reorder the queue without recording why.
8. **Record the inheritance.** Log task ids, base/head, commit sha, checks, and remaining queue so the next session can resume from evidence.
**Routing / handoff**
Resume the queue through `20`-`50`. The `DRAIN` task kind is different: follow `policies/drain.json` R1-R5 and use `20-bugfix.md` for its exploit/repair legs. Hand off task id, lane, head/tree, and remaining budget.
**Policy refs**
`schemas/task.json`, `policies/tiers.json`, `policies/checks.json`, `policies/dispatch-rules.json`, `policies/task-limits.json`, `policies/escalation.json`, `policies/worktree-map.json`, `policies/drain.json`, `skills/builder/SKILL.md`.
**Done / failure codes**
Done: `RECORD` with `reason_code: OK`; valid work committed, checks green, queue resumed.
Failure: `HALT` with `DIRTY_TREE`, `HEAD_MOVED`, `EVIDENCE_CONFLICT`, `SCOPE_VIOLATION`, or `BUILDER_BLOCKED`; preserve all uncommitted work.
