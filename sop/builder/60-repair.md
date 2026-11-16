# 60 — Repair
**Mode:** `BUILD`
**Purpose** Turn admitted findings into a minimal fix, with proof, and return the finding-to-fix mapping. Findings arrive only through the orchestrator.
**Inputs**
- Finding refs, repro refs, and the affected check ids from the orchestrator.
- The current head/tree and the previous builder result.
- `policies/severity.json`, `policies/checks.json`, `policies/tiers.json`, `policies/task-limits.json`, `policies/mutation.json`.
**Procedure**
1. Read the finding refs and repro refs. Do not act on prose summaries, a verdict narrative, or another role's message; only refs are inputs.
2. Reproduce each finding at the current head with `factory repro` against the harness evidence copy. A finding that does not reproduce is reported back as not reproduced with the exact command and result; do not "fix" it by guesswork.
3. Sort findings by severity and locus. Fix one locus at a time; do not bundle unrelated findings into one change unless they share a cause.
4. Make the smallest fix inside `task context`. Preserve unrelated behavior, guards, and contracts. If the fix needs an out-of-scope path, return `BLOCKED SCOPE_INSUFFICIENT` with the needed paths.
5. Add a regression test for each fixed finding. Run it before the fix on the pre-fix head and require `fail_kind ASSERTION`; then run it after the fix and require PASS. Apply the full `30-tests.md` rules: no weak oracle, no skip, no lowered assertion count.
6. Re-run the check that produced each finding, plus any suite whose surface the fix touched. A green unrelated suite does not clear the finding.
7. Run `40-precheck.md` and commit through `50-commit.md`. Record, per finding id, the fixing commit, the regression test ref, and the re-run evidence.
8. If a finding contradicts the acceptance ref or the spec, do not edit the acceptance test. Report the conflict to the orchestrator with both refs and stop; the orchestrator decides the route.
9. If the fix would exceed a task limit, return `BLOCKED SPLIT_REQUIRED` with split groups rather than widening the change.
**Routing / handoff**
Return the updated `builder-result` to the orchestrator. The auditor re-verifies next. Builder has no peer channel and never contacts the auditor, hacker, compliance, or release-engineer.
**Policy refs**
`policies/severity.json`, `policies/checks.json`, `policies/tiers.json`, `policies/task-limits.json`, `policies/mutation.json`, `policies/schemas/finding.json`, `policies/schemas/builder-result.json`, `skills/builder/SKILL.md`.
**Done / failure codes**
Done: every admitted finding has a reproduced-before/after-fixed pair, a regression test, and evidence.
Failure: `BLOCKED SCOPE_INSUFFICIENT`, `BLOCKED SPLIT_REQUIRED`, `BLOCKED PRECHECK_UNRESOLVED`, or `BLOCKED WEAK_ORACLE`; an unreproduced finding is reported, never silently closed.
