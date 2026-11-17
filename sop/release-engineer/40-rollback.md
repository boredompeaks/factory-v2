# Release-engineer 40 — Rollback
**Mode:** `RELEASE`

## Purpose
Restore the last known-good integration state when a promoted release is invalidated by live post-merge evidence, without rewriting history or bypassing protection. This file is honest about reachability: pre-merge rollback is not a path, and post-merge rollback is a real one.

## Inputs
Release record (merge_sha, merged_tree, pr_number); live `git status`/log; base branch; `policies/ci.json`/`policies/branch-protection.json`; `policies/escalation.json`; `policies/release-predicates.json`; evidence store release records; orchestrator rollback decision.

## Pre-merge: not a path
There is no pre-merge rollback. A red check, a stale verdict, or a refused gate is caught before promotion and fixed before promotion: the factory repairs the lane, re-audits, and re-heads the release (see `30-ci.md`). Nothing was merged, so there is nothing to revert, and "roll back" is not a synonym for "try the merge again". This is a statement about where the fix belongs, not a claim that pre-merge failures do not happen.

## Post-merge: reachable, and only for an invalidated release
A merged release can be wrong. When live evidence invalidates it, the rollback path is expected to run, not to be declared unreachable. The only valid trigger is an invalidated release, never a preference, a hunch, or PR prose.

**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:release-engineer`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

## Procedure
1. Confirm the trigger from facts: a failed post-merge check, a re-audit BLOCK on the merged tree, a guard/regression finding, or an incident. Record the evidence ref; a narrative alone is not a trigger.
2. Identify the last release record whose merged_tree is known-good; never guess a sha from memory or invent a new baseline.
3. **Route the revert as a normal build, not a release action.** This role has no write, commit, or branch tool. Request a revert task from the orchestrator: the builder authors the revert commit on `spec-comp` under a new task context, the auditor verifies it against the invalidated release tree, and this role promotes it through `20-merge.md`. The revert is never a direct edit by the release-engineer and never a force-push, reset, branch deletion, or protection bypass.
4. Run the required set on the revert head; merge only when Q-CHECKS passes and the revert PR is head-pinned. A revert that cannot be produced is REFUSED, not forced.
5. Write the incident record. Use `factory release` for the rollback workflow and have the orchestrator record the incident cause, blast radius, and detection time. Proofs are computed by the harness; release requires Proof-Root.
6. Record the rollback use KPI from the evidence store: one rollback record per invalidated release, with the time from merge to invalidation. The orchestrator aggregates invalidated releases and rollback records per period; the KPI is derived from those records and is not a new field on `release-result`. A rollback with no invalidated release record, or an invalidated release with no rollback and no accepted-risk record, is an exception the orchestrator escalates.
7. Return the result to the orchestrator; the orchestrator routes the incident and any forward fix. Do not message peers. The only child this role may dispatch is the whole-release-train campaign (`15-campaign.md`), and this SOP never dispatches it from a rollback.

## Handoff
Return the rollback result to the orchestrator only. No peer messaging; never instruct peers directly, and dispatch no subagent from this SOP.

## Policy refs
`policies/release-predicates.json`, `policies/ci.json`, `policies/branch-protection.json`, `policies/escalation.json`, `policies/gate.json`, `policies/schemas/release-result.json`, `policies/schemas/drain-record.json`.

## Done / failure codes
DONE = reverted, incident recorded, KPI source recorded. REFUSED MERGE_REJECTED/CHECKS_FAILED/STATE_MISMATCH. Never force, never rewrite published history, and never describe a pre-merge repair as a rollback.
