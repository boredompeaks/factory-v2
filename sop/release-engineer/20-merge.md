# Release-engineer 20 — Merge / PR contract
**Mode:** `RELEASE`

## Purpose
Reconcile the lanes into spec-comp, push it, open or reuse the promotion PR, watch required checks, and merge at a pinned head without bypass. A red required check is repaired and re-headed, never merged around. Each gate is a named predicate defined in `policies/release-predicates.json`; this SOP cites the id and never restates the test.

## Inputs
Promotion-gate result; worktrees list; reconciled head/tree; `policies/ci.json` (base branch, promotion branch, merge method); `policies/tiers.json` (cycle cap); `policies/escalation.json`; `policies/release-predicates.json`; GitHub CLI commands; live `gh api branch protection`.

## Procedure
1. Sync each permanent worktree from origin using Git; stop on dirty/wrong-branch/conflict, push nothing.
2. Reconcile lane/a then lane/b into spec-comp (`git merge`); capture merge shas, reconciled head/tree. Conflict → REFUSED MERGE_REJECTED.
3. Re-read state: refs/heads/spec-comp and tree equal the reconciled values (Q-STATE).
4. `git push` spec-comp at expected_head; capture the pushed sha (Q-PRHEAD input).
5. `gh pr create`: reuse an open promotion PR or create one. `body` is an optional multiline string (max 65536, no leading `-`); carry only ids and hashes ( release record hash, head, tree, lane merge shas, verdict refs). When omitted, the tool sends an empty body; on reuse the existing body is left unchanged (`body_applied: false`). Verify the base is `policies/ci.json.promotion_branch` and Q-BASE.
6. `gh pr checks` exactly once for the release head; then re-check Q-PRHEAD/Q-BASE and call `gh api branch protection` live for the promotion base at the PR base OID immediately before merge. A stale policy-file `live_verified` flag is not proof.
7. If the watch is red, times out, or reports a missing/unmapped required id: do not merge, do not re-watch the same head, and do not clear it by editing a workflow, the required set, or a check. A missing mapped name or an `unmapped` required id is `CHECKS_UNMAPPED`, not a pass. Run the CI-red loop in `30-ci.md`: the orchestrator routes the patch, the builder repairs the lane, the auditor re-audits the new head, and the release-engineer reconciles, re-heads the PR, and watches the new head once. The loop is bounded by `policies/tiers.json[tier].max_cycles`; at the cap the release returns REFUSED `CHECKS_FAILED`/`CHECKS_TIMEOUT`/`CHECKS_UNMAPPED` with the cycle count and the orchestrator records `CYCLES_EXHAUSTED` and escalates.
8. **Verify release proofs.** Confirm `factory gate` passes for the pinned head/tree. Proofs are computed by the harness; release requires Proof-Root. Record product CI results with `gh pr checks` and do not merge if required checks fail.
9. `gh pr merge` at the pinned head using `policies/ci.json.merge_method` (Q-METHOD), only after required checks pass and Proof-Root is present. No admin/auto-merge/force. `.github/workflows/**` is release-engineer-owned: the builder, auditor, hacker, compliance, and crouton-alpha may not edit it, and no release clears a red check by changing a workflow or the required set.

## Handoff
Return the release-result object to the orchestrator only. No peer messaging; builder, auditor, hacker, compliance, and crouton-alpha are not contacted directly. The orchestrator wakes the same release-engineer context for a repaired head; a repair does not mint a second release task context for the same lineage.

## Policy refs
`policies/release-predicates.json`, `policies/ci.json`, `policies/ci-required.json`, `policies/tiers.json`, `policies/branch-protection.json`, `policies/worktree-map.json`, `policies/escalation.json`, `policies/schemas/release-result.json`, `policies/gate.json`.

## Done / failure codes
DONE = MERGED with merge_sha/merged_tree. REFUSED STATE_MISMATCH/BASE_MOVED/CHECKS_FAILED/CHECKS_TIMEOUT/CHECKS_UNMAPPED/MERGE_REJECTED. Never claim MERGED without a real merge sha. Cycle exhaustion is the orchestrator's `CYCLES_EXHAUSTED`, not a release-result token.
