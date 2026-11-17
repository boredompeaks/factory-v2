# Release-engineer 10 — Promotion gate
**Mode:** `RELEASE`

## Purpose
Decide whether the two permanent lanes may be promoted as one integration: every lane verdict is PASS and still matches live state and policy. Each gate is a named predicate defined in `policies/release-predicates.json`; this SOP cites the id and never restates the test. The whole-release-train campaign in `15-campaign.md` runs as a preceding step and is folded into the release record; it never changes a predicate.

## Inputs
Orchestrator section-boundary handoff; release task context; lane verdicts; live `git status`/`git rev-parse`; `policies/worktree-map.json`; `policies/ci.json`; `policies/release-predicates.json`; gate-control record; live branch protection.

**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:release-engineer`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

## Procedure
1. **Campaign first.** Run `15-campaign.md` for the whole release train and fold its one result into the release record. This is a preceding step, not a predicate: a campaign result never changes a Q-* outcome, and a campaign `NOT_RUN` is not gate failure.
2. Load `skills/release-engineer/SKILL.md`; validate the envelope and every lane verdict signature/hash (Q-SIG).
3. Evaluate Q-POLICY on the lane verdicts and the live gate-control record; a failure is REFUSED POLICY_DRIFT.
4. Evaluate Q-VERDICT on the release set and the task context's lane ids.
5. Evaluate Q-LANEHEADS on `git status` for origin/lane/a and origin/lane/b; a moved lane is STATE_MISMATCH.
6. Evaluate Q-CLEAN on `git status` for the spec-comp worktree; a failure is DIRTY_TREE.
7. Evaluate Q-PROT by calling `gh api branch protection` live for the promotion base at the PR base OID; the policy file's `live_verified`/`verified_at` is a historical observation, never a substitute for the fresh call. A 404 is PROTECTION_MISSING; any setting difference is PROTECTION_MISMATCH; a moved base is STATE_MISMATCH.
8. For DRAIN, resolve `task context` and require Q-GUARD.
9. Refuse on any failure; never downgrade, never treat a historical PASS as live.

## Handoff
Return the decision/result to the orchestrator only. Do not dispatch builders or message lanes. The only child this role dispatches is the whole-release-train campaign (`15-campaign.md`); it never messages a peer.

## Policy refs
`policies/release-predicates.json`, `policies/ci.json`, `policies/gate.json`, `policies/worktree-map.json`, `policies/branch-protection.json`, `policies/models.json`, `policies/schemas/release-result.json`.

## Done / failure codes
DONE = all gates true, proceed to merge. REFUSED SIG_INVALID/POLICY_DRIFT/VERDICT_NOT_PASS/STATE_MISMATCH/DIRTY_TREE/PROTECTION_UNVERIFIED/GUARD_REMOVED.
