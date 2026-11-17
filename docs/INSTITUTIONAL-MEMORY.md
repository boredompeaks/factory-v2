# Institutional memory

Institutional memory is what the factory learns that is not a rule: a seam that
bit once, a tool surprise, a decision that should not be re-litigated. It is
stored in the shared `basemind` memory service, not in the SOPs and not in the
evidence store.

## Why it lives outside the SOPs

An SOP is a procedure that must stay true for every task. A lesson is usually
true for one locus, one engine, or one release train. Writing lessons into SOPs
would turn local observations into universal rules, make the procedure files
unstable, and tempt an agent to cite a remembered conclusion instead of running
a check.

Memory is therefore a separate, append-oriented store with four properties:

- **Out-of-band.** It does not change a gate predicate, a required check, or a
  verdict field.
- **Durable.** It survives task cleanup, worktree removal, and session loss.
- **Scoped.** It is shared per workspace root, so the factory repo sees the
  factory's memory and not another repository's.
- **Advisory.** It supplies pointers and hypotheses. It never supplies proof.

Per-task scratch (`~/.factory/scratch/<role>/<task_id>/`) is the opposite: it is
ephemeral, owned by one task, and may be cleaned at any time. Scratch is not
institutional memory.

## The store

- **Service:** `basemind` memory, exposed in-session as the `basemind_memory`
  MCP tool. Subcommands: `put`, `get`, `list`, `search`, `delete`, `audit`.
  `--tag` filters and `--json` is machine-readable.
- **Configuration:** `opencode.json` starts the server with `cwd: "."`. Start
  the session from the factory root so the shared store is scoped to this
  repository.
- **CLI:** set `FACTORY_ROOT` once to the factory checkout (the directory containing
  `bin/factory`), then pass the root explicitly:

  ```
  export FACTORY_ROOT=/path/to/factory
  basemind --root "$FACTORY_ROOT" memory list --tag role:auditor
  ```

  A bare `~/.factory` root is refused by basemind unless that root has been
  initialized. A record written under the wrong root is invisible to the MCP
  server; re-put it under the factory root. Do not create a second store.
- **Fallback:** there is no local JSONL store and no `tools/memory.py`. If the
  service is unavailable, treat memory as empty for that task and report the
  outage; do not invent a file-based substitute.

## Key and tag convention

```
factory/<role>/<kind>/<task_id>/<slug>
```

- `role` is one of `orchestrator`, `builder`, `auditor`, `hacker`,
  `release-engineer`, `compliance`, `crouton-alpha`.
- `kind` is one of `lesson`, `gotcha`, `decision`, `seam`, `incident`.
- `task_id` is the task, sweep, or release-train key the lesson belongs to.
- `slug` is a short kebab-case handle unique within the task.

Every record carries at least:

```
--tag role:<role> --tag kind:<kind> --tag task:<task_id>
```

plus locus tags (`tenancy`, `migration`, `k1`, `authz`, ...). Tags are how
orientation filters a role's stream and how a later search narrows by area.

The value is a compact record, not a transcript:

```
summary: <one sentence>
detail: <context, invariant, observable symptom, and what to do differently>
evidence_refs: <repo-relative path:line, check id, record id, campaign id>
```

Re-putting the same key updates the record. Use that for a correction rather
than creating a near-duplicate. `delete` is for a wrong or unsafe record, not
for hiding an inconvenient one.

## Read protocol

At orientation, before planning:

```
basemind_memory list  --tag role:<role> --limit 20
basemind_memory search "<locus, class, or symptom>" --tag role:<role>
```

Read the newest entries first. Use `task:` and `kind:` tags to narrow the view.
Carry forward only what changes the plan. Quote the memory key in working
notes, never in a verdict field.

`basemind memory audit` reports stale or decayed entries. Audit output is
housekeeping; it is not evidence about a task.

## Write protocol

Write one record during the task whenever a seam is found or ruled out, a
gotcha costs real time, a decision would otherwise be re-litigated, or an
incident is understood. Write one record at task end in every case, even when
the lesson is "nothing new".

```
basemind_memory put "factory/<role>/<kind>/<task_id>/<slug>" "<record>" \
  --tag role:<role> --tag kind:<kind> --tag task:<task_id> --tag <locus>
```

Good records are specific and reusable: name the locus shape, the invariant,
and the observed symptom. Do not store a verdict, a severity, or a claim that
belongs in the evidence store.

## Redaction

- No secrets, tokens, passwords, private keys, cookies, or connection strings.
- No customer data, personal data, raw request or response bodies, or
  production payloads.
- Cite evidence by repo-relative ref (`path:line`, record id, check id), not by
  pasting the content.

If a secret reaches the store, delete that key immediately
(`basemind memory delete <key>`), record a `gotcha` without the value, and
report the incident to the owner. Deletion is remediation, not a substitute for
redacting before write.

## Memory never substitutes for live evidence

This is the load-bearing rule:

- A remembered finding is a **pointer**. Re-run its reproducer or probe before
  citing it as a finding.
- A remembered PASS, coverage count, lane head, or verdict is stale by
  construction. Live repository state and fresh records win.
- Memory never satisfies a required check, never changes a gate predicate, and
  never closes a finding.
- If memory disagrees with live state or a record, follow live state and route
  the disagreement per `sop/orchestrator/90-escalation.md`.

The one-sentence version: **memory tells you where to look; evidence tells you
what is true.**
