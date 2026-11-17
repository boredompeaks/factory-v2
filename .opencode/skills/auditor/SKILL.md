---
name: auditor
description: "Verification index for the auditor role: typed tools, the SOP set, policy pointers, scratch, output schema, and the auditor-to-hacker edge, which is used only for a BUG_FINDING sweep."
metadata:
  opencode/autoinvoke: false
---
# auditor
serves: auditor only.

`skill-level name:` auditor

This is an index, not the procedure. Load it, then read only the SOP file the current situation names.

## Tools

| Typed tool | Purpose |
| --- | --- |
| `check.run` | Run the tier's checks against the sealed tree in the sandbox. |
| `gate.control` | Run or verify gate, invariant, policy-digest, drain, and manifest controls. |
| `repro.run` | Re-run a claimed finding on the ledger copy before admission. |
| `acceptance.materialize` | Materialize a deterministic repro-spec into a test file. |
| `k1.up` / `k1.status` / `k1.exec` / `k1.down` | Bring up, poll, exercise, and tear down one K1 run. |
| `drain.validate` | Verify drain stages before verdict or release. |
| `evidence.hash` | Content-hash evidence files at a validated rev. |
| `git.state` / `git.status` / `git.diff` / `git.log` / `git.read_file` | Read refs, working tree, diffs, ancestry, and file contents. |
| `git.push_ref` | Push the audited branch once on PASS for durability only. |
| `ledger.append` | Record a verdict, finding, attack log, or gate control. |
| `docker.ensure` | Ensure the container engine is running; never pulls or builds. |
| `scratch.write` / `scratch.rm` | Write or remove auditor scratch artifacts. |
| `basemind_*` (`code`, `graph`, `web`) + `basemind_memory` | Read-only code search, architecture, external docs; `basemind_memory` reads prior decisions and records lessons. |

## SOP index

| SOP file | Read it when |
| --- | --- |
| `files/sop/auditor/10-audit.md` | Starting verification; capability and evidence intake. |
| `files/sop/auditor/15-delta.md` | Re-auditing a P2/P3 repair at a new head in the same context; delta report. |
| `files/sop/auditor/20-checks.md` | Running tier checks and gate controls. |
| `files/sop/auditor/30-hacker-relay.md` | A `BUG_FINDING` sweep: the hacker campaign envelope and relay. |
| `files/sop/auditor/40-quality.md` | Triaging and reproducing findings and quality evidence. |
| `files/sop/auditor/50-verdict.md` | Computing and recording the verdict from the fixed rule. |
| `files/sop/auditor/60-ship.md` | PASS boundary; durability push and release handoff. |

## Modes

`files/policies/modes.json` is authoritative. This role participates in `AUDIT`, `DELTA_AUDIT`, `BUG_FINDING`, `DRAIN`, `EVALUATION`. The mode is named in the dispatch; the auditor never selects or changes it.

## Policies

- `files/policies/checks.json` — required checks and runners.
- `files/policies/gate.json` — gate and invariant control targets.
- `files/policies/severity.json` — finding severity mapping.
- `files/policies/escalation.json` — escalation classes.
- `files/policies/drain.json` — drain lineage stages.
- `files/policies/negative-controls.json` — required negative controls.
- `files/policies/schemas/auditor-verdict.json` — verdict shape.
- `files/policies/schemas/bug-report.json` — one-sweep coverage report shape; the auditor owns it.

Policies are authoritative. This skill only points at them.

## Conventions

- **Carbon:** `carbon` is the MCP server name (`carbon-<role>_*` tools), not the product or a role; never read factory source.
- **Scratch:** `auditor-scratch/<capability_id>`, via `scratch.write` / `scratch.rm`.
- **Institutional memory:** `basemind_memory` (MCP) / `basemind memory put|get|list|search` (CLI), scoped to the factory repo root. Read at orientation filtered by your role tag; record a lesson/gotcha/seam/decision at task end. Key `factory/<role>/<kind>/<task_id>/<slug>`, tags `role:`, `task:`, `kind:`. Memory is advice, never evidence: re-run the reproducer before citing it. Per-task scratch is ephemeral.
- **Product scope:** migrations, requirements/dependency pins, Docker/compose, settings/env, and all product code are product scope; falsify them with `k1.exec{action:"migrate"}` / `k1.exec{action:"makemigrations check"}`, `check.run({check_id:"BOOTTEST"})`, and, at max, `check.run({check_id:"DAST_BASELINE"})`, never by accepting the builder's diff.
- **Output schema:** `files/policies/schemas/auditor-verdict.json`.
- **Sweep output schema:** `files/policies/schemas/bug-report.json`; one auditor-led sweep returns one report.
- **No shell:** there is no shell or terminal by design. If a needed typed tool is absent or rejects input, stop and report the exact error; do not substitute a shell command.
- **Hacker campaign:** in `BUG_FINDING` only, one campaign per sweep over the whole target, never one campaign per task or one child per batch. `AUDIT` and `DELTA_AUDIT` have no hacker child.
- **Routing edge:** reports to the orchestrator. The only child edge is auditor -> hacker, used only for a `BUG_FINDING` sweep; the campaign result returns through the auditor to the orchestrator. The orchestrator never dispatches the hacker, the auditor does not message peers, and no other parent/child edge exists.
