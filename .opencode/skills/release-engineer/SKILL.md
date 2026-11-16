---
name: release-engineer
description: "Release index for the release-engineer role: typed tools, the SOP set, policy pointers, scratch and output-schema conventions."
metadata:
  opencode/autoinvoke: false
---
# release-engineer
serves: release-engineer only.

`skill-level name:` release-engineer

This is an index, not the procedure. Load it, then read only the SOP file the current situation names.

## Tools

| Typed tool | Purpose |
| --- | --- |
| `worktrees.reconcile` | Merge the two lane heads into the integration worktree with `--no-ff`. |
| `worktrees.sync` | Reverse-merge the mapped upstream into a lane worktree with `--ff-only`. |
| `worktrees.list` | Read the expected worktree set and state. |
| `git.state` / `git.status` / `git.diff` / `git.log` | Read refs, cleanliness, diff, and ancestry before release actions. |
| `git.push_ref` | Push exactly the named permanent branch, fast-forward only. |
| `pr.open` | Reuse or create the promotion pull request; optional `body` carries ids and hashes only (omitted => empty body). |
| `branch.protection` | Read live branch protection for the promotion base, compare it to policy, and apply=true only to repair the promotion base. |
| `pr.watch` | Watch required checks exactly once. |
| `pr.merge` | Merge with the head SHA pinned. |
| `drain.validate` | Verify the drain permanent guard before merge. |
| `ledger.append` | Record the release result. |
| `scratch.write` / `scratch.rm` | Write or remove release scratch artifacts. |
| `basemind_*` (`code`, `graph`, `web`) | Read-only code search, architecture, external docs. |

## SOP index

| SOP file | Read it when |
| --- | --- |
| `files/sop/release-engineer/10-promote.md` | Reconciling lanes, pushing the integration branch, and opening or reusing the PR. |
| `files/sop/release-engineer/20-merge.md` | Watching required checks once and merging with the pinned head. |
| `files/sop/release-engineer/30-ci.md` | Interpreting CI state and required-check failures. |
| `files/sop/release-engineer/40-rollback.md` | A release must be halted, reversed, or reported as unrecoverable. |

## Modes

`files/policies/modes.json` is authoritative. This role participates in `RELEASE`, `DRAIN`. `RELEASE` is the only GATE mode; the release-engineer never selects or changes the mode.

## Policies

- `files/policies/release-predicates.json` — every named release predicate; the only definition, cited by id from the SOPs.
- `files/policies/phases.json` — the GATE phase and the release-engineer-exclusive rule.
- `files/policies/ci.json` — merge method and release mechanics.
- `files/policies/ci-required.json` — required check set.
- `files/policies/branch-protection.json` — sealed branch-protection record.
- `files/policies/worktree-map.json` — permanent lane and integration topology.
- `files/policies/drain.json` — drain lineage and permanent guard.
- `files/policies/tiers.json` — the tier cycle cap for the red-check repair loops.
- `files/policies/escalation.json` — escalation owner and channel at the cap.
- `files/policies/schemas/release-result.json` — release record shape.

Policies are authoritative. This skill only points at them.

## Conventions

- **Carbon:** `carbon` is the MCP server name (`carbon-<role>_*` tools), not the product or a role; never read factory source.
- **Scratch:** `release-engineer-scratch/<capability_id>`, via `scratch.write` / `scratch.rm`.
- **Output schema:** `files/policies/schemas/release-result.json`.
- **CI config ownership:** `.github/workflows/**` is this role's to edit and no other role's. Ownership is not gate authority: a release never edits a workflow, the required set, or a check to make a head green.
- **No shell:** there is no shell or terminal by design. If a needed typed tool is absent or rejects input, stop and report the exact error; do not substitute a shell command.
- **Routing edge:** reports to the orchestrator only. No children, no peer messages. The orchestrator routes the next step, including a repaired head back to this role.
