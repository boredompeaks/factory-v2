# Compliance 20 — Drain and register review
**Mode:** `DRAIN`

## Purpose
Verify a drain cluster is being drained correctly: rows are confirmed, specs falsifiable, stages recorded, and the register untouched except through drain records. **Scope:** drain lineage review only. This is not a gate before every builder task; the orchestrator requests it for a drain cluster, not for ordinary build work. Stage sequence, tokens, required fields, clustering order, and row caps live in `policies/drain.json`; this file references them and never restates their values.

## Inputs
Drain task context (cluster_id, stage, rows); drain register rows; `policies/drain.json`; `policies/schemas/drain-record.json`; `policies/schemas/repro-spec.json`; `factory drain validate` output; the `DR-<stage>-<n>` review ids allocated for this cluster.

**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:compliance`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

## Procedure
1. Validate the task context and stage. Every reviewed item gets a `DR-<stage>-<n>` id (`DR-R1-1`, `DR-R2-2`,...), where `<stage>` is the stage token from `policies/drain.json#stage_order`. `REQ-` ids belong to `10-review.md` and are never minted here; a drain item is never labelled with a bare stage token. Numbering is sequential across the report's clusters, so one report never repeats a `DR-` id.
2. Clustering: order and row cap per `policies/drain.json#clustering`; splits by locus prefix only.
3. `DR-R1-1`: confirm each cited locus still exhibits the behaviour at the task context head; unreproducible rows get the attempt record named by `policies/schemas/drain-record.json#attempts` and `policies/drain.json#unreproducible`, never deletion.
4. `DR-R2-1`: confirm one executable repro-spec per cluster that fails on base and names the invariant; the auditor reproduces, the hacker authors.
5. `DR-R2-2`: **attempt cap — bounded, never thrashing.** The cap is `max_attempts` on the R2 stage in `policies/drain.json#stages`; never hard-code or infer it. Count every spec rewrite, re-run, and NON_FALSIFIABLE return as one attempt; a repeat at the same tree with the same command is the same attempt, not a new one. When the cap is exhausted, record UNREPRODUCIBLE with the `attempts` and `attempt_log_ref` fields of `policies/schemas/drain-record.json` plus the fields named by `policies/drain.json#unreproducible`, then stop: no extra attempt, no re-wording of the same invariant, no lane rotation to reset the count. The attempt log is the evidence; the row is never deleted.
6. `DR-R3-1`: confirm acceptance refs fail on base with fail_kind ASSERTION; base-green → NON_FALSIFIABLE, back to `DR-R2-1`, and that return consumes one attempt per step 5.
7. `DR-R4-1` / `DR-R5-1`: confirm verdict/release/guard fields and the permanent guard (present, unskipped, passing) against each stage's `exit_when` in `policies/drain.json#stages`; removal or skip reopens the first stage.
8. Run `factory drain validate` in verify mode; record PASS/FAIL with records_checked/problems. A stale digest is FAIL, not green.
9. Never edit the register; drain records are the audit trail.
10. Continue in `30-docs.md` to write the section file and the result JSON; the handoff carries the `DR-` registry plus the id-allocation scan evidence for any `REQ-` ids it cites.

## Handoff
Continue to `30-docs.md`, then return the review and evidence to the orchestrator only. No peer messaging; the orchestrator routes the stage and no peer is contacted.

## Policy refs
`policies/drain.json`, `policies/task-kinds.json`, `policies/schemas/drain-record.json`, `policies/schemas/repro-spec.json`, `policies/schemas/compliance-result.json`, `policies/assert-rules.json`, `policies/reuse.json`.

## Done / failure codes
DONE = stage verified PASS. FAIL = stale/invalid record or missing field. UNREPRODUCIBLE only when `max_attempts` from `policies/drain.json` is exhausted or the locus no longer reproduces, with the record fields `policies/drain.json#unreproducible` names (record, do not delete). BLOCKED = tool/scope fault.
