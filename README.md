# Factory v2

A policy-driven harness for AI coding agents. The harness — not the prompt — owns scope, checks, proofs, and release.

> [!WARNING]
> **Agents have shell access.** Factory agents run `bash`/`shell` and native file tools with your OS user's privileges. They can read, modify, and delete files, execute repository code and Git hooks, start Docker, and make network calls. The optional `factory-run` sandbox uses bubblewrap; outside it, an agent is **not** contained. A confused or malicious agent can damage the machine or the repository.
>
> Run Factory on a disposable VM/container or a backed-up clone. Do not point it at production credentials, customer data, or a repository you cannot restore. Review `policies/` and `.agt/policy.json` before first use. Hooks are fast feedback only and are bypassable; the release gate is the authority. Proofs are deterministic, not cryptographic. **No warranty. Use at your own risk.**

> [!IMPORTANT]
> **Factory is not an end-user product.** There is no UI, account system, onboarding wizard, or support desk. The real users are **AI agents** — orchestrator, builder, auditor, hacker, release engineer. Humans install it, grant scope, read verdicts, and stay accountable. If you want a coding assistant for non-technical users, this is not it.

**New installation?** Do not configure it by hand. Have the agent follow **[AGENT-SETUP.md](AGENT-SETUP.md)**. It asks the operator about worktree topology, gates, CI, branch protection, hooks, and sandbox policy before changing anything.

## What it is

- A small CLI (`factory`) plus JSON policies, SOPs, agent personas, hooks, and check runners.
- A task lifecycle: one `task.json` per task, one lane per worktree, one owner per lane.
- A gate system: the harness chooses checks, runs them, and writes canonical proofs outside the worktree.
- A release train: lane verdicts reconcile into an integration worktree, then pass release predicates and CI.
- A copy-only design: source trees are read-only; Factory itself is read-only to agents after setup.

## What it is not

| Not this | Why |
|---|---|
| A chatbot or IDE plugin | It is a CLI harness that an agent drives. |
| A prompt pack | Policies and gates are executable; prose is not the authority. |
| A sandbox by default | `factory-run` is opt-in. Without `bwrap`, the agent has your shell. |
| A universal check pack | The shipped defaults assume a Django/Postgres product. You must select gates for your stack. |
| A replacement for CI | It complements CI; release predicates can require live CI checks and branch protection. |
| A safe thing to point at production | Treat the target repo and host as disposable. |

## How it works

1. **Task.** `factory task new` records the kind, lane, worktree, base commit, and write scope in `task.json`. It is not an authorization token.
2. **Gate.** `factory gate` selects checks from policy, runs them through registered runners, and writes proofs under `FACTORY_HOME`. Narrowing is allowed; widening is refused.
3. **Proof.** Proofs are canonical JSON over base/head/tree, tool, environment, output hash, artifacts, and seed. Timing fields are excluded from `proof_root`.
4. **Release.** `factory release` validates the gate, task phase, scope, policy hash, lane heads, deletion risk, CI, and branch protection, then merges at a pinned head.

Parallel work is supported: `lane/a`, `lane/b`, and an integration lane. One task, one lane, one worktree. There is **no automatic lane-conflict prevention**; use disjoint scopes or serialize.

## Quickstart

```sh
git clone https://github.com/boredompeaks/factory-v2.git
cd factory-v2
npm install
python3 -m pytest -q tests
bash tests/smoke.sh

export PATH="$PWD/bin:$PATH"
export FACTORY_HOME="$HOME/.factory"
export FACTORY_EVIDENCE_ROOT="$FACTORY_HOME/evidence"
factory --version
```

Expected: tests pass, smoke prints `PASS`, and `factory --version` reports `2.0.0` or newer.

Then read **[AGENT-SETUP.md](AGENT-SETUP.md)**. Do not point Factory at a real repository by hand. That file makes the agent ask the operator for topology, gates, CI, hooks, and sandbox policy, then rewires every author-specific path.

Minimal manual path, after setup:

```sh
cd <your-worktree>
factory task new --kind FEATURE --lane lane/a --scope 'src/**' --worktree "$PWD"

# make and test the change
git add .
git commit -m "Implement the task" -m "Task-Id: <id-from-task.json>"

factory gate --checks SCOPE,CONVENTION   # fast feedback, never release-grade
factory gate --release                  # authoritative, needs the configured tools
factory verify
factory release --dry-run --repo <integration-worktree>
```

`factory gate --checks` may only narrow the policy gate. A real release refuses a narrowed gate. `factory` infers the task from the current worktree when `--task-id` and `FACTORY_TASK_ID` are absent, so `factory scope check --staged` works inside the pre-commit hook.

## Configure it for your repo

Factory ships configured for its original product. Setup is a decision, not a default.

