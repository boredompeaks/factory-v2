# Factory v2 — build status

Scope: `/tmp/opencode/factory-v2/`. Source `/home/boredompeaks/umairnlp` and
`/home/boredompeaks/backend-worktrees` are read-only. No source file was
modified or deleted. The only untracked entry in the source repo remains the
pre-existing `report/`.

## Built

- `bin/factory` — task/scope/check/gate/evidence/verify/release/k1/services/policy
- `bin/factory-run` — bwrap wrapper (worktree rw, factory ro, evidence ro)
- `factory/` — CLI core + 26 check runners + policy cards
- `agents/` and `.opencode/agents/` — 7 personas, shell + native tools, factory-write deny
- `sop/` — 38 SOPs rewritten to the CLI
- `skills/` and `.opencode/skills/` — 7 skills
- `policies/` — copied policy set, including `phases`, `dispatch-rules`, `modes`,
  `concurrency`, `mutation`, `schema-fuzz`; seal/ledger language removed
- `plugins/agt.js` + `.agt/policy.json` + `package.json` — AGT censor (V2 shim)
- `hooks/` — pre-commit, commit-msg, post-commit, install
- `docker/`, `scripts/`, `tools/`, `runbooks/`, `basemind.toml`, `CLAUDE.local.md`

## Verified

| Check | Result |
|---|---|
| `python3 -m pytest -q tests/test_cli.py` | **7 passed** |
| `bash tests/smoke.sh` | **PASS** (`task → gate → verify → release --dry-run`) |
| Out-of-scope staged commit | **blocked** by pre-commit scope check |
| In-scope commit | **passes** pre-commit, gitleaks, ruff, commit-msg |
| `post-commit` evidence | JSONL written to `.git/factory-evidence.jsonl` |
| `factory scope check --staged` | PASS in scope, FAIL out of scope |
| `factory release` without gate | exit 1, clean refusal (no traceback) |
| `factory gate --release --checks` | exit 1, refuses narrowing |
| AGT plugin import | **OK** (`node -e import('./plugins/agt.js')`) |
| Policy JSON | all valid; `phases/dispatch/modes/concurrency/mutation/schema-fuzz` present |
| Persona frontmatter | `permissions:` (OpenCode V2 shape), shell + native tools |
| Source tree | untouched (`?? report/` only) |

## Check runner status on a synthetic repo

Real PASS: SCOPE, CONVENTION, SECRETS, SEMGREP, POLICY_DIGEST, SMELL, ARCH,
GATE_CONTROLS, TEST_INFRA, GUARD_DELTA, ORACLE_PROVENANCE, BOOTTEST, AGT_PARITY,
AGT_AUDIT.

Correct non-PASS:
- RADON, BANDIT — `TOOL_ABSENT` on host; pinned in the K1 image.
- ZIZMOR — `SKIP` when no workflows exist.
- AUTHZ_MATRIX — `SKIP` on a non-Django repo.
- MUTATION — `SKIP` when no relevant tests are collected.
- CHANGELOG — `FAIL` when `backend/docs/changes.md` has no row.
- SUITE — `FAIL` when the postgres endpoint is unavailable or no tests exist.
- SCHEMA_FUZZ, DAST_BASELINE — `TOOL_ABSENT` without a live target/spec.
- TEST_SET, BASE_FAIL_HEAD_PASS, THREAD_RACE — `SKIP` when not applicable to the diff/tier.

## Determinism

Proofs record `base`, `head`, `tree`, tool/version/image, env
(`TZ=UTC`, `PYTHONHASHSEED=0`, `LC_ALL=C`), exit, stdout hash, artifacts, and
`seed` for fuzz checks. `proof_root` is sha256 over canonical JSON of the sorted
proof set, excluding timing fields. Fuzz seeds are `secrets.randbits(64)` per
run and recorded; `--seed` replays. `policy_hash` is checked before and after a
gate run.

## Environment requirements

- `bwrap` for `factory-run` and network-isolated checks (present here).
- K1 image for radon, bandit, hypothesis, ZAP, schemathesis
  (`files/docker/k1/requirements.lock`).
- PostgreSQL (or K1) for `SUITE` postgres.
- `FACTORY_HOME`, `FACTORY_EVIDENCE_ROOT` (defaults under `~/.factory`).
- `npm install` in this directory for a portable AGT plugin; `node_modules` is
  currently a symlink to the source install for local use.

## Known limitations

- `policies/schemas/**` is legacy reference; live shapes are `schemas/task.json`,
  `schemas/proof.json`, `schemas/gate.json`.
- A full `factory gate --release` needs K1/services; on the host it reports
  `TOOL_ABSENT` for radon/bandit and for DAST/schema-fuzz targets.
- Personas retain their existing model IDs. Change them to `openai/gpt-6-luna`
  if the runtime should use that provider.
- `factory release` requires `phase=GATE` and `mode=RELEASE` for a real merge;
  `--dry-run` is the smoke path.

## Quickstart

```sh
cd /tmp/opencode/factory-v2
export PATH="$PWD/bin:$PATH"
export FACTORY_HOME="$HOME/.factory"
export FACTORY_EVIDENCE_ROOT="$HOME/.factory/evidence"

bash tests/smoke.sh

# in a product worktree
factory task new --kind FEATURE --lane lane/a --scope 'backend/**' --worktree "$PWD"
factory gate --checks SCOPE,CONVENTION      # fast feedback
factory gate --release                      # authoritative, needs K1/services
factory verify
factory release --dry-run --repo /path/to/integration
```
