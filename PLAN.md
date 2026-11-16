# Factory v2 — stripped enforcement plan (scratch, not applied)

Status: PROPOSAL. No files under ~/umairnlp or ~/backend-worktrees were modified.
Facts measured 2026-11-14 against HEAD 3d04a20.

## 0. Measured baseline

| Surface | Size |
|---|---|
| `files/tools/mcp/**` | 35 py files, 13,825 LOC |
| `files/tools/*.py` (top level) | 12,745 LOC (gate_run 2,863; gate_control 1,150; authz_matrix 1,345; semgrep_runner 1,324) |
| `scripts/**` | 14,659 LOC, 46 files, 25 `test_*.py` |
| `.opencode` authz + agt | 1,396 LOC |
| `files/policies` | 592 KB: 34 JSON + semgrep.yml + 18 schemas (156 KB) |
| personas | 7 files, 3,504 lines |
| SOP | 38 files, 1,247 lines |
| skills | 7 files, 451 lines |
| runbooks | 11 files, 971 lines |
| checks catalog | 26 checks; 7 semgrep rules |
| MCP servers in opencode.json | 7 carbon-* + basemind + chrome-devtools + context7 |

Host toolchain present: semgrep, zizmor, schemathesis, ruff, gitleaks, gh, docker, bwrap, node, pytest.
Host toolchain missing: radon, bandit, hypothesis, zap-baseline — these exist only in the K1 image
(`files/docker/k1/requirements.lock`, image 1.19 GB with scanners).

## 1. Plan validation

### Holds
- Removing the typed-tool layer is the single largest simplification available: ~41K LOC
  (13.8K MCP + 12.7K tools + 14.6K scripts) exists to police 7 agents.
- K1, services compose, and the worktree topology are independent of the ledger/seal; they survive.
- SOPs/skills are content, not code; they carry over with mechanical edits.
- The product worktrees are already separate; git gives free content-addressing.

### Breaks
1. **`chmod 444` is not enforcement at the same UID.** Agents run as `boredompeaks` (uid 1000),
   who owns the files, so `chmod +w` succeeds. `chmod` is defense-in-depth only.
   Real mechanism: read-only bind mount (bwrap) or a separate UID.
2. **Removing typed tools removes the only deterministic scope/test-infra/policy interception.**
   `pre-commit` hooks are bypassable (`--no-verify`); `core.hooksPath` is agent-writable.
   The authoritative check must run at release/merge, invoked by the human, over the full diff.
3. **"SOPs carry over" is not free.** 192 typed-tool references (`check.run` ×14, `git.*`,
   `ledger.append`, `gate.control`, `basemind_*`) across 38 SOP files need a mechanical rewrite.
4. **"Keep the toolchain" needs a host decision.** radon/bandit/hypothesis/ZAP are K1-only today.
   Either run checks in K1 or `pip install radon bandit hypothesis` into the factory venv.
5. **"Copy all applicable policies" over-copies.** 148 KB `policy-manifest.json`, 18 KB
   `negative-controls.json`, and the `phases/dispatch/modes` state machine are the overhead being parked.

## 2. Target architecture — four thin layers

### L0. Kernel: bwrap jail (the real anti-edit)
Verified on this host: `--ro-bind` returns EROFS on write; `--bind` stays writable.
```
bwrap \
  --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp \
  --bind  ~/backend-worktrees        ~/backend-worktrees \
  --ro-bind ~/umairnlp               ~/umairnlp \
  --bind  ~/.local/share/opencode    ~/.local/share/opencode \
  --bind  ~/.cache                   ~/.cache \
  --die-with-parent --chdir ~/backend-worktrees \
  opencode
```
Wrapper: `~/bin/factory-run` (10 lines). All subagents inherit the namespace.
The agent cannot chmod, edit, or delete anything under `~/umairnlp`; it can work freely in the product.
`chmod 0555` on `files/policies`, `files/sop`, `files/skills`, `.opencode/agents` remains as
defense-in-depth for the un-sandboxed case.

Fallbacks if bwrap ever breaks: dedicated `factory` user (root-owned factory, 0555), or
`systemd-run --user -p ProtectSystem=strict -p ReadOnlyPaths=~/umairnlp`.

### L1. Git: the ledger
- `pre-commit`: deny staged paths in `policies/**`, `sop/**`, `skills/**`, `.opencode/agents/**`,
  `factory/**`; scope check against `.factory/task.json`; test-infra check; gitleaks `--staged`;
  ruff on changed files. Fast feedback, not authoritative.
- `commit-msg`: require `Task-Id: <id>` trailer.
- `post-commit`: append `{sha, tree, task, scope_hash, hook_results}` to
  `.git/factory-evidence.jsonl` (outside the worktree, append-only by construction).
