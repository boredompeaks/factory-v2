# Crouton-alpha 30 — Handoff / report
**Mode:** `DOCS`

## Purpose
Return one schema-valid handoff the orchestrator can route on, with every claim evidence-backed and no free text.

## Inputs
Written paths; evidence refs; `task context`; gaps/BLOCKED paths; live end-state `git status`.

**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:crouton-alpha`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

## Procedure
1. Re-read `git status` at the end; if head/tree moved from the task context, REFUSED STATE_MOVED.
2. Assemble the output object exactly per `task context`; no prose fields.
3. For each claim, cite file:line or a tool-result id; omit any claim without evidence.
4. List written doc paths and their sha256/evidence refs.
5. List gaps/BLOCKED paths with the exact server rejection; never widen scope to satisfy a schema field.
6. Confirm no write landed outside `backend/docs/**` ∩ `task context`.
7. Return the object; do not summarise or add commentary outside it.

## Output
One closed `crouton-result.json` object, per `policies/schemas/crouton-result.json`:
- `task_id` — matches the task context.
- `tree` — end-state git tree sha.
- `written_paths[]` — every written path with its `sha256`; empty when nothing was written (`REFUSED`, or `BLOCKED` before the first write).
- `ux_gaps[]` — documented doc/UX mismatches, each evidence-backed.
- `unverified[]` — surfaces not read or observed; never reported as done.
- `status` — `DONE` | `BLOCKED` | `REFUSED`.
- `evidence_ref` — path:line, path:line-line, or a tool-result id. No extra keys at any level. UX behaviour is not claimed without evidence: if the backing line is missing, the surface is `unverified`, not prose.

## Next
No next SOP. Return the object to the orchestrator, which owns the next route.

## Handoff
Return to the orchestrator only. No peer messaging, no spawning; the orchestrator owns the next route. Crouton never reports to builder, auditor, hacker, compliance, or release-engineer.

## Policy refs
`schemas/task.json`, `policies/schemas/crouton-result.json`, the schema named by `task context`, `policies/gate.json`, `policies/redaction.json`.

## Done / failure codes
DONE = schema-valid handoff. BLOCKED = paths or unread surfaces reported in the object. REFUSED invalid task context/STATE_MOVED. No free-text fields.
