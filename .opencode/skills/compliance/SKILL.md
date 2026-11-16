---
name: compliance
description: "Evidence index for the compliance role: tools, the SOP set, policy pointers, scratch and output-schema conventions."
metadata:
  opencode/autoinvoke: false
---
# compliance
serves: compliance only.

`skill-level name:` compliance

This is an index, not the procedure. Load it, then read only the SOP file the current situation names.

## Tools

| Tool | Purpose |
| --- | --- |
| `harness.verify` | Verify harness-config and policy-manifest evidence. |
| `drain.validate` | Verify drain stage records for a compliance sweep. |
| `gate.control` | Run or verify gate, policy-digest, drain, and manifest controls. |
| `check.run` | Run the policy-digest check and other evidence checks. |
| `write` / `edit` | Native file write/edit, limited by authz intercepts to this section's artifact pair (`backend/docs/compliance/S<n>.md` + `S<n>.json`). |
| `scratch.write` / `scratch.rm` | Write or remove compliance scratch artifacts. |
| `basemind_*` (`code`, `graph`, `memory`, `web`) | Read-only code search, architecture, prior decisions, external docs. |

## SOP index

| SOP file | Read it when |
| --- | --- |
| `files/sop/compliance/10-review.md` | Auditing one spec section and extracting requirements. |
| `files/sop/compliance/20-drain.md` | A drain lineage needs the compliance evidence sweep. |
| `files/sop/compliance/30-docs.md` | After `10` or `20`: writing the section artifacts and returning the result JSON. |

## Modes

`files/policies/modes.json` is authoritative. This role participates in `COMPLIANCE`, `SPEC_COMPLETION`, `DRAIN`. The mode is named in the dispatch.

## Policies

- `files/policies/policy-manifest.json` — policy digest and manifest scope.
- `files/policies/gate.json` — gate and invariant control targets.
- `files/policies/drain.json` — drain lineage stages.
- `files/policies/checks.json` — check ids and runners.
- `files/policies/schemas/compliance-result.json` — result JSON shape (closed).

Policies are authoritative. This skill only points at them.

## Conventions

- **Scratch:** `compliance-scratch/<capability_id>`, via `scratch.write` / `scratch.rm`.
- **Scope:** spec parsing, requirement extraction, implemented-check, and drain review. Not a gate before every builder task; ordinary build, bugfix, release, and hygiene work never loads this role.
- **Ids:** `REQ-<section>.<n>` for requirements and `DR-<stage>-<n>` for drain items. Product `[R-x.y]` ids are external references and are never minted or renumbered. Scan existing ids before allocating (product docs + `quality-scan/`) and record the evidence in the report.
- **Write scope:** native `write`/`edit` only, allowlisted to `backend/docs/compliance/S[0-9]*.{md,json}` intersected with capability scope and the section stem. The glob admits a tail after the digits, so the capability/section check is the real boundary; any non-section path is a STOP.
- **Carbon:** `carbon` is the MCP server name (`carbon-<role>_*` tools), not the product or a role; never read factory source.
- **Output schema:** `files/policies/schemas/compliance-result.json`.
- **No shell:** there is no shell or terminal by design. If a needed typed tool is absent or rejects input, stop and report the exact error; do not substitute a shell command.
- **Routing edge:** reports to the orchestrator only. No children, no peer messages. The orchestrator routes the next step.
