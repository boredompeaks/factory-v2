# Gate hierarchy: invariants, hygiene, debt, exceptions

`policies/gate-hierarchy.json` gives every check a level. The levels are
ordered, and the order is the enforcement rule: a lower level can never
override a higher one.

| order | level | deviable | what it means | checks |
|---|---|---|---|---|
| 0 | `invariant` | no | mechanical facts that must hold | SUITE, TEST_SET, SECRETS, POLICY_DIGEST, GATE_CONTROLS, ARCH, SEMGREP, AUTHZ_MATRIX, ORACLE_PROVENANCE, GUARD_DELTA, CHANGELOG, MUTATION, BASE_FAIL_HEAD_PASS, BOOTTEST, THREAD_RACE, SCHEMA_FUZZ, DAST_BASELINE, ZIZMOR, CONVENTION, AGT_PARITY, AGT_AUDIT, DEVIATION (SUITE_PYTEST is listed here purely for partition; its shadow flag keeps it diagnostic) |
| 1 | `hygiene` | no | the diff may not make state worse | RADON, BANDIT, SMELL |
| 2 | `debt` | declared | pre-existing state the diff touches must be owned | RADON, BANDIT |
| 3 | `exception` | audited | work a gate forces but the task scope does not cover | SCOPE, TEST_INFRA |

## Why hygiene is a delta

Before this change RADON and BANDIT judged the whole changed file at head. A
file could fail for a function the diff never touched, and the only way to
green it was to refactor that function — a large, unrequested change in a
money path. The live example was `IDEM-20261117-local-001`:

```
base bd35016  create_order  rank F  complexity 51  lines 379-919
head df1ab31  create_order  rank F  complexity 55  lines 379-940
```

The finding existed before the task started, so an absolute check told the
author nothing about the change and demanded a 500-line refactor to satisfy a
one-branch feature.

Both checks now run on the exact blobs at `base` and `head` and classify every
block:

| class | meaning | gate effect |
|---|---|---|
| `new` | a block that did not exist at base is at rank C or worse | **FAIL** — you created a hot spot |
| `crossed` | a block went from rank B to rank C or worse | **FAIL** — you pushed it over |
| `worsened` | a pre-existing rank-C+ block got more complex | debt obligation |
| `residual` | a pre-existing rank-C+ block is unchanged | nothing, unless the diff touches it |
| `improved` | a pre-existing block got simpler | nothing; decline the deviation if one was filed |

A move with the exact same content is paired with its base blob through git's
`R100` rename detection, so moving a complex file is not a regression.

ARCH and MUTATION have their own refactor-class rules — surface resolved by
name rather than by path, mutants only on changed non-relocated lines, budget
dealt round-robin, survivors re-run against covering tests. See
`docs/REFACTOR-CLASS-CHECKS.md`.

## Debt: silence is not an option

If the diff's hunks overlap a rank-C+ block that already existed, the author
must choose one of two things:

1. **Improve it** below the ceiling (then nothing is owed), or
2. **Own it** with an `accepted_debt` declaration: `owner`, one-sentence
   `reason`, ISO `expires` (≤ 30 days), and a `follow_up` naming where it is
   paid down.

`DEVIATION` fails the gate for undeclared debt. That is the difference between
"the gate is red and nobody knows why" and "the team decided, on this date,
with this owner, to carry this complexity".

An unrelated edit in a file that contains a rank-F function owes nothing: the
obligation is only created when the hunks overlap the block. Touching the
function is what makes it yours.

List the portfolio at any time:

```
bin/factory debt                        # open debt across all task evidence
bin/factory debt --include-expired      # also show debt that has lapsed
bin/factory deviation --task-id <TASK>  # this task's obligations and ledger
```

## Exceptions: a refactor must be proven forced

When satisfying a gate requires paths outside the task scope (a new helper
module, a moved test file), the author declares `forced_refactor`:

```json
{
  "id": "D-002",
  "kind": "forced_refactor",
  "check": "SCOPE",
  "paths": ["backend/orders/checkout_helpers.py"],
  "forcing": {
    "check": "RADON",
    "path": "backend/orders/views/checkout.py",
    "block": {"type": "function", "name": "create_order", "col_offset": 0}
  },
  "owner": "builder",
  "reason": "Radon refuses the new branch inside create_order, so the logic moves to a helper module.",
  "expires": "2026-12-10",
  "follow_up": "IDEM-REFACTOR-002"
}
```

`DEVIATION` then checks the claim instead of trusting it:

- the forcing block must **exist at base** and already be rank C+ — a refactor
  cannot be "forced" by a finding the diff introduced;
- every covered path must be in the diff, and at least one must be outside
  `task.scope`;
- `tools/**`, `policies/**`, and `.github/workflows/**` can never be excused;
- an auditor verdict (`deviation-verdict.json`) is required with
  `verdict: NECESSARY`, authored by `auditor`, run at the audited head, and a
  falsifier digest that equals the recomputed base state.

The auditor's falsifier is the counterfactual: revert the extraction, or write
the smallest in-scope version, and see whether the forcing check still fails.
If an in-scope version passes, the verdict is `AVOIDABLE` and the gate blocks.
`factory deviation --task-id <TASK>` prints the `necessity` digest the verdict
must carry, so a fabricated or stale verdict is detectable.

If the auditor's verdict is missing at BUILD time, the builder does not file
it: the builder's precheck excludes `DEVIATION` and the obligation surfaces to
the auditor at GATE. See `sop/builder/40-precheck.md` and
`sop/auditor/20-checks.md`.

## How the gate evaluates the hierarchy

1. Every check in the kind's gate set runs. Declarations never turn a check
   result into PASS.
2. A failing invariant blocks. Full stop.
3. A failing hygiene check means the diff regressed state; no declaration
   applies to `new` or `crossed`.
4. `DEVIATION` then enforces the lower levels: every debt obligation is either
   improved or declared, every out-of-scope path is covered by a validated
   exception, and every declaration matches an obligation that actually
   exists. A spare declaration is an error, so blanket pre-approval is
   impossible.
5. Expiry is a payment date. An expired declaration fails the gate.
6. The gate record annotates every outcome with its level and lists them in
   hierarchy order, so a reviewer reads invariants first and exceptions last.

## What this does not do

- It does not prove which persona wrote the verdict. The digest pins the base
  state; the reason is still prose.
- It does not schedule the cleanup. The `follow_up` field names it; a task
  kind that owns refactors still has to exist.
- It does not make `SCOPE` meaningless. An exception covers exactly the paths
  it lists, and only when a base-state finding forces them.

## Operational note

Adding `policies/gate-hierarchy.json`, `DEVIATION`, and the new RADON/BANDIT
predicates changes the policy digest. Gate records created before this change
fail `factory verify` with `policy_hash_matches=false`; re-run the gate at the
current head for any task whose record must be live.
