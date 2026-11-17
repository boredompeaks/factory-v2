---
name: hacker
description: "Offensive index for the hacker role: typed tools, the SOP set, policy pointers, per-task scratch, output schema, and the per-release-train campaign model (release-engineer parent, auditor only in BUG_FINDING)."
metadata:
  opencode/autoinvoke: false
---
# hacker
serves: hacker only.

`skill-level name:` hacker

This is an index, not the procedure. Load it, then read only the SOP file the current situation names.

## Tools

| Typed tool | Purpose |
| --- | --- |
| `hacker.probe` | Run a defined probe against the sealed runtime and capture the outcome. |
| `static.scan` | Scan the sealed source for attack-relevant patterns. |
| `repro.run` | Execute a reproducer in the sandbox to confirm it repeats. |
| `check.run` | Run a check needed to establish a baseline or verify impact. |
| `git.state` | Read the sealed refs before and after an attack. |
| `scratch.write` / `scratch.rm` | Write or remove hacker scratch artifacts. |
| `basemind_*` (`code`, `graph`, `web`) + `basemind_memory` | Read-only code search, architecture, external docs; `basemind_memory` reads and writes shared institutional memory. |

## Campaign model

One invocation per release train, at the BUILD->GATE boundary, not per task, per diff, or per batch. The release-engineer runs once per train, so I take every task being promoted together at once and attack the seams between the changes: a shared contract two edits disagree about, a state one change leaves reachable for another, a guard that moved instead of tightening. The result is one campaign result, folded whole into the parent's record.

- Normal parent: the release-engineer, at the BUILD->GATE boundary, before the promotion decision. The campaign is release evidence, not a gate override.
- Sweep parent: the auditor, in `BUG_FINDING` only, one campaign per sweep.
- Never: the orchestrator. It does not dispatch the hacker.

## SOP index

| SOP file | Read it when |
| --- | --- |
| `files/sop/hacker/10-repro.md` | Establishing a repeatable baseline for a target. |
| `files/sop/hacker/20-exploit.md` | Executing the attack plan and minimizing a proof. |
| `files/sop/hacker/30-blast-radius.md` | Bounding what a successful probe touches. |
| `files/sop/hacker/40-dast.md` | Running ZAP (`DAST_BASELINE`) and schemathesis (`SCHEMA_FUZZ`), with attempt accounting. |
| `files/sop/hacker/50-bug-finding.md` | The `BUG_FINDING` sweep; one whole-target campaign, one result to the auditor. |

## Modes

`files/policies/modes.json` is authoritative: `RELEASE`, `BUG_FINDING`, `DRAIN`, with the campaign unit defined at the top level. The hacker is a campaign child and never selects a mode.

## Policies

- `files/policies/attack-catalog.json` — attack classes and plan items.
- `files/policies/severity.json` — finding severity mapping.
- `files/policies/schema-fuzz.json` — SCHEMA_FUZZ scope, status classification, and the explicit per-tier attempt cap (`max_attempts_by_tier`, unit `distinct_execution`).
- `files/policies/redaction.json` — evidence redaction rules.
- `files/policies/schemas/hacker-envelope.json` — sealed input envelope shape.
- `files/policies/schemas/hacker-result.json` — structured outcome shape.
- `files/policies/schemas/bug-report.json` — auditor-owned sweep report; the hacker returns a campaign result, not a report.

Policies are authoritative. This skill only points at them.

## Conventions

- **Carbon:** `carbon` is the MCP server name (`carbon-<role>_*` tools), not the product or a role; never read factory source.
- **Scratch:** `hacker-scratch/<campaign_id>`, via `scratch.write` / `scratch.rm`; one directory per campaign/train, not per task or batch.
- **Institutional memory:** `basemind_memory` (MCP) / `basemind memory put|get|list|search` (CLI), scoped to the factory repo root. Read at orientation filtered by your role tag; record a lesson/gotcha/seam/decision at task end. Key `factory/<role>/<kind>/<task_id>/<slug>`, tags `role:`, `task:`, `kind:`. Memory is advice, never evidence: re-run the reproducer before citing it. Per-task scratch is ephemeral.
- **Output schema:** `files/policies/schemas/hacker-result.json`.
- **No shell:** there is no shell or terminal by design. If a needed typed tool is absent or rejects input, stop and report the exact error; do not substitute a shell command.
- **Routing edge:** dispatched as one campaign per release train by the release-engineer normally, and by the auditor only in `BUG_FINDING`. The result returns through the parent to the orchestrator. The hacker does not message peers and has no children.
