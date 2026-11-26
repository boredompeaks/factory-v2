# Refactor-class checks: ARCH public surface and MUTATION scope

A decomposition of one large function is a legal diff shape. Two checks used
to reject it on mechanics rather than on product risk:

- **ARCH** treated every `def` added under `backend/**/views/**` as a new
  public export, so `_`-private helpers and a relocated view failed.
- **MUTATION** mutated the whole changed region in sort order, so a global cap
  starved late hunks, moved code produced survivors the task's added tests
  could not reach, and comment-only hunks were reported as below-minimum.

This document is the contract for the fix. The ticket is
`orchestrator-scratch/tickets/HARNESS-20261117-ARCH-MUTATION.md`.

## ARCH: public surface is a name, not a path

`backend/**/views/**` is still a public *path* (it is where the URL layer
lives), but the check now asks which **names** a module publishes. Resolution
order (`tools/symbols.py`, backend `python_ast`):

1. If the module defines `__all__`, that list is the surface. Nothing else is
   public, including non-underscore names.
2. Otherwise a leading `_` marks the name internal.
3. An underscore name in the package `__init__.py`'s `__all__` is published on
   purpose and stays public.
4. Only module-level definitions count. Methods and closures are not module
   surface.
5. A definition whose name already exists on the removed side of the same diff
   is a **relocation**, not a new export.

A new file whose basename starts with `_` is not public surface either.

Consequences on the real case (`IDEM-20261117-local-001`, head `15c5710`):

```
before: 18 public_surface findings (17 `_`-private helpers + relocated create_order)
after:  0 findings, twice, same head
```

No allowance was added and no product function is named in an exemption.

### Degraded mode

`arch_check.py` imports `symbols.py` when it is present. If a lone
`arch_check.py` is copied without the module, surface resolution falls back to
a line regex that still applies the private-prefix and relocation rules. Only
`__all__` and package re-export resolution require `symbols.py`.

### Adding a tree-sitter backend later

No change to `arch_check.py` is needed:

1. Add `tools/symbols_tree_sitter.py` with
   `def public_symbols(path, source, rules) -> dict[int, str]`.
2. Set `policies/layers.json#/public_surface_rules/<language>/backend` to
   `"symbols_tree_sitter:public_symbols"`.
3. Add the dependency wherever the check runs (the factory image or the
   product CI job), not to this repository's runtime.

`resolve()` imports the reference lazily and raises `SymbolError` when it is
missing, so an uninstalled backend fails closed with exit 2 instead of
silently classifying everything as private.

## MUTATION: mutate the change, allocate the budget, score with covering tests

The bar is unchanged: 0.70 kill ratio at `high`, 0.80 at `max`, and zero
survivors in high/max risk paths. Four mechanics changed.

### What is mutated

- Only added lines in a changed hunk (`changed_lines_only`).
- A statement whose every added line moved verbatim from the removed side is
  **excluded** (`scope.exclude_moved_lines`). Mutating it scores the old test
  suite, not the change. A moved line whose text changed is still mutated.
- Comment, docstring, and blank lines are ineligible, and a hunk made only of
  them is recorded as `comment_only`, never as below-minimum.

On the real case: 41 raw operator sites, 28 relocated, **13 mutants**.

### Budget allocation

`_select_mutants` deals the budget round-robin across mutatable hunks: every
hunk gets `min_mutants_per_hunk` first, then one per round up to
`max_mutants_per_hunk` and the global `max_mutants_total`. The cap can no
longer starve the last hunk by construction. `MUTATION_TRUNCATED` now fails
only when the budget could not cover the per-hunk minimums at all.

### Test selection

- **fast**: the tests the diff added (node ids).
- **wide**: test files for the changed app whose name matches the module stem,
  then `tests.py`, capped by `test_selection.wide_max_files`.

Mutants run against fast first; a survivor is re-run against wide before it is
called a survivor. A refactor's moved branches are therefore judged by the
tests that already covered them, without paying the wide cost on every mutant.
`test_selection.wide: "none"` disables the second phase.

### Evidence

The check writes `artifacts/MUTATION.json` with `total_mutatable`,
`total_generated`, `truncated`, `relocated_sites_excluded`,
`comment_only_hunks`, `starved_hunks`, `test_selection`, per-hunk counts, and
one row per mutant including which selection killed it. Two runs at the same
tree produce byte-identical artifacts.

On the real case:

```
13/13 killed (100%), 0 survivors, 0 invalid, truncated: false
fast: 3 added tests; wide: tests_checkout_auth.py + tests.py (not needed)
```

## Limits

- Relocation detection is verbatim-text based. Moving a subtle bug together
  with its line is not distinguished from an honest move; `SUITE` still runs
  on the moved code. A verbatim duplicate one-liner (for example two identical
  `if not x:` guards) is treated as relocated on both sites, because the diff
  cannot say which occurrence moved.
- A moved line that is reformatted (text changes) is treated as new behaviour
  and mutated. That is the conservative direction.
- The symbol resolver reads Python. JavaScript/TypeScript keeps the
  `export`-syntax rule until a tree-sitter backend is registered.
- Coverage is approximated by name/import relevance, not measured per test.
  The wide phase is a heuristic; it only adds tests, never removes the bar.

## Acceptance transcript

At `15c5710` / tree `5878e7c6`, with no product change:

| Check | Run 1 | Run 2 | Evidence |
|---|---|---|---|
| `ARCH` | PASS, 0 findings | PASS, 0 findings | tool 1.1.0, symbol backend `python_ast` |
| `MUTATION` | PASS, 13/13 (100%) | PASS, 13/13 (100%) | identical `artifacts/MUTATION.json` digest `b64a473e8dcb2fbb` |

Remaining gate work is the ordinary full gate (`factory gate --task-id ...`),
which re-runs `SUITE`, `DEVIATION`, and the rest at the same tree.
