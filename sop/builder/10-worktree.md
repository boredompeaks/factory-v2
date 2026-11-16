# 10 — Worktree and state
**Mode:** `BUILD`
**Purpose** Enter the worktree named by the task context and prove the state matches before any write. A mismatch is refused, not repaired by guessing.
**Inputs**
- `task_id` and the task context.
- `policies/worktree-map.json`.
- `schemas/task.json`, `policies/task-limits.json`.
- `git status`, `git rev-parse`, `git log`.
**Procedure**
1. Load the `builder` skill. It is the task procedure; this SOP only fixes the entry conditions.
2. Validate the read-only `task.json` against its schema and confirm the head, branch, and scope before writing.
3. Read the task context's `lane`, `task_branch`, `head`, `tree`, and `scope`. Resolve the worktree path from `policies/worktree-map.json`; never invent a path or create a worktree.
4. Run `git status` in that worktree. Compare head and tree to the task context. A difference is `REFUSED STATE_MOVED`.
5. Confirm the checked-out branch is the one the task context names (`lane/a`, `lane/b`, `spec-comp`, or `task/<task_id>`). Do not create, switch, checkout, or remove a worktree or branch.
6. Confirm the task fits `policies/task-limits.json[task_kind]`. If it exceeds a limit, return `BLOCKED SPLIT_REQUIRED` with `split_groups`; do not start a partial edit.
7. Confirm the scope is writable and non-empty. If the needed path is outside it, return `BLOCKED SCOPE_INSUFFICIENT` with `needed_paths`; never widen the scope yourself.
8. Use scratch only at `builder-scratch/<task_id>` via `factory scratch write`/`factory scratch rm`. Never put scratch files in the worktree.
9. Record the entry state (head, tree, branch, clean) and pass it to `20-implement.md`.
**Routing / handoff**
On success, continue to `20-implement.md` with the same task context. On refusal or block, return the `builder-result` to the orchestrator; it is the only recipient. Builder has no subagent or peer channel, and never reads another lane, the hacker exploit source, or the audit report.
**Policy refs**
`policies/worktree-map.json`, `policies/task-limits.json`, `policies/task-kinds.json`, `schemas/task.json`, `policies/schemas/builder-result.json`, `skills/builder/SKILL.md`, `agents/builder.md`.
**Done / failure codes**
Done: state matches; proceed, the task is not yet `DONE`.
Failure: `REFUSED invalid task context`, `REFUSED STATE_MOVED`, `BLOCKED SPLIT_REQUIRED`, or `BLOCKED SCOPE_INSUFFICIENT`. A refusal is terminal for the turn; do not retry unchanged.
