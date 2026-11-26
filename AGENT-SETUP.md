# Agent setup: decouple Factory and bind it to a repository

**Audience:** an AI coding agent with shell access, supervised by a human operator.
**Goal:** install Factory on a new machine, remove every reference to its original author's setup, bind it to one target repository, and prove the binding works.
**Rule:** setup is a negotiation, not a fixed recipe. Ask before every `ASK`. Never guess a path, branch, stack, test command, gate set, CI provider, or hook policy. Stop on a failed verification and report the exact command and output.

## 0. Non-negotiables

- Do not push, force-push, rebase, or rewrite history.
- Do not run `factory release`; `--dry-run` requires a GATE-phase task and is out of scope for setup.
- Never edit a check to return `PASS`. `TOOL_ABSENT`, `SKIP`, and `FAIL` are valid answers.
- Do not read `.env`, SSH keys, cloud credentials, browser profiles, or token stores.
- Do not install system packages, enable Docker, or use `--no-sandbox` without explicit approval.
- Keep all Factory state outside the target repository.
- Factory source is read-only after setup. Only the operator's explicit approval authorises a Factory edit.
- One task, one lane, one worktree. Do not create parallel tasks during setup.

## 1. Ask first (blocking)

Ask all questions in one batch, read the answers back, and wait for `yes`.

| # | Question | Default / rule |
|---|---|---|
| 1 | Absolute path to the target repo? | No default. Must be an existing clean Git clone. |
| 2 | Absolute path for Factory? | `~/factory-v2`. Must be outside the target repo and not `/tmp`. |
| 3 | Absolute path for the agent worktree root? | `~/worktrees/<repo-name>`. Must be outside Factory. |
| 4 | Absolute path for Factory state? | `~/.factory`. Must be outside the target repo. |
| 5 | Integration/base branch? | Detect from `origin/HEAD`; confirm. |
| 6 | Which paths may agents write? | No default. One or more globs, e.g. `src/**`, `tests/**`. |
| 7 | Canonical test command and its working directory? | No default. Example: `pytest -q` at repo root. |
| 8 | Stack? | `django`, `python`, `node`, `go`, or `other`. |
| 9 | Is `bwrap` available? Is network allowed inside the sandbox? | `bwrap` yes/no; network no. |
| 10 | Is Docker available for K1/services? | No. K1/services checks will be `TOOL_ABSENT`. |
| 11 | May I modify tracked files in Factory and the target repo? | Factory yes; target repo ask per file. |
| 12 | Confirm: no production secrets, customer data, or unrecoverable work in this repo. | Stop if not confirmed. |
| 13 | Will OpenCode run from Factory or from the target repo? | Factory (see Phase 9). |

Then run the configuration interview in Phase 2. Do not start installing until both phases are answered.

Write the answers to `$FACTORY_HOME/setup.env`. Do not commit them. Do not write them into either repo.

```sh
FACTORY_DIR="<FACTORY_DIR>"
TARGET_REPO="<TARGET_REPO>"
WORKTREE_ROOT="<WORKTREE_ROOT>"
FACTORY_HOME="<FACTORY_HOME>"
BASE_BRANCH="<BASE_BRANCH>"
```

Notation: `<LANE>` is the policy key (`lane/a`); `<WORKTREE>` is its directory (`$WORKTREE_ROOT/lane-a`).

## 2. Configuration interview (blocking)

Ask each section. Do not accept "whatever" or "default" without showing the consequence.

### 2.1 Worktree topology

Factory is built for parallel worktrees, but a single worktree is valid. Never create parallel worktrees unless the operator asks.

| Question | Options | Default |
|---|---|---|
| Topology | `single`, `parallel`, `existing` | `single`; `parallel` only if the operator already works that way |
| Lanes | one or more names | `lane/a`; add `lane/b` for parallel |
| Lane names | must match `policies/phases.json` | `lane/a`, `lane/b` |
| Integration lane | one name | `spec-comp` |
| Create worktrees? | `yes` / operator supplies paths | `yes` for lanes the operator names |
| Branch per lane | `task/<lane>` or custom | `task/<lane>` |
| Base branch per lane | same or different | same as `BASE_BRANCH` |
| Max concurrent tasks | `1` or a number | `1` unless parallel requested |
| K1 slots | host/lane counts | `policies/concurrency.json` values |

