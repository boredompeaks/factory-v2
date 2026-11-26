"""Public-surface resolution for refactor-class diffs.

ARCH used to flag every added ``def`` under ``backend/**/views/**``. A view
module is a public *path*, but a ``_``-prefixed helper it defines is
module-internal, and a function moved within the same file is a relocation,
not a new export. These tests pin the three publishing mechanisms (underscore,
``__all__``, package re-export) and the relocation rule.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import arch_check
import symbols


def layers() -> dict:
    return json.loads((ROOT / "policies" / "layers.json").read_text(encoding="utf-8"))


# --- symbol resolution -----------------------------------------------------

def test_private_prefix_is_internal():
    source = "def _helper():\n    pass\n\ndef view():\n    pass\n"
    published = symbols.resolve("pkg/views/checkout.py", source, {})
    assert list(published.values()) == ["view"]


def test_dunder_all_replaces_the_convention():
    source = '__all__ = ["view"]\n\ndef view():\n    pass\n\ndef other():\n    pass\n'
    assert list(symbols.resolve("x.py", source, {}).values()) == ["view"]


def test_dunder_all_can_publish_a_private_name():
    source = '__all__ = ["_internal_but_published"]\n\ndef _internal_but_published():\n    pass\n'
    assert list(symbols.resolve("x.py", source, {}).values()) == ["_internal_but_published"]


def test_package_reexport_rescues_a_private_name():
    source = "def _pinned():\n    pass\n"
    published = symbols.resolve("x.py", source, {}, package_all={"_pinned"})
    assert list(published.values()) == ["_pinned"]


def test_nested_and_method_defs_are_not_module_surface():
    source = "class C:\n    def method(self):\n        pass\n\ndef outer():\n    def inner():\n        return 1\n    return inner\n"
    assert list(symbols.resolve("x.py", source, {}).values()) == ["C", "outer"]


def test_syntax_error_fails_closed():
    try:
        symbols.resolve("x.py", "def broken(:\n", {})
    except symbols.SymbolError:
        return
    raise AssertionError("a syntax error must not be classified as private surface")


def test_regex_fallback_honours_the_private_rule():
    source = "def _helper():\n    pass\ndef view():\n    pass\n"
    assert list(symbols.resolve("x.py", source, {}, backend="regex").values()) == ["view"]


def test_unregistered_backend_fails_loudly():
    try:
        symbols.resolve("x.py", "def view(): pass\n", {}, backend="tree_sitter")
    except symbols.SymbolError as exc:
        assert "not installed" in str(exc) or "unknown symbol backend" in str(exc)
        return
    raise AssertionError("an unavailable backend must raise, never silently pass")


# --- whole-diff evaluation -------------------------------------------------

def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=f@t", "-c", "user.name=f", "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _eval(repo: Path, base: str, head: str) -> list[dict]:
    diff = subprocess.run(
        ["git", "-C", str(repo), "diff", "-U0", f"{base}..{head}"],
        capture_output=True, text=True, check=True,
    ).stdout
    findings, _ = arch_check.evaluate(diff, layers(), [], str(repo))
    return findings


VIEW = "backend/orders/views/checkout.py"


def test_split_into_private_helpers_and_a_relocated_view_is_clean(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    (repo / "backend/orders/views").mkdir(parents=True)
    (repo / "backend/orders/views/__init__.py").write_text("", encoding="utf-8")
    (repo / VIEW).write_text(
        "def create_order(request):\n"
        "    if request:\n"
        "        return 1\n"
        "    return 0\n",
        encoding="utf-8",
    )
    base = _commit(repo, "base")
    (repo / VIEW).write_text(
        "def _check(request):\n"
        "    return bool(request)\n\n"
        "def create_order(request):\n"
        "    if _check(request):\n"
        "        return 1\n"
        "    return 0\n",
        encoding="utf-8",
    )
    head = _commit(repo, "split the view")
    assert _eval(repo, base, head) == []


def test_a_genuinely_new_public_def_is_still_flagged(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    (repo / "backend/orders/views").mkdir(parents=True)
    (repo / "backend/orders/views/__init__.py").write_text("", encoding="utf-8")
    (repo / VIEW).write_text("def create_order(request):\n    return 0\n", encoding="utf-8")
    base = _commit(repo, "base")
    (repo / VIEW).write_text(
        "def create_order(request):\n    return 0\n\ndef new_public_view(request):\n    return 1\n",
        encoding="utf-8",
    )
    head = _commit(repo, "new view")
    findings = _eval(repo, base, head)
    assert [item["symbol"].rsplit(":", 1)[-1] for item in findings] == ["new_public_view"]


def test_dunder_all_keeps_a_new_def_internal(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    (repo / "backend/orders/views").mkdir(parents=True)
    (repo / "backend/orders/views/__init__.py").write_text("", encoding="utf-8")
    (repo / VIEW).write_text(
        '__all__ = ["create_order"]\n\ndef create_order(request):\n    return 0\n',
        encoding="utf-8",
    )
    base = _commit(repo, "base")
    (repo / VIEW).write_text(
        '__all__ = ["create_order"]\n\ndef create_order(request):\n    return 0\n\n'
        "def internal(request):\n    return 1\n",
        encoding="utf-8",
    )
    head = _commit(repo, "internal helper")
    assert _eval(repo, base, head) == []


def test_a_new_private_file_is_not_public_surface(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    (repo / "backend/orders/views").mkdir(parents=True)
    (repo / "backend/orders/views/__init__.py").write_text("", encoding="utf-8")
    (repo / "backend/orders/views/checkout.py").write_text(
        "def create_order(request):\n    return 0\n", encoding="utf-8"
    )
    base = _commit(repo, "base")
    (repo / "backend/orders/views/_helpers.py").write_text(
        "def _split(value):\n    return value\n", encoding="utf-8"
    )
    head = _commit(repo, "private helper module")
    assert _eval(repo, base, head) == []


def test_new_public_file_is_still_flagged(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    (repo / "backend/orders/views").mkdir(parents=True)
    (repo / "backend/orders/views/__init__.py").write_text("", encoding="utf-8")
    (repo / "backend/orders/views/checkout.py").write_text(
        "def create_order(request):\n    return 0\n", encoding="utf-8"
    )
    base = _commit(repo, "base")
    (repo / "backend/orders/views/returns.py").write_text(
        "def list_returns(request):\n    return 0\n", encoding="utf-8"
    )
    head = _commit(repo, "new public module")
    kinds = {item["kind"] for item in _eval(repo, base, head)}
    assert "public_surface" in kinds
