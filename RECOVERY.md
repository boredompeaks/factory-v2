# Recovery record — 2026-11-16

## What happened

The v2 build was created under `/tmp/opencode/factory-v2` because that was the
approved scratch scope. `/tmp` is tmpfs on this host, and the box rebooted on
2026-11-16. The tree was lost. That was a durability mistake: the deliverable
should have been mirrored to a persistent path and committed.

## What was recovered

Source: the OpenCode SQLite DB (`~/.local/share/opencode/opencode.db`) still
contained the subagent tool payloads:

- `patch` calls (`*** Begin Patch` format) for every file the CLI, checks,
  rewrite, and hooks agents created or updated.
- `write` and `edit` calls for the main session's post-build fixes.
- `shell` heredocs for the rewrite agent's bulk persona/SOP/policy edits and
  the main session's wiring/permission scripts.

Recovered into `/home/boredompeaks/factory-v2` by
`/home/boredompeaks/factory-v2-recover.py` (copy kept at
`tools/recover_from_db.py`).

Seven patch hunks did not apply cleanly; each was an intermediate edit that a
later write or edit superseded. The final acceptance tests pass.

## Verification

- `python3 -m pytest -q tests/test_cli.py` → 7 passed
- `bash tests/smoke.sh` → PASS
- `python3 -c "from factory.checks import CHECKS; print(len(CHECKS))"` → 26
- `node -e "import('@microsoft/agent-governance-opencode/policy')..."` → policy loads
- `bin/factory --version` → 2.0.0

## What was not recovered

The evidence/proof files that lived under `/tmp` are gone. The OpenCode DB stores
messages and tool payloads, not filesystem snapshots, and its `blob` table is
empty. They are not reconstructible byte-for-byte from the DB.

They are, however, **recomputable by design**: proofs are harness-generated from
`base/head/tree` + pinned tools + policy hash, and fuzz seeds are recorded in
each proof. Re-run `factory gate` on the same task branches to regenerate the
proof set. The V1 audit evidence at `/home/boredompeaks/audit-20261115-local/evidence`
(233 files) is a separate artifact and was not touched.

## Durability rules going forward

- Work in `/home/boredompeaks/factory-v2`, never `/tmp`.
- This directory is a git repo; commit after each change.
- Backups: `/home/boredompeaks/factory-v2-20261116.tar.gz`.
- `node_modules` is a symlink to the source install for local use; run
  `npm install` in this directory for a portable install.
- `evidence/` is gitignored and regenerable.