Rules:

- Every lane must appear in `policies/worktree-map.json` and in `policies/phases.json.permanent_lanes`.
- `policies/phases.json.integration_lane` must name the integration worktree used for release.
- `policies/concurrency.json.postgres.test_database.lane_servers` must have one entry per Postgres-backed lane. If the target does not use Postgres, ask before editing; do not leave author ports.
- There is no automatic lane-conflict prevention. Parallel lanes must use disjoint `--scope` globs or be serialized by the operator.

### 2.2 Gates and checks

Ask: **"Which gates do you want, and which must block?"**

| Question | Default |
|---|---|
| Gate preset | `policies/gate.json` defaults for `DOC`, `FIX`, `FEATURE`, `SECURITY`, `release` |
| Extra blocking checks | none |
| Advisory / shadow checks | defaults in `policies/tiers.json` |
| Remove any check? | no; require a written reason and explicit approval |
| Security gates | `SEMGREP`, `ARCH`; `AUTHZ_MATRIX` only for Django |
| Mutation testing | only if tests cover the changed source |
| Complexity/security tools | `RADON` / `BANDIT` only if installed |
| Default tier | `none`; `high`/`max` via `policies/tiers.json` |
| Strict shadow | no |
| Release gate | `policies/gate.json.gate_sets_by_kind.release` |

Configuration map:

| Concern | File |
|---|---|
| Which checks run per task kind | `policies/gate.json` (`gate_sets_by_kind`, release set) |
| Check definitions, shadow, scope, `required_in_ci` | `policies/checks.json` |
| Check commands and tool resolution | `policies/check-runners.json` |
| Per-tier required checks and budgets | `policies/tiers.json` |
| Path-to-tier mapping | `policies/risk-map.json` |
| Invariant/hygiene/debt/exception levels | `policies/gate-hierarchy.json` |
| Failure codes and severity | `policies/severity.json` |
| Release predicates | `policies/release-predicates.json` |

Check groups to show the operator:

| Group | IDs | Requires |
|---|---|---|
| Core | `SCOPE`, `SECRETS`, `CONVENTION`, `POLICY_DIGEST`, `GATE_CONTROLS`, `TEST_SET`, `TEST_INFRA`, `BASE_FAIL_HEAD_PASS`, `CHANGELOG`, `DEVIATION`, `ARCH`, `SMELL` | repo and Git |
| Test | `SUITE`, `SUITE_PYTEST`, `MUTATION` | Django or an adapter; covering tests |
| Security | `SEMGREP`, `AUTHZ_MATRIX`, `DAST_BASELINE`, `ZIZMOR`, `BANDIT`, `RADON` | tools, Django, live target, or workflows |
| Runtime | `BOOTTEST`, `THREAD_RACE`, `SCHEMA_FUZZ` | live app, Postgres, or OpenAPI spec |
| Agent | `AGT_PARITY`, `AGT_AUDIT`, `ORACLE_PROVENANCE`, `GUARD_DELTA` | AGT plugin and history |

Rules:

- Never remove `SCOPE`, `SECRETS`, `POLICY_DIGEST`, or `GATE_CONTROLS` without explicit approval. `SCOPE` and `SECRETS` should stay blocking.
- A check id must exist in `factory/checks/__init__.py` **and** `policies/checks.json`; an unknown id fails validation.
- Keep a check but make it `SKIP`/`TOOL_ABSENT` when it cannot run. Do not delete the check just to get green.
- Record every gate change and its reason in the operator's changelog or `docs/`.
- Validate with `python3 -m pytest -q tests` and a narrowed `factory gate --checks <IDs>`.

### 2.3 CI, branch protection, release

Ask: **"What CI do you have, and do you want Factory to gate releases through it?"**

