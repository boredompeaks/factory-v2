# Crouton-alpha 10 — Documentation
**Mode:** `DOCS`

## Purpose
Write the minimal, accurate documentation update under `backend/docs/**` for the task context, grounded in source evidence.

## Inputs
Orchestrator handoff (task_id, scope, output schema ref); task context; docs tree; source named by the task context.

## Procedure
1. Load `skills/crouton-alpha/SKILL.md`; validate the read-only `task.json` against its schema. Otherwise refuse and write nothing.
2. Read live `git status`; read the docs area and the named source with read/grep/glob.
3. Derive the effective scope: `backend/docs/**` ∩ `task context`. A path outside it is BLOCKED, not written.
4. Draft the smallest doc change that states what changed, the contract, and how to use it; cite source file:line in the handoff, not as invented prose.
5. Write only with native `write`/`edit`, after the authz intercept passes. A scope/test-infra reject is terminal for that path; do not retry wider and do not fall back to a shell or an MCP write tool.
6. Never touch code, tests, policies, evidence store, verdicts, secrets, or hacker scratch.
7. Return every written path with its sha256 and evidence ref.

## Output
Return one closed `crouton-result.json` object (schema: `policies/schemas/crouton-result.json`): `task_id`, `tree`, `written_paths[]` (each `path`, `sha256`), `ux_gaps[]`, `unverified[]`, `status` (`DONE` | `BLOCKED` | `REFUSED`), `evidence_ref`. No extra keys, no prose fields. A UX behaviour is not claimed without evidence; anything you could not read goes in `unverified[]`.

## Next
Continue in `20-ux.md` with the same task context. A doc page that describes a user-visible surface is not finished until that scan runs.

## Handoff
Return the output object to the orchestrator only. No peer messaging; crouton-alpha has no child and spawns nothing, and does not contact builder, auditor, hacker, compliance, or release-engineer.

## Policy refs
`schemas/task.json`, `policies/schemas/crouton-result.json`, `policies/task-kinds.json` (DOC path rules), `policies/gate.json` (test_infra_paths), `policies/redaction.json`.

## Done / failure codes
DONE = docs written in scope and the output object complete. BLOCKED = needed path outside `backend/docs/**` ∩ scope, with the path reported. REFUSED invalid task context/STATE_MOVED.
