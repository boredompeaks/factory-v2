# Auditor 60 — Ship durability push
**Mode:** `AUDIT`

## Purpose
On PASS, make the audited branch durable by pushing that branch once, so the release-engineer has a stable promotion head. Durability only: not a release, and it grants no merge/PR authority.

## Inputs
Task context (branch, head, tier); the completed audited verdict; `policies/worktree-map.json`; live `git status`.

**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:auditor`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

## Procedure
1. Confirm the verdict object is complete and ; push only after it exists.
2. Resolve the audited branch → worktree strictly from `policies/worktree-map.json` (lane/a, lane/b, spec-comp). feat/add-frontend is never pushed here.
3. Re-read live `git status` for that branch/worktree; HEAD must equal the audited head and the checked-out branch must match.
4. Call `git push` exactly once. The implementation is fast-forward only, refuses other refs/branches, and refuses a head/tree/branch that does not match the PASS verdict.
5. Record the returned pushed sha as evidence. It is not a verdict field.
6. A failed push is recorded in the handoff as `FAILED_PUSH` with the refusal text; it does not change the verdict. Do not retry with force, a different ref, or a second push.
7. Never merge, never create/close/comment a PR, never push another ref.

## Handoff
Return the unchanged verdict plus push evidence to the orchestrator. The release-engineer alone reconciles, opens the PR, watches checks, and merges. No peer messaging.

## Policy refs
`policies/worktree-map.json`, `policies/gate.json`, `policies/schemas/auditor-verdict.json`, `policies/schemas/release-result.json`.

## Done / failure codes
DONE = pushed sha equals audited head, or no push required at this boundary. FAILED_PUSH recorded with the verdict unchanged. REFUSED STATE_MISMATCH when branch/head differ; never force.
