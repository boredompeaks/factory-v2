# CHANGELOG lineage on a re-mint

`CHANGELOG` keys an added row to the task identity of the commit range, not to
the live task context id. The order is:

1. If `git log --no-merges <base>..<head>` contains a `Task-Id: <id>` trailer
   (or `Task: <id>`), the row's `task_id` must be one of those ids.
2. Otherwise the row must equal `ctx.task_id`, preserving the old behaviour for
   every task that never re-mints.

A re-mint therefore keeps the historical id in the changelog row and adds the
same id as a trailer on the new commit:

```
fix(orders): re-apply the capture guard on the live head

Task-Id: T-20261119-003
```

The live task context may be `T-20261119-007`; the row and the trailer still say
`T-20261119-003`, so no extra commit that rewrites only the row id is needed.
Rewriting the row would move the head after its own evidence was recorded, which
is the loop this replaces.

The proof's `stdout` payload records `allowed_task_ids` and `task_id_source`
(`commit-trailer` or `task-context`) so the decision is auditable.
