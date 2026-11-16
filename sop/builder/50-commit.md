# 50 — Commit
**Mode:** `BUILD`
**Purpose** Commit exactly the scoped change set on the assigned branch, with the task context binding, and confirm the resulting state.
**Inputs**
- Green precheck from `40-precheck.md`.
- The changed file list, acceptance hashes, added-test table, and changelog ref.
- `schemas/task.json`, `policies/schemas/builder-result.json`, `policies/changelog.json`.
**Procedure**
1. Run `git status` in the assigned worktree. Confirm the only changes are the intended scoped paths and that no scratch, secret, generated, out-of-scope file, or modified read-only exploit/repro test is present.
2. Run `git diff` at validated refs and read the staged change set. A path outside `task context` is a block; do not commit around it. Confirm each edited function or block carries the task id and that no comment narrates the diff.
3. Compose the message as `<scope>: <summary>` in the repository's existing style. It describes the change, not the process.
4. Call `git commit` with the task context id, the task context tree, and the explicit path list. Never use a bypass, force, amend, or a path list wider than the reviewed set. The server validates scope and test-infrastructure denies.
5. Confirm the result with `git log` and `git status`. Record the new head, tree, and branch; a commit that did not change head is a failure.
6. Never push, merge, rebase, cherry-pick, or open/comment/close a pull request. Durability push belongs to the auditor; promotion belongs to the release-engineer.
7. If the commit is refused, preserve the worktree and return the exact refusal. Do not reset, discard, or retry with a different path list.
8. Return the `builder-result`: result type, task context id, base/head/tree, files, added tests, removed tests, guard delta, architecture declarations with their `declaration_ref` (`backend/docs/requests.md#anchor`), `ci_request` (null unless a CI/workflow change is required), and changelog row ref.
**Routing / handoff**
Return to the orchestrator; the auditor is next. The builder does not notify the auditor, release-engineer, or any peer directly.
**Policy refs**
`schemas/task.json`, `policies/schemas/builder-result.json`, `policies/changelog.json`, `policies/task-kinds.json`, `policies/gate.json`, `agents/builder.md`, `skills/builder/SKILL.md`.
**Done / failure codes**
Done: one commit on the assigned branch, scoped paths only, new head/tree recorded, clean state confirmed.
Failure: `BLOCKED SCOPE_INSUFFICIENT` on a refused path, `REFUSED STATE_MOVED` if head/tree moved, or the exact commit-tool refusal; no bypass.
