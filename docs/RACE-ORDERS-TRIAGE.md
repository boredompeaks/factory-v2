# RACE-ORDERS triage: 9 counted vs min 10

## Observation

A `max`-tier RACE-ORDERS campaign returned **9 counted probes** against
`min_probes.max = 10`. Sixteen distinct executions ran; seven of them
(R03/R05/R07/R09/R10/R11/R16) were concurrent-writer probes executed against a
SQLite runtime and ended in `OperationalError` debug 500s. The nine counted
executions all held their invariants (single capture, stock >= 0, monotone
ledgers). The shortfall is real; it was not fabricated into coverage.

## Root cause

This was an **engine fault, not an application gate**:

- SQLite allows one writer. A probe that opens N>1 writers raises
  `OperationalError: database is locked`, which the app reports as a 500.
  The four RACE-ORDERS classes (`double_spend`, `stock_oversell`,
  `concurrent_cancel`, `watermark_regression`) are all concurrent-writer
  classes.
- The catalog advertised `engines: ["sqlite","postgres"]` for RACE-ORDERS
  without per-class eligibility, so the campaign was free to spend its budget
  on the ineligible engine.
- An `error` outcome was already excluded from the count, which is why the
  result was 9 rather than 16. The remaining gap was that nothing forced the
  engine retry.

## What changed

1. `policies/attack-catalog.json#/engine_policy` declares the four
   concurrent-writer classes PostgreSQL-only, states that an `error` never
   counts, and requires the ineligible class to be re-run on its required
   engine in the same campaign.
2. Each item now carries `engine_eligibility` per probe class. Static items
   (`PROMPT-INJECTION`, `SECRET-EXPOSURE`) declare `["none"]`.
3. `tools/probe_audit.py` / `bin/factory probe audit` computes coverage:
   a counted probe is a distinct `(item, class, endpoint, tenant_pair, engine)`
   execution with `blocked` or `reproduced`, an eligible engine, and a class
   the item declares. Engine faults, errors, and off-catalog classes never
   count. It emits `next_probe_plan` naming the required engine.
4. `policies/concurrency.json#/probe` is engine-aware: SQLite cap 1, K1
   PostgreSQL up to the catalog depth (16), host PostgreSQL 8. The catalog
   depth is the upper bound.
5. The harness now gives every DB-backed run a per-run database name and a
   per-server slot lease, so re-running the seven probes on PostgreSQL does not
   collide with another gate or suite.

## Next cycle

Run the missing classes against a PostgreSQL-backed runtime (K1's internal pg,
or the lane's assigned pg2a/pg2b) rather than the SQLite dev database. Ten
counted executions with at least one in every class satisfies `max`; the
campaign can do that in one invocation because it no longer wastes attempts on
SQLite.

Verify with:

```bash
bin/factory probe audit --records evidence/probe --variant max --item RACE-ORDERS --json
```

Until the audit reports `complete: true`, the campaign result is `PARTIAL`
with the `next_probe_plan` attached. That is the correct outcome; it is never
upgraded to `COMPLETE` by hand.
