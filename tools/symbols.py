"""Symbol resolution for the ARCH public-surface rule.

The architecture check has to answer one question per added definition: is this
name part of the module's *published* surface, or is it module-internal? A
line-based regex cannot answer it, because Python publishes names three
different ways:

* a leading underscore marks a name internal by convention;
* ``__all__`` replaces the convention with an explicit list;
* a package ``__init__`` may re-export a name on purpose, even a private one,
  and that is a public decision.

The ``python_ast`` backend resolves all three. ``regex`` is the degraded
backend used when the source text is not available (a diff-only invocation).

Adding a tree-sitter backend later
---------------------------------
Do not touch ``arch_check.py``. Provide a module ``tools/symbols_tree_sitter.py``
that exposes::

    def public_symbols(path: str, source: str, rules: dict) -> dict[int, str]:
        ...  # line number -> published name

then set, in ``policies/layers.json``::

    "public_surface_rules": {
      "typescript": {"backend": "tree_sitter_module:public_symbols", ...}
    }

``resolve()`` imports ``<module>:<callable>`` lazily, so the dependency stays
optional and a missing backend fails closed with a clear error instead of
silently classifying every definition as private.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

SYMBOL_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


class SymbolError(RuntimeError):
    """The requested backend could not resolve the module's surface."""


def _constant_strings(node: ast.AST) -> list[str] | None:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    values: list[str] = []
    for item in node.elts:
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            values.append(item.value)
        else:
            return None
    return values


def dunder_all(source: str) -> set[str] | None:
    """Return the module's ``__all__`` names, or None when it declares none."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        targets = []
        value = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AugAssign):
            targets, value = [node.target], node.value
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                names = _constant_strings(value) if value is not None else None
                if names is not None:
                    return set(names)
    return None


def _private(name: str, rules: dict) -> bool:
    prefix = str(rules.get("private_prefix", "_"))
    return bool(prefix) and name.startswith(prefix)


def _positionally_private(node: ast.AST) -> bool:
    """A nested def, a method, or a closure is not module surface."""
    return bool(getattr(node, "col_offset", 0))


def python_ast(path: str, source: str, rules: dict) -> dict[int, str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise SymbolError(f"{path}: cannot parse Python for surface resolution: {exc}") from None
    explicit = dunder_all(source) if rules.get("respect_dunder_all", True) else None
    package_all = set(rules.get("package_all") or ())
    module_level_only = rules.get("module_level_only", True)
    published: dict[int, str] = {}
    for node in tree.body:
        if not isinstance(node, SYMBOL_NODES):
            continue
        if module_level_only and _positionally_private(node):
            continue
        name = node.name
        if explicit is not None:
            if name in explicit or name in package_all:
                published[node.lineno] = name
            continue
        if _private(name, rules) and name not in package_all:
            continue
        published[node.lineno] = name
    return published


def regex(path: str, source: str, rules: dict) -> dict[int, str]:
    """Last-resort backend: module-level defs only, ``__all__`` aware."""
    explicit = dunder_all(source) if rules.get("respect_dunder_all", True) else None
    package_all = set(rules.get("package_all") or ())
    published: dict[int, str] = {}
    pattern = re.compile(r"^(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)")
    for number, line in enumerate(source.splitlines(), start=1):
        match = pattern.match(line)
        if not match:
            continue
        name = match.group(1)
        if explicit is not None:
            if name in explicit or name in package_all:
                published[number] = name
            continue
        if _private(name, rules) and name not in package_all:
            continue
        published[number] = name
    return published


BACKENDS = {
    "python_ast": python_ast,
    "regex": regex,
}


def _load_reference(reference: str):
    if reference in BACKENDS:
        return BACKENDS[reference]
    if ":" not in reference:
        raise SymbolError(
            f"unknown symbol backend {reference!r}; registered: {sorted(BACKENDS)}"
        )
    module_name, _, attribute = reference.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise SymbolError(
            f"symbol backend {reference!r} is not installed: {exc}"
        ) from None
    backend = getattr(module, attribute, None)
    if not callable(backend):
        raise SymbolError(f"symbol backend {reference!r} has no callable {attribute}")
    return backend


def resolve(
    path: str,
    source: str,
    rules: dict,
    *,
    backend: str | None = None,
    package_all=(),
) -> dict[int, str]:
    """Return ``{line: published name}`` for one module."""
    chosen = str(backend or rules.get("backend") or "python_ast")
    effective = dict(rules)
    effective["package_all"] = set(package_all or ())
    return _load_reference(chosen)(path, source, effective)


def package_surface(init_path: Path, rules: dict) -> set[str]:
    """Names a package ``__init__`` deliberately publishes, even private ones."""
    if not init_path.is_file():
        return set()
    try:
        source = init_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    if not rules.get("respect_package_all", True):
        return set()
    names = dunder_all(source)
    return names or set()


def def_names_in_line(line: str) -> str | None:
    match = re.match(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", line)
    return match.group(1) if match else None
