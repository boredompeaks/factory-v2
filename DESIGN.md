# Factory v2 — design contract

Scope: `/tmp/opencode/factory-v2/`. Source `/home/boredompeaks/umairnlp` and
`/home/boredompeaks/backend-worktrees` are READ-ONLY. Nothing is deleted.

## 0. Decisions locked by the owner

- Additive only: v2 is a copy. "Drop" means "not copied".
- Keep: censor, policies, gates, determinism, skills.
- Keep: `phases.json`, `dispatch-rules.json`, `modes.json`, `concurrency.json`,
  `mutation.json`, `schema-fuzz.json`.
- Drop: signed capability tokens (replace with unsigned `task.json`), typed MCP
  tools (replace with shell + native tools).
- Proofs are computed by the harness only. Agent claims are not evidence.
- Fuzz seeds are RANDOM per run (non-enumerable) and RECORDED in the proof so a
  run is replayable from the proof. No fixed seeds.
- Agents get shell AND native tools: `bash`, `read`, `write`, `edit`, `grep`,
  `glob`, `skill`, `webfetch`, `websearch`.

## 1. Layout

```
bin/factory                 CLI entrypoint
bin/factory-run             bwrap wrapper
factory/                    Python package (stdlib + optional jsonschema)
  cli.py task.py state.py scope.py gate.py proof.py release.py util.py
  policy_cards.py
  checks/static.py tests.py mutation.py fuzz.py dast.py
policies/                   copied policies (source of truth)
agents/                     copied personas, edited for shell+native
sop/                        copied SOPs, edited for the CLI
skills/                     copied skills
runbooks/                   copied + rewritten runbooks
docker/                     copied K1 + services
tools/                      copied standalone check bodies
scripts/                    copied service/K1 scripts
hooks/                      pre-commit, commit-msg, post-commit, install.sh
schemas/                    task.json, proof.json, gate.json
evidence/                   runtime; gitignored; harness-written
tests/                      smoke.sh, test_cli.py
.agt/policy.json            censor policy (AGT)
opencode.json               host config
```

## 2. Task lifecycle (no capability tokens)

`task.json` (unsigned, read-only to the agent):

```json
{
  "task_id": "T-20261115-001",
  "kind": "FEATURE|FIX|SECURITY|DOC",
  "lane": "lane/a|lane/b|spec-comp",
  "worktree": "/home/boredompeaks/backend-worktrees/spec-comp-wt",
  "branch": "task/T-20261115-001",
  "base_sha": "<40-hex>",
  "scope": ["backend/orders/**", "backend/tests/test_orders.py"],
  "phase": "BUILD|GATE",
  "mode": "<modes.json id>",
  "tier": "none|high|max",
  "created_at": "<iso8601>"
}
```

`factory task new|show|phase|mode|claim` validates against
`phases.json#transitions`, `modes.json#modes`, `dispatch-rules.json#rules`,
and `concurrency.json` (lane/K1 slots). It writes plain JSON + a lock file; no
hash chain, no seals.

## 3. Gate sets (harness chooses, not the agent)

| Kind | Checks |
|---|---|
| DOC | SCOPE, CHANGELOG, SECRETS, CONVENTION |
| FIX/FEATURE | SCOPE, SUITE, TEST_SET, TEST_INFRA, BASE_FAIL_HEAD_PASS, MUTATION, SECRETS, CONVENTION, SMELL, SEMGREP, ARCH, RADON, BANDIT, ZIZMOR |
| SECURITY | above + AUTHZ_MATRIX, GUARD_DELTA, ORACLE_PROVENANCE, DAST_BASELINE |
| release | above + THREAD_RACE, SCHEMA_FUZZ, BOOTTEST, POLICY_DIGEST |

The live table is `policies/gate.json#gate_sets_by_kind`; the CLI reads it and
does not hardcode a second copy.

`gate.json` may narrow but never widen. `shadow: true` checks are recorded but
do not block unless `factory gate --strict-shadow` is set.

## 4. Proof model

