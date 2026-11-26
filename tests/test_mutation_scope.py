"""Mutation scope, allocation, and test selection.

Two harness defects made every refactor diff fail: the global cap was applied
in sort order (so late hunks got 0 mutants while the policy demanded 1), and
the test selection was the task's added tests only (so relocated branches that
pre-existing tests already covered read as survivors). These tests pin the
rules that fix both without weakening the bar.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factory.checks import mutation


def policy() -> dict:
    return json.loads((ROOT / "policies" / "mutation.json").read_text(encoding="utf-8"))


# --- diff indexing ---------------------------------------------------------

DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -10,3 +10,4 @@
-    if a > b:
+    if a >= b:
+    if c:
         return 1
     return 0
"""


def test_parse_hunks_keeps_added_and_removed_text():
    line_hunks, hunk_counts, added, removed = mutation._parse_hunks(DIFF)
    assert hunk_counts["app.py"] == 1
    assert added["app.py"][10] == "    if a >= b:"
    assert "if a > b:" in removed["app.py"]
    assert line_hunks["app.py"][10] == 1


def test_relocated_statement_is_not_new_behaviour():
    added = {10: "    value = compute(a, b)", 11: "    return value"}
    removed = {"value = compute(a, b)", "return value"}
    tree = ast.parse("def f(a, b):\n    value = compute(a, b)\n    return value\n")
    node = tree.body[0].body[0]
    assert mutation._is_relocated(node, added, removed) is False  # spans the def line too


def test_relocated_line_with_changed_text_is_new_behaviour():
    added = {10: "    value = compute(a, b + 1)"}
    removed = {"value = compute(a, b)"}
    tree = ast.parse("\n" * 9 + "value = compute(a, b + 1)\n")
    node = tree.body[0]
    assert mutation._is_relocated(node, added, removed) is False


def test_verbatim_relocation_is_excluded():
    added = {10: "value = compute(a, b)"}
    removed = {"value = compute(a, b)"}
    tree = ast.parse("\n" * 9 + "value = compute(a, b)\n")
    node = tree.body[0]
    assert mutation._is_relocated(node, added, removed) is True


# --- hunk eligibility ------------------------------------------------------

def test_comment_and_docstring_only_hunks_are_ineligible():
    source = (
        '"""module docstring\n'
        'second line\n'
        '"""\n'
        "def f():\n"
        "    # a comment\n"
        "    return 1\n"
    )
    tree = ast.parse(source)
    line_hunks = {1: 1, 2: 1, 3: 1, 5: 2, 6: 3}
    ignorable = mutation._ignorable_lines(source, tree)
    comment_only = mutation._comment_only_hunks("app.py", line_hunks, ignorable)
    assert comment_only == {("app.py", 1), ("app.py", 2)}
    assert ("app.py", 3) not in comment_only


# --- allocation ------------------------------------------------------------

def _site(path: str, hunk: int, line: int, kind: str = "ROR") -> dict:
    return {
        "path": path,
        "hunk": hunk,
        "kind": kind,
        "operator_index": 0,
        "column": 0,
        "node": ast.parse("\n" * (line - 1) + "a < b\n").body[0].value,
    }


def test_round_robin_gives_every_hunk_its_minimum_before_a_second_mutant():
    candidates = [_site("a.py", 1, 1), _site("a.py", 2, 2), _site("a.py", 3, 3), _site("a.py", 4, 4)]
    selected, counts = mutation._select_mutants(candidates, 4, 20, 1)
    assert len(selected) == 4
    assert counts == {("a.py", 1): 1, ("a.py", 2): 1, ("a.py", 3): 1, ("a.py", 4): 1}


def test_a_small_cap_does_not_starve_the_last_hunk_without_saying_so():
    candidates = [_site("a.py", 1, 1), _site("a.py", 1, 2), _site("a.py", 1, 3),
                  _site("a.py", 2, 4), _site("a.py", 3, 5)]
    selected, totals = mutation._select_mutants(candidates, 3, 20, 1)
    # Three hunks, budget three: each gets exactly one, the busy hunk does not
    # consume the whole budget first.
    assert [item["hunk"] for item in selected] == [1, 2, 3]
    assert totals == {("a.py", 1): 3, ("a.py", 2): 1, ("a.py", 3): 1}


def test_allocation_is_deterministic():
    candidates = [_site("a.py", 1, 1), _site("a.py", 1, 2), _site("a.py", 2, 3)]
    first = mutation._select_mutants(list(candidates), 2, 20, 1)
    second = mutation._select_mutants(list(reversed(candidates)), 2, 20, 1)
    assert [item["hunk"] for item in first[0]] == [item["hunk"] for item in second[0]]


# --- covering test selection ----------------------------------------------

def test_covering_targets_prefer_the_module_stem_and_skip_added_tests(tmp_path):
    repo = tmp_path / "repo"
    app = repo / "backend" / "orders"
    app.mkdir(parents=True)
    for name in ("test_idempotency.py", "tests.py", "tests_checkout_auth.py",
                 "tests_guest.py", "models.py"):
        (app / name).write_text("", encoding="utf-8")
    targets = mutation._covering_targets(
        repo,
        ["backend/orders/views/checkout.py"],
        ["backend/orders/test_idempotency.py::T::test_one"],
        {"wide": "covering_module", "wide_max_files": 4},
    )
    assert "backend/orders/test_idempotency.py" not in targets
    assert targets[0] == "backend/orders/tests_checkout_auth.py"
    assert "backend/orders/tests.py" in targets
    assert all(name.startswith("backend/orders/") for name in targets)


def test_covering_targets_can_be_disabled(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend/orders").mkdir(parents=True)
    (repo / "backend/orders/tests.py").write_text("", encoding="utf-8")
    assert mutation._covering_targets(
        repo, ["backend/orders/views/checkout.py"], [], {"wide": "none"}
    ) == []


# --- policy ----------------------------------------------------------------

def test_policy_requires_move_exclusion_and_a_round_robin_budget():
    cfg = policy()
    assert cfg["scope"]["exclude_moved_lines"] is True
    assert cfg["scope"]["changed_lines_only"] is True
    assert cfg["max_mutants_total"] >= cfg["max_mutants_per_hunk"]
    assert cfg["test_selection"]["fast"] == "added_tests"
    assert cfg["test_selection"]["wide"] == "covering_module"
    assert "starve" in cfg["cap_enforcement"]["rule"]
    assert cfg["timeout_s"] >= 300


def test_threshold_bar_is_unchanged():
    cfg = policy()
    assert cfg["thresholds"]["high"]["kill_ratio"] == 0.7
    assert cfg["thresholds"]["high"]["max_survivors_in_high_path"] == 0
    assert cfg["thresholds"]["max"]["kill_ratio"] == 0.8
