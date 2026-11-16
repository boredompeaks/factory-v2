---
name: hacker
description: "Offensive index for the hacker role: typed tools, the SOP set, policy pointers, scratch, output schema, and the auditor-dispatched edge."
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
| `basemind_*` (`code`, `graph`, `web`) | Read-only code search, architecture, external docs. |

## SOP index

| SOP file | Read it when |
| --- | --- |
| `files/sop/hacker/10-repro.md` | Establishing a repeatable baseline for a target. |
| `files/sop/hacker/20-exploit.md` | Executing the attack plan and minimizing a proof. |
| `files/sop/hacker/30-blast-radius.md` | Bounding what a successful probe touches. |
| `files/sop/hacker/40-dast.md` | Running ZAP (`DAST_BASELINE`) and schemathesis (`SCHEMA_FUZZ`), with attempt accounting. |
| `files/sop/hacker/50-bug-finding.md` | One bounded sweep batch; build the flow inventory and return it to the auditor. |

## Modes

`files/policies/modes.json` is authoritative. This role participates in `AUDIT`, `BUG_FINDING`, `DRAIN`. The hacker is a child of the auditor and never selects a mode.

## Policies

- `files/policies/attack-catalog.json` — attack classes and plan items.
- `files/policies/severity.json` — finding severity mapping.
- `files/policies/schema-fuzz.json` — SCHEMA_FUZZ scope, status classification, and the explicit per-tier attempt cap (`max_attempts_by_tier`, unit `distinct_execution`).
- `files/policies/redaction.json` — evidence redaction rules.
- `files/policies/schemas/hacker-envelope.json` — sealed input envelope shape.
- `files/policies/schemas/hacker-result.json` — structured outcome shape.
- `files/policies/schemas/bug-report.json` — auditor-owned sweep report; the hacker returns batches, not reports.

Policies are authoritative. This skill only points at them.

## Conventions

- **Carbon:** `carbon` is the MCP server name (`carbon-<role>_*` tools), not the product or a role; never read factory source.
- **Scratch:** `hacker-scratch/<capability_id>`, via `scratch.write` / `scratch.rm`.
- **Output schema:** `files/policies/schemas/hacker-result.json`.
- **No shell:** there is no shell or terminal by design. If a needed typed tool is absent or rejects input, stop and report the exact error; do not substitute a shell command.
- **Routing edge:** dispatched by the auditor only. The result returns through the auditor to the orchestrator. The hacker does not message peers and has no children.