| Area | What the agent must ask | Where it lives |
|---|---|---|
| Worktrees | one lane or parallel lanes, lane names, integration worktree | `policies/phases.json`, `policies/worktree-map.json` |
| Gates | which checks block, which are shadow, default tier | `policies/gate.json`, `policies/checks.json`, `policies/tiers.json` |
| CI / release | provider, check names, promotion branch, branch protection | `policies/ci.json`, `policies/ci-required.json`, `policies/branch-protection.json` |
| Hooks | install pre-commit/commit-msg/post-commit, repo-wide or per-worktree | `hooks/`, `core.hooksPath` |
| Stack | test command, Django vs other, changelog path | `policies/check-runners.json`, `policies/changelog.json` |
| Sandbox | `bwrap`, network isolation, Docker/K1 | `bin/factory-run`, `policies/concurrency.json` |

See **[AGENT-SETUP.md](AGENT-SETUP.md)** for the question set, exact commands, and stop conditions. If CI and branch protection are not configured, `factory release` is intentionally blocked; use `factory gate` and `--dry-run`.

## Requirements

- Linux or WSL2; POSIX shell.
- Python >= 3.11 and `git`.
- Node >= 20 and `npm` for the AGT censor plugin (optional; without it `AGT_PARITY`/`AGT_AUDIT` cannot run).
- `bwrap` for the sandbox (optional but strongly recommended).
- `ruff`, `gitleaks`, `pytest` for the corresponding checks/hooks (optional; missing tools report `TOOL_ABSENT`).
- Docker for K1/services (optional; required only for the checks that use them).

## Repository map

| Path | Purpose |
|---|---|
| `bin/factory` | CLI entry point |
| `bin/factory-run` | bubblewrap sandbox wrapper |
| `factory/` | CLI core, checks, gate, proof, release, task state |
| `policies/` | JSON source of truth for tasks, gates, CI, risk, tiers |
| `sop/` | numbered role SOPs |
| `agents/`, `.opencode/` | seven personas and their OpenCode deployment copies |
| `skills/` | role skills loaded on demand |
| `hooks/` | pre-commit, commit-msg, post-commit, installer |
| `schemas/` | live task/proof/gate JSON shapes |
| `tests/` | CLI, policy, isolation, and smoke tests |
| `docker/`, `scripts/`, `tools/` | K1 image, host services, scanners, support scripts |
| `docs/`, `runbooks/` | design notes and operational runbooks |

## Safety model

- **Scope containment.** The task records write globs; `SCOPE` rejects staged changes outside them.
- **Harness-computed proofs.** Agent claims are not evidence. Proofs record revisions, commands, tool versions, environment, exit codes, output hashes, artifacts, and fuzz seeds.
- **Read-only Factory.** Agents may work in assigned worktrees, not in `factory/`, `policies/`, `sop/`, `skills/`, `agents/`, or `.agt/`.
- **Sandbox.** `factory-run` mounts the worktree writable, the rest of the host read-only, and can disable networking. It must be installed and invoked deliberately.
- **Hooks.** Pre-commit, `Task-Id:`, and post-commit evidence are fast feedback. They are bypassable and do not replace the release gate.
- **Censor.** The AGT plugin redacts sensitive output and blocks selected secret reads, unsafe download-and-execute patterns, metadata access, and recursive deletion. Its audit log is not proof.

**No-deletion guarantee:** Factory operations are additive. They do not remove source files, rewrite source history, or silently discard work. A component not present in this stripped copy was not copied; it was not deleted from its origin.

## Known limitations

- `policies/schemas/` is legacy reference; live shapes are `schemas/*.json`.
- A full release gate needs the configured tools, CI, and branch protection. Missing tools report `TOOL_ABSENT`, never `PASS`.
- `SUITE` is Django-specific in this build; other stacks need an adapter or a deliberate gate change.
- Proofs are deterministic, not cryptographic; verdict authorship is not signed.
- Budgets are policy intent, not metered enforcement.
- Lane conflicts are not detected automatically.
- The shipped policies, SOPs, and checks retain product-specific assumptions from the original repo.

See [STATUS.md](STATUS.md) for the current build status and residual failures.

## Documentation

| Document | Use |
|---|---|
| **[AGENT-SETUP.md](AGENT-SETUP.md)** | Agent onboarding: decouple, bind to a repo, choose gates/CI/hooks |
| [DESIGN.md](DESIGN.md) | Design rationale and layer model |
| [PLAN.md](PLAN.md) | Build plan and scope |
| [STATUS.md](STATUS.md) | Verification status and known gaps |
| [docs/GATE-HIERARCHY.md](docs/GATE-HIERARCHY.md) | Invariant/hygiene/debt/exception gates |
| [docs/DISPATCH-RULES.md](docs/DISPATCH-RULES.md) | Task minting and dispatch routing |
| [docs/CHANGELOG-LINEAGE.md](docs/CHANGELOG-LINEAGE.md) | Changelog and commit-trailer rules |
| [runbooks/](runbooks/) | K1, services, gate, release, worktrees, PR |
| [sop/](sop/) | Role procedures |
| [policies/](policies/) | Authoritative workflow, gate, CI, and risk policy |

## Status and license

Factory v2.0.0. The current test suite has 109 passing tests; `tests/smoke.sh` covers task → gate → verify → release dry-run.

No license file is present yet. The repository is public for inspection; do not redistribute or reuse until a license is added.
