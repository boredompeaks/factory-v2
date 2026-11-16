---
name: orchestrator
description: "Routing index for the orchestrator role: typed tools, the SOP set, policy pointers, scratch and output-schema conventions."
metadata:
  opencode/autoinvoke: false
---
# orchestrator
serves: orchestrator only.

`skill-level name:` orchestrator

This is an index, not the procedure. Load it, then read only the SOP file the current situation names.

## Tools

| Typed tool | Purpose |
| --- | --- |
| `subagent` | Spawn one child session for a routed role; only the orchestrator routes. |
| `skill` | Load this role skill or a supporting skill's instructions. |
| `question` | Ask the owner one blocking clarification. |
| `harness.verify` | Check harness/config invariants before dispatch. |
| `gate.control` | Run or verify gate, invariant, policy-digest, drain, and manifest controls. |
| `drain.validate` | Verify a drain stage record before routing on it. |
| `worktrees.list` | Read the expected worktree set and its state. |
| `git.state` / `git.status` / `git.diff` / `git.log` | Read live repository state for routing predicates. |
| `ledger.append` | Record a dispatch or routing decision. |
| `scratch.write` / `scratch.rm` | Write or remove orchestrator scratch artifacts. |
| `basemind_*` (`code`, `graph`, `memory`, `web`, `workspace`) | Read-only context for code, architecture, prior decisions, external docs, workspace layout. |

## SOP index

| SOP file | Read it when |
| --- | --- |
| `files/sop/orchestrator/00-intake.md` | A new request arrives and must become a task shape with acceptance refs. |
| `files/sop/orchestrator/10-resume.md` | An existing lineage continues and recorded state must be reconciled with the live repo. |
| `files/sop/orchestrator/20-bugfix.md` | The request is a defect with a known failing behavior; P2/P3 repair reuses the builder context and the auditor's delta pass. |
| `files/sop/orchestrator/30-feature.md` | The request adds behavior and scope or acceptance needs settling. |
| `files/sop/orchestrator/40-spec-completion.md` | A spec section is partially built and the remaining gap set needs routing. |
| `files/sop/orchestrator/50-release.md` | A verdict is PASS and the release boundary is next. |
| `files/sop/orchestrator/60-drain.md` | An inherited session has uncommitted work and the queue must be drained. |
| `files/sop/orchestrator/70-evaluator.md` | The task needs rubric evaluation rather than a build verdict. |
| `files/sop/orchestrator/80-bug-finding.md` | Exploratory defect search: dispatch one auditor-led sweep with target and budget. |
| `files/sop/orchestrator/90-escalation.md` | A predicate is stuck, contradictory, or needs owner authority. |

## Modes

`files/policies/modes.json` is authoritative. This index names the modes this role may run; the SOP named by the mode is the procedure.

| Mode | SOP |
| --- | --- |
| `INTAKE` | `00-intake.md` |
| `RESUME` | `10-resume.md` |
| `FEATURE` | `30-feature.md` |
| `BUGFIX` | `20-bugfix.md` |
| `SPEC_COMPLETION` | `40-spec-completion.md` |
| `BUG_FINDING` | `80-bug-finding.md` |
| `DRAIN` | `60-drain.md` |
| `EVALUATION` | `70-evaluator.md` |
| `RELEASE` | `50-release.md` |
| `ESCALATION` | `90-escalation.md` |
| `PARK` | `90-escalation.md` |

`BUILD`, `AUDIT`, `DELTA_AUDIT`, `COMPLIANCE`, and `DOCS` are the role modes the orchestrator dispatches into; the orchestrator does not run them itself.

## Policies

- `files/policies/task-kinds.json` — task shape and tier selection.
- `files/policies/task-limits.json` — per-kind limits used when framing a dispatch.
- `files/policies/models.json` — model family and role assignment constraints.
- `files/policies/gate.json` — gate and invariant control targets.
- `files/policies/escalation.json` — escalation classes and owner authority.
- `files/policies/worktree-map.json` — permanent lane and integration topology.
- `files/policies/phases.json` — BUILD/GATE state machine, lane exclusivity, and release-train criteria.
- `files/policies/modes.json` — the visible mode registry; every decision names one mode and one phase.
- `files/policies/drain.json` — drain lineage stages.
- `files/policies/schemas/dispatch.json` and `files/policies/schemas/capability.json` — dispatch and capability shapes.
- `files/policies/schemas/bug-report.json` — the one report a bug-finding sweep returns.

Policies are authoritative. This skill only points at them.

## Conventions

- **Carbon:** `carbon` is the MCP server name (`carbon-<role>_*` tools), not the product or a role; never read factory source.
- **Scratch:** `orchestrator-scratch/<capability_id>`, via `scratch.write` / `scratch.rm`.
- **Output schema:** `files/policies/schemas/orchestrator-decision.json`; `phase` and `lane` are required and are validated by `ledger.append` against `files/policies/phases.json`.
- **No shell:** there is no shell or terminal by design. If a needed typed tool is absent or rejects input, stop and report the exact error; do not substitute a shell command.
- **Routing edge:** the orchestrator routes all work. Every subagent returns to it; no subagent messages a peer. The auditor is the only subagent with a child: auditor -> hacker, only when the auditor's SOP says so. The hacker's result returns through the auditor to the orchestrator. No other parent/child edge exists.