- `git log` is the hash chain. No `prev_hash`, no `record_hash`, no signatures.

### L2. One CLI: `factory` (~500 LOC Python)
Replaces MCP (13.8K), gate_run (2.9K), gate_control (1.2K), ledger, policy_digest.
```
factory task new|show|phase      # task.json: id, kind, lane, scope, base_sha, phase
factory policy card --role R     # generate compressed rule card from JSON
factory policy drift             # persona card == generated card?
factory check <ID>               # shell out: semgrep/radon/bandit/zizmor/pytest/...
factory gate                     # run gate set, aggregate, exit 1 on BLOCK
factory evidence claim|proof     # write claims / regenerate proofs
factory verify                   # recompute diff, hashes, scans; PASS/FAIL
factory release                  # merge --no-ff, push, gh pr create
factory k1 up|down|status|exec   # thin docker wrapper
factory services up|down|health  # wraps existing scripts
```
No server, no daemon, no plugin, no MCP.

### L3. Prompt: generated policy cards
Policies stay JSON. `factory policy card` emits ~2-4 KB per role (scope rules, test rules,
severity caps, check list). Personas embed the card; `factory policy drift` fails on mismatch.
Agents read the card, not 350 KB of JSON. Source of truth stays the JSON.

## 3. Evidence policy: claims vs proofs

Replace the hash-chained ledger with two directories:

| Kind | Written by | Content | Trust |
|---|---|---|---|
| `claims/` | agent | plan, build summary, audit verdict, findings | self-reported |
| `proofs/` | `factory verify` | `git diff base..head`, test log, semgrep JSON, radon/bandit/zizmor JSON, hashes | recomputable |

Layout: `evidence/<task_id>/{claims,proofs}/*.json`, committed on the task branch.
Release requires **proofs**, not claims. `factory verify` regenerates proofs and refuses on
mismatch or missing artifact. Tamper evidence = git commit sha + reflog + optional `git notes`.
Commit trailers: `Task-Id`, `Base-Sha`, `Scope-Hash`.

This is the "better evidence" answer: legible, portable, re-runnable, zero crypto.

## 4. Enforcement matrix

| Rule | Old mechanism | New mechanism | Determinism | Bypass |
|---|---|---|---|---|
| No policy edit | plugin deny + manifest | bwrap ro-bind + chmod + hash baseline | kernel | run outside sandbox (owner only) |
| Scope containment | typed tool intercept | pre-commit + `factory verify` at release | deterministic at release | `--no-verify` (caught at release) |
| Test-infra change | typed tool | pre-commit + task.kind globs | deterministic | same |
| Secrets | typed tool + gitleaks | gitleaks hook + `factory verify` | deterministic | same |
| Suite green | gate_run cache | `factory gate` → pytest | deterministic | re-run at release |
| Test removal/tautology | TEST_SET runner | keep `assert-rules.json` + a 150-line checker | semi | claims reviewed by auditor |
| Semgrep/radon/bandit/zizmor | 7K LOC runners | direct CLI + JSON | deterministic | none |
| Mutation / schema fuzz | gate_run | drop (optional manual) | - | - |
| Verdict | ledger append | `claims/audit.json` | prompt | human release gate |
| Phase/dispatch | phases.json + ledger | `task.json` field + 20-line transition table | weak | acceptable pre-MVP |
| Budgets | harness metered | drop | - | - |
| Seal | gate_control | git commit | git | - |

## 5. Copy / fork / drop inventory

### Copy verbatim
- `files/skills/**` (7, 451 L) — review tool names only.
- `files/docker/**` (212 KB) — K1 + services, unchanged.
- `files/policies/semgrep.yml` (7 rules) — unchanged.
- `files/policies/{checks,invariants,severity,risk-map,task-kinds,test-removal-reasons,assert-rules,gate,escalation,release-predicates,worktree-map,changelog,lessons,reuse,tiers}.json` — ~15 files, tiny edits.
- `files/policies/secrets-baseline.json`, `attack-catalog.json` (if hacker stays).
- `scripts/services_*.sh`, `scripts/k1_build.sh`, `scripts/audit_test_runner.sh`.
- `files/BASEMIND-INDEX.md`, `CLAUDE.local.md`.
- `files/runbooks/{k1,services,test-runner,release,pr,worktrees,basemind}.md`.

### Fork / edit
- `files/*.md` personas (7, 3.5K L): replace `permissions:` with shell model; delete H/B contract
  blocks or compress to 3 lines; swap typed-tool calls for `factory` CLI. **~3 h.**
