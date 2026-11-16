# Factory v2 — copy manifest

Source is read-only. All operations are `cp`/`rsync` into
`/tmp/opencode/factory-v2/`.

## Copy as-is

- `files/skills/**` → `skills/`
- `files/sop/**` → `sop/` (edited later in the copy)
- `files/docker/**` → `docker/`
- `files/runbooks/{k1,services,test-runner,release,pr,worktrees,basemind}.md` → `runbooks/`
- `files/tools/{smell,arch_check,authz_matrix,guard_delta,oracle_provenance}.py` → `tools/`
- `scripts/{services_up,services_run,services_down,services_health,services_reap}.sh`, `scripts/k1_build.sh`, `scripts/audit_test_runner.sh`, `scripts/verify_workspace.sh` → `scripts/`
- `.opencode/plugins/agt.js` → `plugins/agt.js`
- `basemind.toml` → `basemind.toml`
- `files/BASEMIND-INDEX.md` → `BASEMIND-INDEX.md`
- `CLAUDE.local.md` → `CLAUDE.local.md`

## Copy + edit (in the copy only)

- `files/<role>.md` (7) → `agents/<role>.md`
  - permissions: shell + native tools, factory read-only
  - remove H/B harness-contract blocks
  - typed tool calls → `factory`
- `opencode.json` → `opencode.json`
  - remove 7 carbon MCP servers
  - keep basemind, chrome-devtools, context7
  - plugins: `agt.js` only
- `.agt/policy.json` → `.agt/policy.json`
  - shell + native tools allowed
  - targeted denies: secrets, factory writes, curl|sh, metadata, rm -rf
- `files/policies/*.json` + `semgrep.yml` → `policies/`
  - keep all except `policy-manifest.json` and `negative-controls.json`
  - explicitly keep: phases, dispatch-rules, modes, concurrency, mutation, schema-fuzz
- `files/policies/schemas/**` → `policies/schemas/` (reference)
- `files/runbooks/{gate,ledger,mcp}.md` → `runbooks/` (rewrite to the CLI)

## Not copied (left in source)

- `files/tools/mcp/**`
- `files/tools/{gate_run,gate_control,policy_digest,agt_audit,agt_parity}.py`
- `.opencode/lib/authz-core.js`, `.opencode/plugins/authz.js`
- `files/policies/{policy-manifest,negative-controls}.json`
- `files/quality/ledger/**`
- `scripts/{policy_manifest,check_harness_config,config_controls,control_mutation,gate_all_controls,mcp_discover,scan_sessions,backfill_register,bug_lint}.py`
- `scripts/test_*.py` (25)
- `drafts/`, `audit-scratch/`, `phase6/`, `phase7/`, `quality-scan/`, `report/`

## New files (own code)

- `bin/factory`, `bin/factory-run`
- `factory/**`
- `hooks/**`
- `schemas/{task,proof,gate}.json`
- `tests/**`
- `README.md`