| Question | Options | Default |
|---|---|---|
| CI provider | `github`, `gitlab`, `none`, `other` | `none` unless detected |
| Existing CI check names | list | none |
| Add a CI workflow? | yes/no | no; ask before writing |
| Release by PR? | yes/no | yes if CI exists |
| Branch protection enforced? | yes/no | no |
| Promotion branch | real branch name | `BASE_BRANCH` or `main` |
| Merge method | `merge`, `squash`, `rebase` | `merge` |
| Required CI checks | map each check id to a real context | none |
| GitHub auth available? | `gh auth status` | required for GitHub release |

Configuration map:

| Concern | File |
|---|---|
| Base/promotion branch, merge method, CI timeout | `policies/ci.json` |
| Required check ids and GitHub context names | `policies/ci-required.json` |
| Branch-protection expectations and live verification | `policies/branch-protection.json` |
| Which checks are required in CI | `policies/checks.json` (`required_in_ci`) |

Rules:

- If CI or branch protection is absent: **do not run `factory release`**. Use `factory gate` and `--dry-run`. Release will refuse with `PROTECTION_MISSING`, `CHECKS_UNMAPPED`, or `CHECKS_FAILED`.
- Release automation is GitHub/`gh`-only in this build. For GitLab, Bitbucket, or no CI, record `RELEASE=blocked` and use `factory gate` only.
- If CI exists: map every required id. An unmapped id blocks; never claim a check that does not exist.
- Only add a workflow with approval. Its job names must match the `github_check_map` names.
- Never hand-write `verified_at`, `response_sha256`, or `live_verified`. Run the verifier or leave the record unverified.
- Replace the author's repository/branch in `ci.json`, `ci-required.json`, and `branch-protection.json`.

### 2.4 Pre-commit and commit policy

Ask: **"Do you want hooks, which ones, and where?"**

| Question | Options | Default |
|---|---|---|
| Install hooks? | `yes` / `no` | `yes` in agent worktrees |
| Scope | `repo-wide` / `per-worktree` | `per-worktree` |
| Hooks | `pre-commit`, `commit-msg`, `post-commit` | all three |
| Require `Task-Id:` trailer? | yes/no | yes if `commit-msg` installed |
| Run `gitleaks`? | yes/no | yes if installed |
| Run `ruff`? | yes/no | yes if installed |
| Protected-path collision? | target has `policies/`, `sop/`, `skills/`, `agents/`, `factory/`, or `.agt/` | resolve before install |

Rules:

- `core.hooksPath` is **repo-wide**, not per-worktree. Every commit in the clone gets the hook, including human commits.
- Prefer per-worktree hooks for a shared clone:

```sh
git -C "$TARGET_REPO" config extensions.worktreeConfig true
git -C "$WORKTREE_ROOT/lane-a" config --worktree core.hooksPath "$FACTORY_DIR/hooks"
```

- The pre-commit hook protects `policies/`, `sop/`, `skills/`, `agents/`, `factory/`, `.agt/` by name. If the target repo uses those directories, the hook will block legitimate commits. Ask: dedicated agent clone, edit the hook's path list, or decline hooks.
- `factory` must be on `PATH` inside the hook environment. Otherwise commits fail.
- Hooks are fast feedback only and are bypassable. The release gate is the authority.
- If the operator declines `commit-msg`, say that `Task-Id:` is no longer enforced.

### 2.5 Record the decisions

Append to `$FACTORY_HOME/setup.env`:

```sh
TOPOLOGY="<single|parallel|existing>"
LANES="<space-separated lane names>"
INTEGRATION_LANE="<name>"
GATE_PROFILE="<default|custom>"
BLOCKING_CHECKS="<comma-separated>"
SHADOW_CHECKS="<comma-separated>"
CI_PROVIDER="<github|gitlab|none|other>"
RELEASE_BY_PR="<yes|no>"
HOOKS="<none|pre-commit,commit-msg,post-commit>"
HOOK_SCOPE="<repo-wide|per-worktree>"
OPENCODE_MODE="<factory|target>"
```

## 3. Preflight

Run:

```sh
python3 --version
git --version
node --version
npm --version
command -v bwrap
command -v docker
command -v ruff
command -v gitleaks
command -v rg
```

Required: Python >= 3.11, Git, a POSIX shell, and `rg` (or substitute `grep -RIn` in every command below). Optional: Node >= 20 (AGT plugin), `bwrap` (sandbox), Docker (K1/services), `ruff` and `gitleaks` (hooks).