- `files/sop/**` (38, 1.2K L, 192 refs): mechanical rewrite + remove seal/ledger steps. **~4 h.**
- `opencode.json`: drop 7 carbon-* servers + `agt.js`/`authz.js` plugins; keep basemind,
  chrome-devtools, context7; add permissive shell/edit permissions. **~0.5 h.**
- `files/policies/{check-runners,gate,release-predicates}.json`: point at CLI, drop `gate_control_*`. **~1 h.**
- `files/runbooks/{gate,ledger,mcp}.md`: rewrite or delete. **~1 h.**

### New
- `factory` CLI (500 L): **~6 h.**
- `factory-run` bwrap wrapper + permissions: **~1 h.**
- git hooks (pre-commit/commit-msg/post-commit): **~2 h.**
- policy card generator + drift check: **~2 h.**
- evidence schemas + `verify`: **~2 h.**

### Drop entirely
- `files/tools/mcp/**` (13,825 L), `.opencode/plugins/{agt,authz}.js`, `lib/authz-core.js` (1,396 L).
- `files/tools/{gate_run,gate_control,policy_digest,agt_audit,agt_parity}.py`.
- `scripts/{policy_manifest,check_harness_config,config_controls,control_mutation,gate_all_controls,mcp_discover,scan_sessions,backfill_register,bug_lint}.py`.
- `scripts/test_*.py` (25 files) except a few new CLI tests.
- `files/policies/policy-manifest.json` (148 KB), `negative-controls.json` (18 KB),
  `{phases,dispatch-rules,modes,concurrency,models,branch-protection,ci-required,enums,redaction,layers,mutation,schema-fuzz}.json`.
- `files/quality/ledger/**`, `.agt/**`, `.agents/**`.
- `drafts/`, `audit-scratch/`, `phase6/`, `phase7/`, `quality-scan/`, `report/`.

Net: ~41K LOC → ~1.5K LOC (CLI + hooks + wrapper); policies 592 KB → ~250 KB.

## 6. Plan and time

| Phase | Work | Hours |
|---|---|---|
| 0 | Scratch dir, baseline `sha256sum`, bwrap smoke test | 0.5 |
| 1 | Skeleton + copy set + `factory` CLI (task/check/gate/verify) | 6 |
| 2 | Personas + SOP rewrite + policy cards + opencode.json | 7 |
| 3 | Hooks + evidence claims/proofs | 3 |
| 4 | Toolchain wiring (semgrep/radon/bandit/zizmor/pytest/ZAP/schemathesis) + K1 wrapper | 3 |
| 5 | Dry run: FEATURE, FIX, SECURITY, release on disposable branch | 4 |
| 6 | Cutover + archive v1 | 1 |

- Cowboy thin slice (phases 0-2 + hooks, no evidence polish, no dry run): **8-10 h**.
- Usable v2 (all phases): **22-24 h** (~3 focused days).
- With subagents on the mechanical SOP/persona rewrite: **14-16 h**.

## 7. Tradeoffs removed

Removed: signed/sealed records, hash-chained ledger, capability minting, tool-layer scope
interception, phase/dispatch state machine, lane concurrency locks, budget metering, policy
manifest + digest + gate-control cache, negative controls, mutation/schema-fuzz meta-testing,
AGT parity/audit, authz matrix, guard delta, oracle provenance, 7 MCP servers, 2 plugins.

Kept: 7 roles, skills, SOPs, policy subset, semgrep rules, checks catalog, test-integrity rules,
severity/risk tables, K1 + services + ZAP/schemathesis/hypothesis, worktrees, gitleaks, branch
protection, release-train predicate, escalation rules, basemind index.

Accepted losses: no cryptographic proof of verdict authorship; no metered budgets; no automatic
lane-conflict prevention; evidence is partly self-reported but re-checked at release.

## 8. Executive verdict

**Proceed — with three corrections.**

1. Replace `chmod` as the primary lock with bwrap `--ro-bind` (verified working here). Keep chmod
   as defense-in-depth, not the mechanism.
2. Do not go pure-prompt. Keep the single `factory` CLI and move the authoritative check to the
   human-invoked release step over the full diff. Hooks are fast feedback; release is the gate.
3. Keep the deterministic core: scope, test-infra, secrets, suite, semgrep/radon/bandit/zizmor.
   Drop the state machine, seals, ledger, manifest, and MCP.

The direction is correct: the current factory spends ~41K LOC policing 7 agents that a mount
namespace, a hook, and 500 lines of CLI can police. The one thing the original plan gets wrong is
believing `chmod` is enforcement; the one hidden cost is the 192 SOP references. Both are fixable
inside the estimate above.

Estimated finish: **8-10 h for the cowboy MVP, 22-24 h for a usable v2, 14-16 h with subagent
help on the mechanical rewrite.**
