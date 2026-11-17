# 30 — Tests and proof
**Mode:** `BUILD`
**Purpose** Prove the change with tests that fail for the right reason on base and pass on head, and that survive mutation and oracle checks.
**Inputs**
- The edit set from `20-implement.md`.
- Acceptance refs and their pre-change hashes.
- `policies/tiers.json`, `policies/checks.json`, `policies/mutation.json`, `policies/test-removal-reasons.json`, `policies/severity.json`.
**Memory** Read `basemind_memory` (`list`/`search` filtered by `role:builder`) at orientation; put one record at task end and whenever a seam, gotcha, or decision is found. Memory is advice, never evidence; re-run a remembered finding before citing it (`sop/00-memory.md`).

**Procedure**
1. **Acceptance and exploit tests are read-only.** Acceptance refs and any returned probe/test authored by the auditor or hacker are inputs. Never edit, rename, move, skip, or xfail them. Re-hash acceptance at the task context head after the change; a changed hash is `ACCEPTANCE_NOT_RED` and blocks the build.
2. **Separate test classes.** Exploit/repro tests are authored upstream and read-only; unit tests are yours. Add unit tests under the product test tree (`backend/**/tests/**`, `tests/**`) when `task context` names them, in a new module or in the builder-owned test file for the same surface; do not copy or relocate an exploit test into them, and you may run a returned exploit/probe but never strengthen, weaken, skip, or xfail it. Test infrastructure is `pytest.ini`, `tox.ini`, `setup.cfg`, `conftest.py`, `.coveragerc`, `Makefile`, `scripts/test*`, `.github/workflows/**`, and any fixture module imported by a test you did not author; such a path is writable only when `task context` names it, builder never seeks that grant for test configuration, shared fixtures, or CI, and `.github/**` belongs to the release-engineer. Keep a new fixture inside the module you add, and return `BLOCKED SCOPE_INSUFFICIENT` for an unavoidable shared-fixture or config change.
3. **Run every added test on base.** Use `factory check BASE_FAIL_HEAD_PASS` (or `factory repro` against the harness evidence copy) for each test this change adds, not only the acceptance refs. Each must fail with `fail_kind ASSERTION`.
4. **Base-green is a block.** A test that passes on base is not proof. Return `BLOCKED ADDED_TEST_NOT_RED` unless the task context's `characterization_tests` names it with a non-empty reason. A characterization test is recorded but never counts as proof of the fix.
5. **Cover the change.** Add supporting tests for each new branch, guard, lock, or state transition, so `MUTATION` kills at least one mutant in every changed hunk. On a high-tier path, zero survivors is the bar; do not average a survivor away.
6. **Check the oracle.** Run `ORACLE_PROVENANCE` on added assertion lines. Replace every `SELF_COMPARE`, `ORACLE_BY_COPY`, or `MOCK_ECHO` assertion with one that reads behavior, not implementation. An unreplaced weak oracle is `BLOCKED WEAK_ORACLE`.
7. **Check the guard delta.** For every removed conditional or guard in a high-tier path, name the added test whose coverage includes the changed hunk in `guard_delta`. An unnamed removal is `BLOCKED GUARD_UNCOVERED`.
8. **Protect the test set.** Do not lower an assertion count, add a skip or xfail, or remove a test id without an entry in `allowed_test_removals` carrying a `reason_code` and `covered_by`. Removed tests are reported in the handoff.
9. **Run the touched suites.** Targeted suite first, postgres where the check requires it; parse `Ran N tests` plus `OK|FAILED`, never the shell exit alone. Name unrelated pre-existing failures verbatim; do not chase them.
10. Hand the added-test table, outcomes, mutation evidence, and oracle/guard results to `40-precheck.md`.
**Routing / handoff**
Continue to `40-precheck.md`. Return only to the orchestrator. Builder does not dispatch, message, or read another role's findings; a finding reaches the builder only through the orchestrator and `60-repair.md`.
**Policy refs**
`policies/tiers.json`, `policies/checks.json`, `policies/mutation.json`, `policies/test-removal-reasons.json`, `policies/severity.json`, `policies/schemas/builder-result.json`, `skills/builder/SKILL.md`.
**Done / failure codes**
Done: every added test red on base (`ASSERTION`), green on head, mutation/oracle/guard evidence recorded.
Failure: `BLOCKED ADDED_TEST_NOT_RED`, `BLOCKED ACCEPTANCE_NOT_RED`, `BLOCKED WEAK_ORACLE`, `BLOCKED GUARD_UNCOVERED`.
