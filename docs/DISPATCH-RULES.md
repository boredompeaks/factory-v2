# Task mint and dispatch-rule matching

Two harness facts from the 2026-11-18 mint, both fixed here.

## `factory task new` flags

```
factory task new --kind SECURITY --lane lane/b \
  --scope backend/orders/webhooks.py \
  --title "AUD-002 webhook envelope and currency" \
  --base 34d491f --worktree <lane> --json
```

- `--title` is optional, trimmed, must be non-empty when given, and is stored
  as `title` in `task.json` (schema: optional, max 200 chars). The builder
  brief still carries the detail; the title no longer exists only there.
- `--base` accepts any commit or ref resolvable **in the task worktree** and
  stores the full 40-hex `base_sha`. Without it, the mint keeps the old
  behaviour (worktree `HEAD`). An unresolvable base refuses the mint instead of
  silently recording the wrong parent.

## Dispatch matching

`factory/task.py::validate_dispatch` used `path_matches(scope, rule_glob)`,
which treats the task scope as a concrete path. Three forms are now tried by
`factory/scope.py::dispatch_matches`:

1. the scope as written;
2. the same path from the product root (`orders/views/checkout.py` also means
   `backend/orders/views/checkout.py`, and vice versa);
3. concrete samples of a wildcard scope (`**/views/**` samples `views/x.py`).

A rule glob ending in `/**` now also matches the **module file** of that name:
`backend/**/webhooks/**` covers `backend/orders/webhooks.py`, and
`backend/**/views/**` covers `backend/orders/views.py` as well as
`backend/orders/views/checkout.py`. That is how the product spells those
packages, and it was the recorded miss.

Dispatch matching is deliberately generous: a false positive costs one review,
a false negative skips a gate. `SCOPE` containment (`in_scope`) also gains the
module-file form but keeps the traversal and absolute-path refusals.

## Policy

The rules now spell the file-level forms the product actually uses:

```
DR-SECURITY-01: backend/**/webhooks*.py, backend/**/views*.py, backend/**/views/*.py
DR-MONEY-01:    backend/**/orders/** (plus the precise checkout/payment/refund views)
```

Every `backend/**/views/**` task is not money; the orders application is, and
`backend/**/orders/**` covers `orders/views/checkout.py` without pulling every
backend view into a money dispatch. `DR-SECURITY-01` already covered
`backend/**/views/**`; the file-level forms are kept next to the directory
forms so a tool that does not share the module-file rule still matches.

## Recorded cases

| Mint scope | Kind | Rules before | Rules after |
|---|---|---|---|
| `backend/orders/webhooks.py` | SECURITY | `[]` | `DR-SECURITY-01`, `DR-MONEY-01` |
| `orders/views/checkout.py` (+ inventory/tests) | SECURITY | `[]` | `DR-SECURITY-01`, `DR-MONEY-01` |
| `**/views/**` | SECURITY | `[]` | `DR-SECURITY-01` |
| `backend/cart/models.py` | FIX | `[]` | `[]` (correct) |

## Limits

- `dispatch_rules.keywords` remains informational; matching is scope-only.
  A task title does not raise the tier by itself.
- Matching a rule is not the same as dispatching compliance: only `DR-SPEC-01`
  and `DR-DRAIN-01` set `dispatch_first: [compliance]`. A plain security or
  money fix still goes straight to builder — that part was in policy, and it
  is unchanged. What was broken was the match, and an empty `dispatch_rules`
  list is now evidence of a harness bug rather than a policy pass.
- The matcher is a conservative approximation, not a glob-intersection proof.
