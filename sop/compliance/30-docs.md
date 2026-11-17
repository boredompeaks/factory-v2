# Compliance 30 — Compliance documentation output
**Mode:** `COMPLIANCE`

## Purpose
Produce the durable compliance artifact, the machine-readable result, and the terse handoff that lets the orchestrator verify the sweep without re-reading the spec. Entered from `10-review.md` or `20-drain.md`; this is the only compliance step that writes.

## Inputs
Section/range, the registry from `10-review.md` or `20-drain.md`, the id-allocation scan evidence, sweep verdicts (`factory verify`, `factory drain validate`, `factory gate`, `factory check POLICY_DIGEST`), task context scope.

**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:compliance`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

## Procedure
1. Assemble the registry in id order: one row per `REQ-<section>.<n>` (or `DR-<stage>-<n>`). Never merge, drop, or range a gap row.
2. The table columns are fixed, in this order: `| id | status | evidence | gap |`.
- `id` — `REQ-<section>.<n>` or `DR-<stage>-<n>`; exactly as allocated in the scan.
- `status` — one of `IMPLEMENTED`, `PARTIAL`, `MISSING`, `DEVIATES`, `NOT-APPLICABLE`, `DEFERRED->S<x>`; no other token. In JSON, `DEFERRED->S<x>` serializes as status `DEFERRED` with the owning section in `gap`.
- `evidence` — `path:line` or `path:line-line`, or `none` plus the searches actually run.
- `gap` — the exactly missing piece; `—` only when status is `IMPLEMENTED` or `NOT-APPLICABLE`. A `DEFERRED->S<x>` row names the owning section here. Contiguous `IMPLEMENTED`/`NOT-APPLICABLE` runs may share a range row only when the evidence cell still cites every file:line; `PARTIAL`, `MISSING`, `DEVIATES`, and `DEFERRED` rows are never ranged.
3. Write the ID ALLOCATION SCAN block into the same file: patterns (`REQ-`, `[R-`), roots (`backend/docs/`, `quality-scan/`), hit count, highest existing `REQ-<section>.<n>`, first id allocated, and the collision result (`none` or the renumbering applied). A report without this block is incomplete.
4. Record sweep verdicts separately: harness_config, policy_manifest, drain stage, gate chain, POLICY_DIGEST digest/match.
5. Add CONVENTION FINDINGS and TEST COVERAGE OBSERVATION sections with file:line.
6. Emit the result as `compliance-result.json`, matching `policies/schemas/compliance-result.json` exactly. The shape is closed — no extra keys at any level: `schema` (`compliance-result/1`), `section`, `range` (`path`, `start_line`, `end_line`), `requirements[]` (each with `id`, `status`, `evidence`, `gap`), `status_counts` (one non-negative integer per status token; `DEFERRED` counts the deferred rows), `uncovered[]` (ids or spec ranges not reviewed), `verdict` (`COMPLETE` | `PARTIAL` | `BLOCKED`). A `COMPLETE` verdict requires `PARTIAL`/`MISSING`/`DEVIATES` counts of zero and an empty `uncovered`; a `BLOCKED` verdict requires a non-empty `uncovered`.
7. Write the section artifact pair — `backend/docs/compliance/S<n>.md` and `backend/docs/compliance/S<n>.json` — via native `write`/`edit` only. Authz intercepts allow only `backend/docs/compliance/S[0-9]*.md` and `S[0-9]*.json`, intersected with `task context` and this section's stem; overwrite only these two files on re-check. The permission glob cannot express a digits-only stem, so a tail after the digits is refused by the task context/section check, never admitted on the glob alone. Any other path is a STOP, not a retry and not a wider glob.
8. Return the full registry in the `compliance-result.json` shape; `verdict` and `status_counts` must agree with the rows and `uncovered`.
9. Never report `COMPLETE` while any sweep verdict is FAIL or `uncovered` is non-empty; name the failing tool and field.

## Handoff
Return to the orchestrator only. No peer messaging; compliance has no child and spawns nothing, and never contacts builder, auditor, hacker, release-engineer, or crouton-alpha.

## Policy refs
`policies/severity.json`, `policies/gate.json`, `policies/drain.json`, `schemas/task.json`, `policies/schemas/compliance-result.json`; `backend/docs/conventions.md`.

## Done / failure codes
DONE = section artifacts written, scan block present, and registry complete. A `PARTIAL` or `BLOCKED` verdict is reflected honestly, never upgraded to `COMPLETE`. BLOCKED = write outside `backend/docs/compliance/S<n>.{md,json}` or `task context`; report the rejected path.
