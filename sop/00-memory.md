# 00 — Institutional memory protocol
**Modes:** every mode
**Role:** every role

## Purpose
Make lessons survive the task that learned them. SOPs say what to do; memory says what this repository has already taught the role. Memory lives in the shared basemind store, not in SOPs and not in the evidence store. It is advisory: never a gate input and never evidence.

## Inputs
- The `basemind_memory` MCP tool, backed by the basemind daemon for the factory workspace.
- `opencode.json` configures the server with `cwd: "."`; the shared root is the factory repo.
- `FACTORY_ROOT` is the factory checkout (the directory containing `bin/factory`); export it in the session or substitute the absolute path once.
- Direct CLI access: `basemind --root "$FACTORY_ROOT" memory ...`.
- The current task context and its evidence refs.

## Procedure

### 1. Read at orientation
At the start of every task, list or search memory for your role before planning:

```
# MCP tool, in-session
basemind_memory list  --tag role:<role> --limit 20
basemind_memory search "<locus, class, or symptom>" --tag role:<role>

# CLI equivalent, always pinned to the factory root
basemind --root "$FACTORY_ROOT" memory list --tag role:<role> --limit 20
basemind --root "$FACTORY_ROOT" memory search "<query>" --tag role:<role>
```

Add `--json` for machine-readable output. Read the newest entries first. Use the `task:` and `kind:` tags to narrow when the role view is noisy.

Carry only what changes the plan: a known seam, a probe refuted for a non-obvious reason, a decision that constrains this locus. Quote the memory key in working notes, not in a verdict.

### 2. Write during the task, not only at the end
Put one record when any of these happens:

- a seam between two changed surfaces is found or ruled out;
- a gotcha costs real time (tool, engine, fixture, ordering, policy surprise);
- a decision is made that a later task would otherwise re-litigate;
- an incident or near-miss is understood.

Put one record at task end in every case: the durable lesson, or `lesson` with the outcome if the task taught nothing new.

Key convention:

```
factory/<role>/<kind>/<task_id>/<slug>
```

`kind` is one of `lesson`, `gotcha`, `decision`, `seam`, `incident`. `slug` is a short kebab-case handle unique within the task.

Value: a compact record with the reusable facts, not a transcript:

```
summary: <one sentence>
detail: <context, invariant, observable symptom, and what to do differently>
evidence_refs: <repo-relative path:line, check id, record id, or campaign id>
tags: role:<role>, kind:<kind>, task:<task_id>, <locus tags>
```

Write it with the MCP tool or the CLI:

```
basemind_memory put "factory/<role>/<kind>/<task_id>/<slug>" "<record>" \
  --tag role:<role> --tag kind:<kind> --tag task:<task_id> --tag <locus>

basemind --root "$FACTORY_ROOT" memory put \
  --tag role:<role> --tag kind:<kind> --tag task:<task_id> \
  "factory/<role>/<kind>/<task_id>/<slug>" "<record>"
```

Good records are specific and reusable: name the locus shape, the invariant, and the observed symptom. Do not record a verdict, a severity, or a claim that belongs in the evidence store. Re-putting the same key updates the record; use that for a correction rather than creating a near-duplicate.

### 3. Treat memory as advice
- A remembered finding is a **pointer**, never evidence. Re-run its reproducer or probe before citing it as a finding.
- A remembered PASS, verdict, coverage count, or lane head is stale by construction. Live state and fresh evidence win.
- If memory disagrees with live state or a record, follow live state and route the disagreement per `sop/orchestrator/90-escalation.md`.
- Memory never substitutes for a required check, never satisfies a gate predicate, and never closes a finding.
- `basemind memory audit` reports stale/decayed entries. Audit output is housekeeping, not evidence about a task.

### 4. Redact before writing
- No secrets, tokens, passwords, private keys, cookies, or connection strings.
- No customer data, personal data, raw request or response bodies, or production payloads.
- Cite evidence by repo-relative ref (`path:line`, record id, check id), not by pasting the content.
- If a secret reaches the store, delete that key immediately (`basemind memory delete <key>`), record a `gotcha` without the value, and report the incident to the owner. `delete` removes the entry; it is not a substitute for redacting before write.

### 5. Keep scratch separate
Per-task scratch is `~/.factory/scratch/<role>/<task_id>/`. It is disposable and may be cleaned with the task. Durable memory is the basemind store and survives task cleanup; do not point scratch at the store and do not write memory into the product worktree.

### 6. Scope the store correctly
The shared store is scoped to the workspace root, so the root must be the factory repository, not a home directory:

- MCP: the server in `opencode.json` runs with `cwd: "."`; start sessions from `$FACTORY_ROOT` so `cwd: "."` scopes the shared store to the factory repo.
- CLI: always pass `--root "$FACTORY_ROOT"`. A bare `~/.factory` root is refused by basemind unless that root has been initialized.
- A record written from the wrong root is invisible to the factory MCP server. Re-put it under the factory root; do not create a second memory store.

## Handoff
Memory keys and summaries travel in working notes, never as verdict fields or gate evidence. A downstream role that needs the lesson re-runs the recorded probe; it does not inherit the conclusion.

## Policy refs
`opencode.json` (basemind MCP server), `sop/00-memory.md`, `docs/INSTITUTIONAL-MEMORY.md`, `policies/redaction.json`, `policies/schemas/fault-codes.json`.

## Done / failure codes
DONE = orientation read and at least one end-of-task record written.
REFUSED = the record would contain a secret or customer data; redact and retry.
SCOPE_ERROR = the CLI root was not `$FACTORY_ROOT`; re-put under the factory root.