Stop and ask if:

- `bwrap` is missing and the operator has not approved unsandboxed runs.
- Docker is missing and the operator expects K1/services.
- The target repo is dirty:

```sh
test -z "$(git -C "$TARGET_REPO" status --porcelain)"
```

Missing optional tools are reported as `TOOL_ABSENT` later. Do not fake them.

## 4. Install Factory

```sh
git clone https://github.com/boredompeaks/factory-v2.git "$FACTORY_DIR"
cd "$FACTORY_DIR"
npm install
python3 -m pytest -q tests
bash tests/smoke.sh
```

Expected: tests pass, smoke prints `PASS`. If either fails: stop, report, do not continue.

Set the environment for this shell and persist it only with approval:

```sh
export FACTORY_HOME="$FACTORY_HOME"
export FACTORY_EVIDENCE_ROOT="$FACTORY_HOME/evidence"
export PATH="$FACTORY_DIR/bin:$PATH"
factory --version
```

Expected: `factory 2.0.0` or newer.

## 5. Sever author-machine references

Detect:

```sh
cd "$FACTORY_DIR"
rg -n '/home/boredompeaks|~/umairnlp|~/backend-worktrees' \
  bin policies .agt opencode.json agents .opencode/agents
```

Every match below must be replaced. Historical files (`RECOVERY.*`, `STATUS.md`, `DESIGN.md`, `PLAN.md`, `.rewrite_factory_v2.py`, `tools/recover_from_db.py`) are excluded from this check; never run them.

### 5.1 `FACTORY-ENV.sh`

Replace the fixed root with the script's location:

```sh
FACTORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export FACTORY_HOME="${FACTORY_HOME:-$HOME/.factory}"
export FACTORY_EVIDENCE_ROOT="${FACTORY_EVIDENCE_ROOT:-$FACTORY_HOME/evidence}"
export PATH="$FACTORY_ROOT/bin:$PATH"
```

### 5.2 `bin/factory-run`

Delete this line:

```sh
--ro-bind /home/boredompeaks/umairnlp /home/boredompeaks/umairnlp
```

The earlier `--ro-bind / /` already makes the host read-only. Add extra `--ro-bind "<PATH>" "<PATH>"` entries only when the target needs them.

Verify:

```sh
rg -n '/home/boredompeaks' bin/
```

Expected: no output.

### 5.3 `policies/worktree-map.json`

Map every lane from Phase 2.1:

```json
{
  "lane/a": "<WORKTREE_ROOT>/lane-a",
  "lane/b": "<WORKTREE_ROOT>/lane-b",
  "spec-comp": "<WORKTREE_ROOT>/spec-comp"
}
```

Keep only lanes the operator chose. Lane keys must remain valid per `policies/phases.json`.

### 5.4 `.agt/policy.json`

Replace the three author paths, preserving regex escaping:

| Old regex source | New regex source |
|---|---|
| `^/home/boredompeaks/umairnlp(/|$)` | `^<escaped TARGET_REPO>(/|$)` |
| `^/home/boredompeaks/factory-v2(/|$)` | `^<escaped FACTORY_DIR>(/|$)` |
| `^/home/boredompeaks/\.factory(/|$)` | `^<escaped FACTORY_HOME>(/|$)` |