Every check returns a `Proof`:

```json
{
  "check": "SEMGREP",
  "task_id": "T-...",
  "base": "<sha>", "head": "<sha>", "tree": "<sha>",
  "command": ["semgrep", "--config", "policies/semgrep.yml", "--json", "."],
  "tool": {"name": "semgrep", "version": "1.x", "image": "k1@sha256:..."},
  "env": {"TZ": "UTC", "PYTHONHASHSEED": "0", "network": "none"},
  "seed": null,
  "exit": 0,
  "stdout_sha256": "...",
  "artifacts": [{"path": "semgrep.json", "sha256": "..."}],
  "result": "PASS|FAIL|SKIP|TOOL_ABSENT|FLAKY",
  "detail": "..."
}
```

- Proofs are written to `~/.factory/evidence/<task_id>/proofs/<CHECK>.json`
  (outside the worktree). Under bwrap the agent sees `/evidence` read-only.
- `proof_root = sha256(canonical_json(sorted(proofs)))`.
- `release.json` carries `proof_root`, `policy_hash`, `base`, `head`, `tree`.
- The release commit trailer carries `Proof-Root: <sha>`.
- Re-running `factory gate` on the same base/head/tree must reproduce the root
  except for `seed` (recorded) and timing fields (excluded from the hash).

Canonicalization: `json.dumps(obj, sort_keys=True, separators=(",", ":"))`,
UTF-8, no trailing newline. Timing/duration fields are excluded from the hash.

## 5. Determinism rules

- `base`, `head`, `tree` recorded.
- `TZ=UTC`, `PYTHONHASHSEED=0`, `LC_ALL=C`.
- Tool versions and K1 image digest recorded.
- Static checks run with network off (`bwrap --unshare-net` when available).
- `policy_hash = sha256(canonical_json({path: sha256(file)} for policies/**))`
  computed before and after; mismatch fails the gate.
- Fuzz seeds: `secrets.randbits(64)` per run, recorded as `seed`, replayable via
  `--seed`. Never fixed, never enumerated.
- Flaky checks: record every run; `FLAKY` fails unless the policy's flake budget
  allows it.

## 6. Censor (AGT)

- `plugins/agt.js` is copied unchanged (V2 shim).
- `.agt/policy.json`: `defaultEffect: "allow"`, targeted denies for secret
  reads, factory writes, `curl|sh`, metadata IPs, recursive deletes.
- Keep `execute.after` redaction and prompt defense; drop the two custom AGT
  tools (`agt_policy_status`, `agt_policy_check_text`) from the agent surface.
- AGT audit log is a secondary sensor, not evidence.

## 7. File ownership (subagents)

| Owner | Files |
|---|---|
| CLI core | `factory/{cli,task,state,scope,gate,proof,release,util}.py`, `bin/factory`, `schemas/{task,proof}.json` |
| Checks | `factory/checks/**`, `schemas/gate.json` |
| Rewrite | `agents/**`, `sop/**`, `skills/**`, `policies/**`, `opencode.json`, `.agt/policy.json`, `runbooks/**`, `factory/policy_cards.py` |
| Hooks | `bin/factory-run`, `hooks/**`, `tests/**`, `README.md` |

## 8. Host config notes

- OpenCode V2 agent frontmatter uses `permissions:` (ordered `{action, resource,
  effect}` rules, last match wins), not `tools:`. The seven personas carry the
  shell + native allow list plus a factory-write deny.
- `policies/schemas/**` is legacy reference only. The live shapes are
  `schemas/task.json`, `schemas/proof.json`, and `schemas/gate.json`.

## 9. Acceptance

- `factory task new` creates a valid `task.json`.
- `factory gate` runs the gate set, writes proofs + `proof_root`, exits 1 on
  required failure.
- `factory verify` recomputes proofs and compares roots.
- `factory release` refuses on missing/failed proof, scope violation, or policy
  hash mismatch.
- `tests/smoke.sh` passes on a synthetic repo.
- No source file under `~/umairnlp` is modified.
