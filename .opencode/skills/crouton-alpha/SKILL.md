---
name: crouton-alpha
description: "Documentation index for the crouton-alpha role: native writes, the SOP set, policy pointers, scratch and output-schema conventions."
metadata:
  opencode/autoinvoke: false
---
# crouton-alpha
serves: crouton-alpha only.

`skill-level name:` crouton-alpha

This is an index, not the procedure. Load it, then read only the SOP file the current situation names.

## Tools

| Tool | Purpose |
| --- | --- |
| `write` / `edit` | Native file write/edit, limited by authz intercepts to `backend/docs/**` ∩ `capability.scope`. |
| `git.state` | Read the current ref and branch before and after a docs change. |
| `scratch.write` / `scratch.rm` | Write or remove crouton-alpha scratch artifacts. |
| `basemind_*` (`code`, `graph`, `web`) + `basemind_memory` | Read-only code search, architecture, external docs; `basemind_memory` reads and writes shared institutional memory. |

## SOP index

| SOP file | Read it when |
| --- | --- |
| `files/sop/crouton-alpha/10-docs.md` | Authoring or revising a documentation page; continues in `20-ux.md`. |
| `files/sop/crouton-alpha/20-ux.md` | Reviewing information architecture, scan path, and examples; continues in `30-handoff.md`. |
| `files/sop/crouton-alpha/30-handoff.md` | Final review and handoff note for a docs change; returns the result object. |

## Modes

`files/policies/modes.json` is authoritative. This role participates in `DOCS`. The mode is named in the dispatch.

## Policies

- `files/policies/redaction.json` — evidence and prose redaction rules.
- `files/policies/changelog.json` — changelog and docs-adjacent conventions.
- `files/policies/schemas/crouton-result.json` — result JSON shape (closed).

Policies are authoritative. This skill only points at them.

## Conventions

- **Carbon:** `carbon` is the MCP server name (`carbon-<role>_*` tools), not the product or a role; never read factory source.
- **Scratch:** `crouton-alpha-scratch/<capability_id>`, via `scratch.write` / `scratch.rm`.
- **Output schema:** `files/policies/schemas/crouton-result.json`.
- **No shell:** there is no shell or terminal by design. If a needed tool is absent or rejects input, stop and report the exact error; do not substitute a shell command or an MCP write tool.
- **Routing edge:** reports to the orchestrator only. No children, no peer messages. The orchestrator routes the next step.
