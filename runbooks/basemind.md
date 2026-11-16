# Basemind runbook — search and code graph

Canonical paths: `basemind.toml`, `files/BASEMIND-INDEX.md`,
`scripts/mcp_discover.py` (discovery). Escalation:
`files/policies/escalation.json`.

## Purpose

Give agents scoped search and symbol context over a workspace without reading
excluded or secret files. Basemind is a retrieval aid, not a policy source; the
repository and policy files remain authoritative.

## Commands

```sh
basemind rescan                    # refresh after large edits
python scripts/mcp_discover.py --server basemind
```

Typed surfaces (grant-dependent): `basemind_code`, `basemind_graph`,
`basemind_memory`, `basemind_web`, `basemind_workspace`; history is
orchestrator-only through `basemind_git`.

## Inputs

- workspace id/path selected from the session
- query, symbol, or file path; mode from the tool schema
- exclusion list from `basemind.toml` `[scan].exclude`

## Outputs

- ranked matches with path/line and symbol context
- workspace index status and cache identity
- empty result (valid) versus tool error (stale/missing index)

## Failure modes

- `watch` daemon holds the index → `scan` refuses a collision; use `rescan`.
- Stale index after a rebase or branch switch → rescan, then retry once.
- `basemind_git` unavailable/denied → expected for non-orchestrator roles; do
  not substitute a raw git history command.
- Excluded-file leak in results → stop and report; do not quote the content.

## Escalate

`agent-platform` owns index/daemon and permission faults; `policy-steward` owns
exclusion-list changes. See `files/policies/escalation.json`.
