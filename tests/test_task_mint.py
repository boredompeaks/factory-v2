"""Mint flags and dispatch-glob matching.

Recorded from the 2026-11-18 mint:

* `factory task new` had no `--title`/`--base`, so the title lived only in the
  builder brief and `base_sha` was whatever the worktree HEAD happened to be.
* DR-MONEY-01 / DR-SECURITY-01 could not match the real files: the patterns end
  in `/**`, which required a directory (`webhooks/`), while the product spells
  the same package as a module (`backend/orders/webhooks.py`); a scope written
  from the product root (`orders/views/checkout.py`) also missed the
  `backend/**` rule. No pre-dispatch gate fired.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factory.scope import dispatch_matches, in_scope, path_matches
from factory.task import create_task, validate_dispatch
from factory.util import FactoryError


def rule_ids(kind: str, scopes: list[str]) -> list[str]:
    return [rule["rule_id"] for rule in validate_dispatch(kind, scopes)]


# --- glob semantics --------------------------------------------------------

def test_trailing_package_glob_also_matches_the_module_file():
    assert path_matches("backend/orders/webhooks.py", "backend/**/webhooks/**")
    assert path_matches("backend/orders/views.py", "backend/**/views/**")
    assert path_matches("backend/orders/views/checkout.py", "backend/**/views/**")


def test_package_glob_does_not_match_an_unrelated_module():
    assert not path_matches("backend/orders/signals.py", "backend/**/webhooks/**")
    assert not path_matches("backend/cart/models.py", "backend/**/views/**")


def test_product_root_scope_matches_a_repo_relative_rule():
    assert dispatch_matches("orders/views/checkout.py", "backend/**/views/**")
    assert dispatch_matches("backend/orders/views/checkout.py", "**/views/**")
    assert dispatch_matches("**/webhooks/**", "backend/**/webhooks/**")
    assert not dispatch_matches("cart/models.py", "backend/**/views/**")


def test_scope_containment_still_rejects_traversal():
    assert in_scope("backend/orders/views/checkout.py", ["backend/**/views/**"])
    assert not in_scope("../backend/orders/views/checkout.py", ["backend/**/views/**"])
    assert not in_scope("/etc/passwd", ["backend/**"])


# --- the two recorded tasks ------------------------------------------------

def test_webhook_file_fires_money_and_security_dispatch():
    matched = rule_ids("SECURITY", ["backend/orders/webhooks.py"])
    assert "DR-SECURITY-01" in matched
    assert "DR-MONEY-01" in matched


def test_checkout_view_fires_money_and_security_dispatch():
    matched = rule_ids(
        "SECURITY",
        ["orders/views/checkout.py", "orders/inventory.py", "orders/tests.py"],
    )
    assert "DR-MONEY-01" in matched
    assert "DR-SECURITY-01" in matched


def test_wildcard_scope_fires_the_matching_rule():
    assert "DR-SECURITY-01" in rule_ids("SECURITY", ["**/views/**"])
    assert "DR-SECURITY-01" in rule_ids("SECURITY", ["backend/**/views/**"])


def test_dispatch_does_not_over_fire():
    assert rule_ids("FIX", ["backend/cart/models.py"]) == []
    # The money rule deliberately covers the whole orders application,
    # including its tests: an orders test change is part of a money change.
    assert rule_ids("FIX", ["backend/orders/tests.py"]) == ["DR-MONEY-01"]
    assert "DR-SECURITY-01" not in rule_ids("FIX", ["backend/orders/views/checkout.py"])


# --- task mint -------------------------------------------------------------

def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    (path / "backend").mkdir()
    (path / "backend" / "orders.py").write_text("x = 1\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "-c", "user.email=f@t", "-c", "user.name=f", "commit", "-q", "-m", "base")
    (path / "backend" / "orders.py").write_text("x = 2\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "-c", "user.email=f@t", "-c", "user.name=f", "commit", "-q", "-m", "tip")
    return path


def test_task_new_records_title_and_an_explicit_base(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "factory-home"))
    first = _git(repo, "rev-parse", "HEAD~1")
    result = create_task(
        kind="FIX",
        lane="lane/b",
        scopes=["backend/orders.py"],
        worktree=str(repo),
        title="Bind the keyed checkout replay to its key",
        base=first,
    )
    task = result["task"]
    assert task["title"] == "Bind the keyed checkout replay to its key"
    assert task["base_sha"] == first
    # The explicit base survives the round-trip through task.json.
    stored = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert stored["base_sha"] == first
    assert stored["title"] == task["title"]


def test_task_new_defaults_to_worktree_head(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "factory-home"))
    head = _git(repo, "rev-parse", "HEAD")
    task = create_task(
        kind="FIX", lane="lane/b", scopes=["backend/orders.py"], worktree=str(repo)
    )["task"]
    assert task["base_sha"] == head
    assert "title" not in task


def test_task_new_refuses_an_unresolvable_base(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "factory-home"))
    with pytest.raises(FactoryError):
        create_task(
            kind="FIX",
            lane="lane/b",
            scopes=["backend/orders.py"],
            worktree=str(repo),
            base="not-a-real-ref",
        )


def test_task_new_refuses_an_empty_title(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "factory-home"))
    with pytest.raises(FactoryError):
        create_task(
            kind="FIX",
            lane="lane/b",
            scopes=["backend/orders.py"],
            worktree=str(repo),
            title="   ",
        )


def test_schema_allows_title_but_still_requires_the_core_fields():
    schema = json.loads((ROOT / "schemas" / "task.json").read_text(encoding="utf-8"))
    assert "title" in schema["properties"]
    assert "title" not in schema["required"]
    assert "base_sha" in schema["required"]
