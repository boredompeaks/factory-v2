# Factory v2

Factory v2 is a stripped, copy-only agent factory: it keeps the policies,
workflow cards, gates, skills, and censoring controls while replacing signed
capability tokens and typed MCP operations with an unsigned `task.json`, shell,
and native agent tools. Work here is a copy; the source tree is read-only, and
omitted components are simply not copied. Nothing in the source is deleted.

## Quickstart

From a task worktree, create a task, make a task-tagged commit, then run the
harness lifecycle:

```sh
factory task new --kind FEATURE --lane lane/a --scope 'backend/**' --worktree "$PWD"
# make and test the change
git add .
git commit -m "Implement the task" -m "Task-Id: <id-from-task.json>"

# fast feedback (narrowed, never release-grade)
factory gate --checks SCOPE,CONVENTION

# authoritative release gate and proof check
factory gate --release
factory verify

# dry-run the merge, then release from the integration worktree
factory release --dry-run --repo /path/to/integration
factory task phase GATE --evidence gate:<record>
factory release --repo /path/to/integration
```

`factory gate --checks` may only narrow the policy gate. A real release refuses
a narrowed gate; `--release` always runs the full policy set. `factory` infers
the task from the current worktree when `--task-id` and `FACTORY_TASK_ID` are
absent, so `factory scope check --staged` works inside the pre-commit hook.

The task file is plain JSON and is not an authorization token. The harness
chooses the applicable checks, computes proofs itself, and records them outside
the worktree. Proofs use canonical JSON; the proof root is derived from the
sorted proof set. Releases require passing evidence for the current base, head,
tree, scope, and policy state.

## Sandbox and censor

Use `bin/factory-run --worktree PATH -- COMMAND ...` to run a command in a
Bubblewrap sandbox. The wrapper gives the worktree write access, mounts source
and evidence read-only, and exposes only the designated OpenCode/cache
directories as writable; it never binds the entire home directory writable.
`--unshare-net` disables network access, and `--no-sandbox` is reserved for
trusted owner operations. Bubblewrap must be installed for sandboxed execution.

The AGT censor (`plugins/agt.js` and `.agt/policy.json`) is a secondary
protection layer for tool use: it redacts sensitive output and blocks selected
secret reads, factory writes, unsafe download-and-execute patterns, metadata
access, and recursive deletion. Its audit log is not proof or gate evidence.
Policy cards under `policies/` are the visible source of truth for workflow,
task, and gate rules.

## Reproducibility and safety

Gate proofs record the relevant revisions, tool/environment details, and
outputs. Randomized checks use a fresh random seed per run and record that seed
in the proof so the run can be replayed; seeds are not fixed or enumerated.
Canonicalization excludes timing fields from the proof root.

**No-deletion guarantee:** factory operations are additive. They do not remove
source files, rewrite source history, or silently discard work. A component
not present in this stripped copy was not copied; it was not deleted from its
origin.