Escape regex metacharacters in the path (at minimum `.` and `\`). The first rule protects the target's source; the second keeps Factory read-only; the third keeps evidence read-only. Validate:

```sh
python3 -m json.tool .agt/policy.json >/dev/null
```

### 5.5 `opencode.json`

- If `command -v basemind` succeeds, replace the absolute command with `basemind`. Otherwise remove the `basemind` server or keep an absolute path the operator supplies.
- Remove MCP servers that are not installed on this machine.
- Keep `plugin` pointing at `./plugins/agt.js`.
- Validate:

```sh
python3 -m json.tool opencode.json >/dev/null
```

### 5.6 Agent permissions

Edit **both** `agents/*.md` and `.opencode/agents/*.md`. They must stay byte-identical: `diff -rq agents .opencode/agents` must print nothing.

Replace the author's external directories:

```md
- { action: external_directory, resource: "~/backend-worktrees/**", effect: allow }
- { action: external_directory, resource: "~/umairnlp/**", effect: allow }
```

with:

```md
- { action: external_directory, resource: "<TARGET_REPO>/**", effect: allow }
- { action: external_directory, resource: "<WORKTREE_ROOT>/**", effect: allow }
```

Replace the four author write-deny rules:

```md
- { action: write, resource: "~/umairnlp/**", effect: deny }
- { action: edit, resource: "~/umairnlp/**", effect: deny }
- { action: patch, resource: "~/umairnlp/**", effect: deny }
- { action: multiedit, resource: "~/umairnlp/**", effect: deny }
```

with the same four actions against `<FACTORY_DIR>/**`. Factory must remain read-only to agents.

### 5.7 Stack, gates, CI, and changelog

Edit only what the operator confirmed in Phase 2:

- `policies/check-runners.json`: `suite_runner` for Django; see Phase 6 for other stacks.
- `policies/changelog.json`: `file` must name the target's changelog, or a file the operator will maintain.
- `policies/gate.json`, `policies/checks.json`, `policies/tiers.json`: only the requested gate changes.
- `policies/ci.json`, `policies/ci-required.json`, `policies/branch-protection.json`: only the requested CI/release changes; see Phase 8.
- `policies/risk-map.json` and `policies/layers.json`: product-specific; ask before changing.

### 5.8 Historical author files

Do not run `CLAUDE.local.md`, `RECOVERY.*`, `.rewrite_factory_v2.py`, or `tools/recover_from_db.py`. They contain the author's paths and one-off recovery logic. Leave them, or delete them from your fork after approval.

### 5.9 Verify decoupling

```sh
cd "$FACTORY_DIR"
rg -n '/home/boredompeaks|~/umairnlp|~/backend-worktrees' \
  bin policies .agt opencode.json agents .opencode/agents
```

Expected: no output. Then validate JSON:

```sh
for f in .agt/policy.json opencode.json policies/worktree-map.json \
         policies/check-runners.json policies/changelog.json \
         policies/gate.json policies/checks.json policies/ci.json \
         policies/ci-required.json policies/branch-protection.json; do
  python3 -m json.tool "$f" >/dev/null || exit 1
done
```

## 6. Stack adapter (only when not Django)

`SUITE` is currently Django-only: `factory/checks/tests.py` requires `manage.py` and parses Django output. `SUITE_PYTEST` is a shadow diagnostic. `check-runners.json` alone cannot make `SUITE` generic.

Ask the operator to choose exactly one:

| Choice | Action | Consequence |
|---|---|---|
| A (default) | Leave `SUITE` in place. | `SUITE` reports `FAIL`/`TOOL_ABSENT`; a full FEATURE/FIX gate will not go green. Honest but incomplete. |
| B (recommended) | Adapt `factory/checks/tests.py` to run the confirmed `TEST_CMD`, add tests, and wire the runner. | Real coverage; requires a Factory code change and review. |
| C (pilot only) | Remove `SUITE` from the relevant `gate_sets_by_kind` entries in `policies/gate.json`. | Weakens the gate. Requires explicit approval and a written reason. Never remove `SCOPE`, `SECRETS`, or `POLICY_DIGEST`. |

Never make `SUITE` return `PASS` without running the target's tests.

Expected non-PASS checks on a non-Django or bare host: `AUTHZ_MATRIX` (Django only), `RADON`/`BANDIT` (tools absent), `DAST_BASELINE`/`SCHEMA_FUZZ` (no live target/spec), `BOOTTEST` (no app), `ZIZMOR` (no workflows), `AGT_PARITY`/`AGT_AUDIT` (plugin not installed), `MUTATION` (no covering tests).

## 7. Bind the target repository

### 7.1 Create the worktrees

Single worktree:

```sh
mkdir -p "$WORKTREE_ROOT"
git -C "$TARGET_REPO" fetch origin "$BASE_BRANCH"
git -C "$TARGET_REPO" worktree add "$WORKTREE_ROOT/lane-a" \
  -b "task/lane-a" "origin/$BASE_BRANCH"
```

Parallel worktrees, only if chosen:

```sh
git -C "$TARGET_REPO" worktree add "$WORKTREE_ROOT/lane-a" \
  -b task/lane-a "origin/$BASE_BRANCH"
git -C "$TARGET_REPO" worktree add "$WORKTREE_ROOT/lane-b" \
  -b task/lane-b "origin/$BASE_BRANCH"
```

Integration worktree, only if the operator will release:

```sh
git -C "$TARGET_REPO" worktree add "$WORKTREE_ROOT/spec-comp" \
  -b spec-comp "origin/$BASE_BRANCH"
```

If a branch already exists, stop and ask whether to reuse it or pick a new name. Update `policies/worktree-map.json` and `policies/phases.json` to match the chosen lanes.

### 7.2 Install hooks (only if chosen in 2.4)

Repo-wide:

```sh
"$FACTORY_DIR/hooks/install.sh" --worktree "$WORKTREE_ROOT/lane-a"
git -C "$WORKTREE_ROOT/lane-a" config --get core.hooksPath
```

Expected: `$FACTORY_DIR/hooks`.

Per-worktree (preferred for shared clones):

```sh
git -C "$TARGET_REPO" config extensions.worktreeConfig true
git -C "$WORKTREE_ROOT/lane-a" config --worktree core.hooksPath "$FACTORY_DIR/hooks"
```

If the operator wants only some hooks, copy those into a separate hooks directory and point `core.hooksPath` there. Do not point at `$FACTORY_DIR/hooks` and then expect omitted hooks to be skipped.

Warnings to repeat:

- `core.hooksPath` is repo-wide unless `--worktree` is used. Every commit requires a `Task-Id:` trailer when `commit-msg` is installed.
- Pre-commit protects `policies/`, `sop/`, `skills/`, `agents/`, `factory/`, `.agt/` by name. Resolve collisions first.
- `factory` must be on `PATH` inside the hook environment, or commits fail.
- Hooks are fast feedback only. The release gate is the authority.

### 7.3 Mint the task

```sh
cd "$WORKTREE_ROOT/lane-a"
factory task new --kind FEATURE --lane "lane/a" \
  --scope '<SCOPE_GLOB>' --worktree "$PWD"
```

Repeat `--scope` for each allowed path. Expected: JSON with `task.task_id`, and `task.json` written under `$FACTORY_HOME/tasks/<id>/`. It must not be inside the target repo.

```sh
factory task show
```

Expected: the same task, inferred from the worktree.

### 7.4 First gate

Run the narrow gate first, then add the checks the operator chose:

```sh
factory gate --checks SCOPE,CONVENTION
factory gate --checks '<COMMA_SEPARATED_CHECKS>'
```

Expected: `PASS` for each runnable check, or a specific `FAIL`/`TOOL_ABSENT`/`SKIP` to report. Do not run `factory gate --release` or `factory release` yet.

## 8. CI and release wiring (only if chosen in 2.3)

1. Set `policies/ci.json.base_branch` and `promotion_branch` to real branches.
2. Set `merge_method` to the operator's policy; the release code has no bypass.
3. In `policies/ci-required.json`, map every required id:
   - `source: product-ci` → exact GitHub/GitLab check context name.
   - `source: factory-gate` → no external name; harness proof is used.
4. In `policies/branch-protection.json`, set `required_checks` and `required_check_ids` to the same set. Do not fabricate `verified_at` or `response_sha256`.
5. In `policies/checks.json`, set `required_in_ci` for each check.
6. `policies/ci-required.json` says it is generated by `scripts/generate_policies.py`; that generator is not in this copy. Edit `ci-required.json` by hand and keep it exactly consistent with `checks.json`.
7. If the operator wants a workflow, ask for provider and file path; do not write it silently. The job names must match the mapping.
8. Verify:

```sh
gh auth status                 # GitHub only
python3 -m json.tool policies/ci.json >/dev/null
python3 -m json.tool policies/ci-required.json >/dev/null
python3 -m json.tool policies/branch-protection.json >/dev/null
```

If CI or branch protection is missing, stop Phase 8 and record `RELEASE=blocked: no CI + no branch protection`. Use `factory gate` only. `factory release --dry-run` still needs a clean integration worktree and a GATE-phase task; do not force it.

## 9. OpenCode integration

Ask which mode the operator chose.

### Mode A: run OpenCode from Factory (recommended)

- Start OpenCode with cwd `$FACTORY_DIR`. It loads `opencode.json`, `.opencode/agents/`, `plugins/agt.js`, and the Factory skills.
- The agent reaches the target only through the permissions in 5.6 (`TARGET_REPO`, `WORKTREE_ROOT`).
- Basemind indexes the Factory tree, not the target. If target code search is required, ask the operator whether to re-point `basemind` at `$TARGET_REPO`.

### Mode B: run OpenCode from the target repo

Only with approval, because it writes tracked files:

1. Copy `.opencode/agents/` and each role's `SKILL.md` into `<TARGET_REPO>/.opencode/`.
2. Create or merge `<TARGET_REPO>/opencode.json`:
   - `default_agent` = `orchestrator`
   - `plugin` = `["<FACTORY_DIR>/plugins/agt.js"]`
   - keep only installed MCP servers.
3. Copy the permission block from 5.6, with `TARGET_REPO` and `WORKTREE_ROOT`.
4. Record that the copies must be re-synced after any Factory agent/permission change.

Verify the plugin:

```sh
cd "$FACTORY_DIR"
node -e "import('./plugins/agt.js').then(()=>console.log('AGT OK'))"
```

Expected: `AGT OK`. If it fails, stop; OpenCode may fail to load.

## 10. Done checklist

- [ ] `python3 -m pytest -q tests` passes in `$FACTORY_DIR`.
- [ ] `bash tests/smoke.sh` prints `PASS`.
- [ ] `rg -n '/home/boredompeaks|~/umairnlp|~/backend-worktrees' bin policies .agt opencode.json agents .opencode/agents` returns nothing.
- [ ] All edited JSON parses with `python3 -m json.tool`.
- [ ] `diff -rq agents .opencode/agents` returns nothing.
- [ ] Worktree map, `phases.json`, and `concurrency.json` match the chosen topology.
- [ ] The requested gate changes are in `gate.json`/`checks.json`/`tiers.json`, and no blocking check was removed without approval.
- [ ] CI/branch-protection records match the real provider, or `RELEASE=blocked` is recorded.
- [ ] Hook installation matches Phase 2.4, or hooks were deliberately declined.
- [ ] `factory task show` works inside the worktree.
- [ ] `factory gate --checks SCOPE,CONVENTION` was run and its exact result recorded.
- [ ] `$FACTORY_HOME` and evidence are outside both repos.
- [ ] `git -C "$FACTORY_DIR" status --short` shows only intended setup edits.
- [ ] `git -C "$TARGET_REPO" status --porcelain` is clean except worktree registration.
- [ ] No push, no release, no history rewrite.

## 11. Stop and roll back

Stop immediately if:

- tests or smoke fail,
- the target repo is dirty,
- hooks collide with target directories and the operator declines the workaround,
- a missing tool would be faked,
- a gate would be weakened without approval,
- CI/branch protection cannot be mapped,
- a path cannot be decoupled without guessing.

Rollback only with approval:

```sh
git -C "$TARGET_REPO" config --unset core.hooksPath
git -C "$TARGET_REPO" worktree remove "$WORKTREE_ROOT/lane-a"
```

Never delete the target repo or the operator's branch.

## 12. Report back

Return exactly:

```text
FACTORY_DIR: <path>
TARGET_REPO: <path>
FACTORY_HOME: <path>
TOPOLOGY: <single|parallel|existing> (<lanes>)
WORKTREES: <path list>
TASK_ID: <id>
GATES: <blocking checks> / <shadow checks>
GATE RUN: SCOPE=<PASS/FAIL> CONVENTION=<PASS/FAIL>
CI: <provider> RELEASE=<enabled|blocked: reason>
HOOKS: <none|list> (<repo-wide|per-worktree>)
DECOUPLED: <yes/no> (remaining matches: <none/list>)
OPENCODE: <Mode A/Mode B>
RESIDUAL: <TOOL_ABSENT/SKIP/FAIL list with reasons>
BLOCKED: <questions still needing the operator>
```
