---
name: builder
description: "Implementation index for the builder role: allowed tools, the SOP set, policy pointers, scratch and output-schema conventions."
metadata:
  opencode/autoinvoke: false
---
# builder
serves: builder only.

`skill-level name:` builder

This is an index, not the procedure. Load it, then read only the SOP file the current situation names.

## Tools

| Tool | Purpose |
| --- | --- |
| `write` / `edit` | Write worktree files inside the capability scope. The server intercepts scope, test-infra, secret, and policy paths; a rejected path is terminal. |
| `git.state` / `git.status` / `git.diff` / `git.log` | Read head, branch, cleanliness, diff, and ancestry at validated refs. |
| `git.commit` | Commit the staged, scope-checked diff on the assigned branch. |
| `check.run` | Run the tier's deterministic checks in the sandbox. |
| `repro.run` | Execute ledger-copy reproducers for falsification. |
| `evidence.hash` | Content-hash evidence files at a validated rev. |
| `docker.ensure` | Ensure the container engine is running; never pulls or builds. |
| `scratch.write` / `scratch.rm` | Write or remove builder scratch artifacts. |
| `basemind_*` (`code`, `graph`, `web`) | Read-only code search, architecture, external docs. |

## SOP index

| SOP file | Read it when |
| --- | --- |
| `files/sop/builder/10-worktree.md` | Before the first edit; confirm the assigned worktree, branch, and head. |
| `files/sop/builder/20-implement.md` | Implementing the scoped change. |
| `files/sop/builder/30-tests.md` | Adding or strengthening tests and proving falsifiability. |
| `files/sop/builder/40-precheck.md` | Before commit; run deterministic checks and review scope. |
| `files/sop/builder/50-commit.md` | Committing and assembling the handoff evidence. |
| `files/sop/builder/60-repair.md` | Reworking after audit findings or a failed precheck. |

## Modes

`files/policies/modes.json` is authoritative. This role participates in `BUILD` (the mode named by the orchestrator; this role never selects or changes it).

## Policies

- `files/policies/task-kinds.json` — task kind and tier.
- `files/policies/task-limits.json` — file and model limits for the task kind.
- `files/policies/checks.json` — required check ids and runners.
- `files/policies/worktree-map.json` — assigned lane and integration topology.
- `files/policies/test-removal-reasons.json` — allowed reasons for touching test coverage.
- `files/policies/schemas/builder-result.json` — handoff shape.

Policies are authoritative. This skill only points at them.

## Conventions

- **Carbon:** `carbon` is the MCP server name (`carbon-<role>_*` tools), not the product or a role; never read factory source.
- **Scratch:** `builder-scratch/<capability_id>`, via `scratch.write` / `scratch.rm`; never put scratch artifacts in the worktree.
- **Comments:** record the non-obvious why, never a narration of the diff; put the task id in the function or block edited.
- **Test ownership:** exploit/repro tests are read-only inputs authored by the auditor or hacker; unit tests are yours, added under the product test tree when `capability.scope` names them. Test infrastructure (`pytest.ini`, `tox.ini`, `setup.cfg`, `conftest.py`, `.coveragerc`, `Makefile`, `scripts/test*`, `.github/workflows/**`, and any fixture module imported by a test you did not author) is writable only when `capability.scope` names it; builder never seeks that grant for test configuration, shared fixtures, or CI. `.github/**` is owned by release-engineer and builder never edits it. Run a returned probe, never edit, weaken, skip, or xfail it.
- **Product scope:** migrations, requirements/dependency pins, Docker/compose, settings/env, and all product code are product scope written by builder inside `capability.scope`; audit falsifies them with `SUITE`/`BOOTTEST` plus the K1 migrate and makemigrations checks.
- **CI request:** if a required check is red because a workflow, CI config, dependency pin, or check definition is missing/defective, the builder never edits `.github/**` or the required set; it sets `ci_request` in `builder-result` (kind, reason, paths, evidence_ref) and returns `BLOCKED PRECHECK_UNRESOLVED` for the orchestrator to route.
- **Output schema:** `files/policies/schemas/builder-result.json`.
- **No shell:** there is no shell or terminal by design. If a needed tool is absent or rejects input, stop and report the exact error; do not substitute a shell command.
- **Routing edge:** reports to the orchestrator only. No children, no peer messages. The orchestrator routes the next step.
