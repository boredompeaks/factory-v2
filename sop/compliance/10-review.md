# Compliance 10 — Spec/compliance review
**Mode:** `COMPLIANCE`

## Purpose
Compare one spec section against real code, producing a complete requirement registry with unique ids and evidence-backed gaps. Read-only; never fixes or builds. **Scope:** spec parsing, requirement extraction, implemented-check, and drain review. Compliance is not a gate before every builder task: ordinary build, bugfix, release, and hygiene tasks never dispatch it. Spec completion is the routine caller; the orchestrator may also request a standalone section review.

## Inputs
Orchestrator handoff: section number, exact spec line range, target area, conventions, deferred rows. Task scope. `backend/docs/conventions.md`. Prior section reports under `backend/docs/compliance/` and the `quality-scan/` corpus — the id scan in step 2 reads these.

**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:compliance`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

## Procedure
1. Load the `compliance` skill; validate the task context/envelope. The review is read-only and has no command execution.
2. **Scan before allocating ids.** Grep for `REQ-` and for the bracket product form `[R-` over the product docs (`backend/docs/`, including `backend/docs/compliance/`) and the quality-scan corpus (`quality-scan/reports/`, `quality-scan/*.md`). Record roots, patterns, hit count, the highest existing `REQ-<section>.<n>`, and the first id allocated. The next id is one past that maximum; a retired id is never reused. The scan evidence is required in the report (`30-docs.md`), not optional.
3. **Recall prior section reports.** Query `basemind_memory` read-only for earlier section reports, owner decisions, and resolved gaps at this section; use the result only to cross-check the scan and seed the gap list. The live `backend/docs/compliance/` artifacts remain the id source of truth: a recalled id with no live file is not allocation evidence.
4. Extract every testable requirement in order as `REQ-<section>.<n>` — always the `REQ-` prefix, never an `R-` id. Product requirement ids (`[R-x.y]`) are external references: cite them, never mint or renumber them. If the scan shows the candidate id already taken, renumber before writing anything.
5. Batch searches by keyword cluster; read each candidate file once; keep an evidence map.
6. Classify each requirement IMPLEMENTED / PARTIAL / MISSING / DEVIATES / NOT-APPLICABLE / DEFERRED->S<x>, citing file:line.
7. Check conventions: permissions, Decimal money, atomic blocks, explicit serializer fields, env config, tests.
8. Severity-rank build work P1/P2/P3 per `policies/severity.json` semantics; scope tasks, never implement.
9. Do not write the section file here. Continue in `30-docs.md`: it owns the exact table columns, the ID ALLOCATION SCAN block, and the `compliance-result.json` shape. "Write the report" means that step, not this one.
10. Return the complete registry assembled for `30-docs.md`: every `REQ-` id appears; gaps are never compressed away.

## Handoff
Continue to `30-docs.md` to write `backend/docs/compliance/S<n>.md` and emit the result JSON. Return the report to the orchestrator only. Do not spawn or message builder, auditor, hacker, release-engineer, or crouton-alpha; compliance never talks to peers.

## Policy refs
`policies/severity.json`, `policies/task-kinds.json`, `policies/gate.json`, `schemas/task.json`, `policies/schemas/compliance-result.json`; `backend/docs/conventions.md`.

## Done / failure codes
DONE = registry complete, ids unique against the scan, and STATUS agrees with the items. `PARTIAL` when missing/partial/deviates remain. BLOCKED = scope/path outside task context; report the needed paths and write nothing else.
