"""Bounded AOR/ROR/LCR/NEG/CR AST mutation runner."""

from __future__ import annotations

import ast
import shutil
import tempfile
import time
from pathlib import Path

from ..util import FactoryError, evidence_dir
from . import (
    canonical_json,
    changed_paths,
    get_diff,
    make_proof,
    policy_path,
    read_json,
    repo_path,
    run_process,
    sha256,
    worktree_path,
)
from .tests import _pytest_command, _pytest_version, _test_env, _test_nodeids

_AOR = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
}
_ROR = {
    ast.Lt: ast.LtE,
    ast.Gt: ast.GtE,
    ast.Eq: ast.NotEq,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}
_LCR = {ast.And: ast.Or, ast.Or: ast.And}
_IMPLEMENTED_OPERATORS = frozenset({"AOR", "ROR", "LCR", "NEG", "CR"})


def _parse_hunks(diff: str) -> tuple[dict, dict, dict, dict]:
    """Index the diff by file.

    Returns ``(line_to_hunk, hunk_counts, added_text, removed_text)``. The
    removed text is what lets the mutator tell a *relocation* from new
    behaviour: a moved statement keeps its normalized text on both sides.
    """
    line_to_hunk: dict[str, dict[int, int]] = {}
    hunk_counts: dict[str, int] = {}
    added_text: dict[str, dict[int, str]] = {}
    removed_text: dict[str, set[str]] = {}
    current = None
    hunk_id = 0
    new_line = 0
    in_hunk = False
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            current = raw[4:].split("\t", 1)[0].strip()
            current = current.removeprefix("b/")
            if current == "/dev/null":
                current = None
            if current:
                current = current.replace("\\", "/")
                line_to_hunk.setdefault(current, {})
                hunk_counts.setdefault(current, 0)
                added_text.setdefault(current, {})
                removed_text.setdefault(current, set())
            in_hunk = False
            continue
        match = __import__("re").match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", raw)
        if match and current:
            hunk_counts[current] += 1
            hunk_id = hunk_counts[current]
            new_line = int(match.group(1))
            in_hunk = True
            continue
        if not in_hunk or current is None or raw.startswith("\\"):
            continue
        marker = raw[:1] if raw else " "
        if marker == "+":
            line_to_hunk[current][new_line] = hunk_id
            added_text[current][new_line] = raw[1:]
            new_line += 1
        elif marker == "-":
            removed_text[current].add(raw[1:].strip())
        elif marker == " ":
            new_line += 1
    return line_to_hunk, hunk_counts, added_text, removed_text


def _node_span(node: ast.AST) -> tuple[int, int]:
    start = int(getattr(node, "lineno", 0) or 0)
    return start, int(getattr(node, "end_lineno", start) or start)


def _is_relocated(node: ast.AST, added: dict[int, str], removed: set[str]) -> bool:
    """True when every line of the node moved here verbatim from elsewhere."""
    start, end = _node_span(node)
    texts = []
    for line in range(start, end + 1):
        text = added.get(line)
        if text is None:
            return False
        stripped = text.strip()
        if stripped:
            texts.append(stripped)
    return bool(texts) and all(text in removed for text in texts)


def _docstring_lines(tree: ast.AST) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if not isinstance(first, ast.Expr):
            continue
        value = first.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            start, end = _node_span(first)
            lines.update(range(start, end + 1))
    return lines


def _ignorable_lines(source: str, tree: ast.AST) -> set[int]:
    """Blank, comment, and docstring lines: nothing a mutant could change."""
    import io
    import tokenize

    ignorable = _docstring_lines(tree)
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT:
                ignorable.add(token.start[0])
    except (tokenize.TokenError, IndentationError):
        pass
    for number, line in enumerate(source.splitlines(), start=1):
        if not line.strip():
            ignorable.add(number)
    return ignorable


def _comment_only_hunks(path: str, line_hunks: dict[int, int], ignorable: set[int]) -> set[tuple[str, int]]:
    by_hunk: dict[int, list[int]] = {}
    for line, hunk in line_hunks.items():
        by_hunk.setdefault(hunk, []).append(line)
    return {
        (path, hunk)
        for hunk, lines in by_hunk.items()
        if lines and all(line in ignorable for line in lines)
    }


def _select_mutants(
    candidates: list[dict],
    max_mutants: int,
    per_hunk_cap: int,
    min_per_hunk: int,
) -> tuple[list[dict], dict[tuple[str, int], int]]:
    """Allocate the budget round-robin so the cap cannot starve a hunk.

    Every mutatable hunk gets its minimum first; the remaining budget is dealt
    one mutant per hunk per round. Selection is deterministic.
    """
    ordered = sorted(
        candidates,
        key=lambda item: (
            item["path"],
            int(item["hunk"]),
            getattr(item["node"], "lineno", 0),
            item["kind"],
            item["operator_index"],
        ),
    )
    by_hunk: dict[tuple[str, int], list[dict]] = {}
    for item in ordered:
        by_hunk.setdefault((item["path"], int(item["hunk"])), []).append(item)
    cursor = {key: 0 for key in by_hunk}
    selected: list[dict] = []
    for key in sorted(by_hunk):
        take = min(min_per_hunk, per_hunk_cap, len(by_hunk[key]))
        selected.extend(by_hunk[key][:take])
        cursor[key] = take
    while len(selected) < max_mutants:
        progressed = False
        for key in sorted(by_hunk):
            if len(selected) >= max_mutants:
                break
            limit = min(per_hunk_cap, len(by_hunk[key]))
            if cursor[key] < limit:
                selected.append(by_hunk[key][cursor[key]])
                cursor[key] += 1
                progressed = True
        if not progressed:
            break
    return selected, {key: len(items) for key, items in by_hunk.items()}


def _operator_sites(tree: ast.AST, changed_line_hunks: dict[int, int]) -> list[dict]:
    sites: list[dict] = []
    for node in ast.walk(tree):
        start = getattr(node, "lineno", 0)
        end = getattr(node, "end_lineno", start) or start
        matching = sorted({changed_line_hunks[line] for line in range(start, end + 1) if line in changed_line_hunks})
        if not matching:
            continue
        if isinstance(node, ast.BinOp) and type(node.op) in _AOR:
            sites.append({
                "node": node, "kind": "AOR", "operator_index": 0,
                "replacement": _AOR[type(node.op)](), "hunk": matching[0],
                "column": getattr(node, "col_offset", 0),
            })
        elif isinstance(node, ast.BoolOp) and type(node.op) in _LCR:
            sites.append({
                "node": node, "kind": "LCR", "operator_index": 0,
                "replacement": _LCR[type(node.op)](), "hunk": matching[0],
                "column": getattr(node, "col_offset", 0),
            })
        elif isinstance(node, ast.Compare):
            for index, operator in enumerate(node.ops):
                if type(operator) in _ROR:
                    sites.append({
                        "node": node, "kind": "ROR", "operator_index": index,
                        "replacement": _ROR[type(operator)](), "hunk": matching[0],
                        "column": getattr(node, "col_offset", 0),
                    })
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            # NEG removes the unary `not`, leaving its operand in place.
            sites.append({
                "node": node, "kind": "NEG", "operator_index": 0,
                "replacement": node.operand, "hunk": matching[0],
                "column": getattr(node, "col_offset", 0),
            })
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            # CR swaps boolean constants: True <-> False.
            sites.append({
                "node": node, "kind": "CR", "operator_index": 0,
                "replacement": ast.Constant(value=not node.value), "hunk": matching[0],
                "column": getattr(node, "col_offset", 0),
            })
    sites.sort(
        key=lambda item: (
            getattr(item["node"], "lineno", 0),
            item["kind"],
            item["operator_index"],
        )
    )
    return sites


class _ReplaceOne(ast.NodeTransformer):
    def __init__(self, site: dict):
        self.site = site
        self.changed = False

    def visit_BinOp(self, node):
        if node is self.site["node"] and self.site["kind"] == "AOR":
            node.op = self.site["replacement"]
            self.changed = True
            return node
        return self.generic_visit(node)

    def visit_BoolOp(self, node):
        if node is self.site["node"] and self.site["kind"] == "LCR":
            node.op = self.site["replacement"]
            self.changed = True
            return node
        return self.generic_visit(node)

    def visit_Compare(self, node):
        if node is self.site["node"] and self.site["kind"] == "ROR":
            node.ops[self.site["operator_index"]] = self.site["replacement"]
            self.changed = True
            return node
        return self.generic_visit(node)

    def visit_UnaryOp(self, node):
        if node is self.site["node"] and self.site["kind"] == "NEG":
            self.changed = True
            return node.operand
        return self.generic_visit(node)

    def visit_Constant(self, node):
        if node is self.site["node"] and self.site["kind"] == "CR":
            node.value = not node.value
            self.changed = True
            return node
        return self.generic_visit(node)


def _mutate(source: str, site: dict) -> str | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    # Re-find the target by stable source location/type because AST object
    # identities belong to the first parse.
    wanted_line = getattr(site["node"], "lineno", 0)
    wanted_column = int(site.get("column", getattr(site["node"], "col_offset", 0)))
    wanted_kind = site["kind"]
    wanted_index = site["operator_index"]
    candidate = None
    for node in ast.walk(tree):
        if (
            getattr(node, "lineno", 0) != wanted_line
            or int(getattr(node, "col_offset", 0)) != wanted_column
        ):
            continue
        if wanted_kind == "AOR" and isinstance(node, ast.BinOp):
            if type(node.op) in _AOR:
                candidate = node
                break
        elif wanted_kind == "LCR" and isinstance(node, ast.BoolOp):
            if type(node.op) in _LCR:
                candidate = node
                break
        elif wanted_kind == "ROR" and isinstance(node, ast.Compare):
            if wanted_index < len(node.ops) and type(node.ops[wanted_index]) in _ROR:
                candidate = node
                break
        elif wanted_kind == "NEG" and isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                candidate = node
                break
        elif wanted_kind == "CR" and isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                candidate = node
                break
    if candidate is None:
        return None
    fresh_site = dict(site, node=candidate)
    transformer = _ReplaceOne(fresh_site)
    tree = transformer.visit(tree)
    if not transformer.changed:
        return None
    ast.fix_missing_locations(tree)
    try:
        return ast.unparse(tree) + "\n"
    except (AttributeError, ValueError):
        return None


def _risk_tier(path: str, policy: dict, default: str) -> str:
    matches = [
        (len(pattern), pattern, spec.get("tier", default))
        for pattern, spec in (policy.get("globs") or {}).items()
        if isinstance(spec, dict) and __import__("fnmatch").fnmatchcase(path, pattern)
    ]
    if not matches:
        return str((policy.get("default") or {}).get("tier") or default)
    # Risk policy is max-wins; longest match breaks ties.
    matches.sort(key=lambda item: (item[2] == "max", item[0], item[1]), reverse=True)
    return str(matches[0][2])




def _covering_targets(
    worktree: Path,
    python_paths: list[str],
    added_selectors: list[str],
    selection: dict,
) -> list[str]:
    """Test files that can reach the changed modules, newest-first by name.

    Mutation is scored against the tests that cover the change, not only the
    tests the change added: a refactor moves exercised code, and the suite
    that exercised it before still owns those branches. The wide set only runs
    for mutants the added tests did not kill, so the fast path stays cheap.
    """
    mode = str(selection.get("wide") or "covering_module")
    if mode in {"none", "off"}:
        return []
    cap = int(selection.get("wide_max_files", 4) or 4)
    added_files = {selector.split("::", 1)[0] for selector in added_selectors}
    stems = {Path(path).stem for path in python_paths}
    app_dirs = sorted({
        Path(*Path(path).parts[:2])
        for path in python_paths
        if len(Path(path).parts) >= 3 and Path(path).parts[0] == "backend"
    })
    scored: list[tuple[int, str]] = []
    for app in app_dirs:
        directory = worktree / app
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.glob("*.py")):
            name = candidate.name
            if not name.startswith(("test", "tests")):
                continue
            relative = candidate.relative_to(worktree).as_posix()
            if relative in added_files:
                continue
            if any(stem and stem in candidate.stem for stem in stems):
                score = 0
            elif candidate.stem in {"test", "tests"}:
                score = 1
            else:
                score = 2
            scored.append((score, relative))
    ranked = sorted(set(scored))
    # Prefer tests that name the changed module or are the app's main suite;
    # only fall back to arbitrary app tests when neither exists.
    preferred = [relative for score, relative in ranked if score <= 1]
    ordered = preferred or [relative for _score, relative in ranked]
    if not ordered and mode == "covering_module":
        ordered = [
            app.as_posix()
            for app in app_dirs
            if (worktree / app).is_dir()
        ]
    return ordered[:cap]

def check_mutation(ctx: dict) -> dict:
    paths, diff_proc, error = changed_paths(ctx)
    if error:
        return make_proof(
            "MUTATION", ctx, result="FAIL",
            command=diff_proc["command"] if diff_proc else [],
            tool_name="git", version="unknown",
            exit_code=diff_proc.get("exit") if diff_proc else None,
            stdout=diff_proc.get("stdout", "") if diff_proc else error,
            detail=f"cannot determine changed files: {error}",
        )
    root, worktree = repo_path(ctx), worktree_path(ctx)
    if root is None or worktree is None:
        return make_proof("MUTATION", ctx, result="FAIL", detail="repo_root and worktree are required")
    python_paths = [
        path for path in paths
        if path.endswith(".py") and (worktree / path).is_file()
        and "/tests/" not in path.replace("\\", "/")
        and not Path(path).name.startswith("test_")
    ]
    if not python_paths:
        return make_proof(
            "MUTATION", ctx, result="SKIP",
            command=["factory-mutation-runner"], tool_name="factory-mutation-runner",
            detail="no changed non-test Python source files; automatic mutation operators are not applicable",
            stdout="no changed Python source",
        )

    policy, policy_error = read_json(policy_path(ctx, "mutation.json"))
    if policy_error:
        return make_proof("MUTATION", ctx, result="FAIL", detail=policy_error, stdout=policy_error)
    tier = str(ctx.get("tier") or "none")
    threshold_cfg = (policy.get("thresholds") or {}).get(tier)
    if not isinstance(threshold_cfg, dict):
        return make_proof("MUTATION", ctx, result="FAIL", detail=f"mutation policy has no threshold for tier {tier!r}")
    operators = {
        str(item.get("id")) for item in policy.get("operators", [])
        if isinstance(item, dict) and tier in item.get("enabled_tiers", [])
    }
    operators &= _IMPLEMENTED_OPERATORS
    if not operators:
        return make_proof("MUTATION", ctx, result="FAIL", detail=f"no implemented mutation operators enabled for tier {tier}")

    diff_proc_full, diff_error = get_diff(ctx, 0)
    if diff_error:
        return make_proof("MUTATION", ctx, result="FAIL", detail=f"cannot read diff: {diff_error}")
    diff = diff_proc_full["stdout"]
    line_hunks, hunk_counts, added_text, removed_text = _parse_hunks(diff)
    scope_cfg = policy.get("scope") if isinstance(policy.get("scope"), dict) else {}
    exclude_moved = bool(scope_cfg.get("exclude_moved_lines", True))
    candidates = []
    parse_failures = []
    per_hunk_total: dict[tuple[str, int], int] = {}
    source_texts = {}
    comment_only: set[tuple[str, int]] = set()
    relocated_sites = 0
    for path in python_paths:
        source_path = worktree / path
        relative_parts = Path(path).parts
        ancestors = [worktree.joinpath(*relative_parts[:index]) for index in range(1, len(relative_parts) + 1)]
        if any(candidate.is_symlink() for candidate in ancestors):
            parse_failures.append(f"{path}: changed source traverses a symlink; mutation overlay refuses it")
            continue
        source = source_path.read_text(encoding="utf-8", errors="replace")
        source_texts[path] = source
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            parse_failures.append(f"{path}: {exc}")
            continue
        comment_only |= _comment_only_hunks(
            path, line_hunks.get(path, {}), _ignorable_lines(source, tree)
        )
        sites = [site for site in _operator_sites(tree, line_hunks.get(path, {})) if site["kind"] in operators]
        for site in sites:
            if exclude_moved and _is_relocated(
                site["node"], added_text.get(path, {}), removed_text.get(path, set())
            ):
                relocated_sites += 1
                continue
            hunk_key = (path, int(site["hunk"]))
            per_hunk_total[hunk_key] = per_hunk_total.get(hunk_key, 0) + 1
            candidates.append({"path": path, **site})

    if parse_failures:
        return make_proof(
            "MUTATION", ctx, result="FAIL", command=["factory-mutation-runner"],
            tool_name="factory-mutation-runner", version="1.0",
            exit_code=1, stdout=canonical_json(parse_failures),
            detail="changed source has syntax errors and cannot be AST-mutated: " + "; ".join(parse_failures),
        )
    if not candidates:
        return make_proof(
            "MUTATION", ctx, result="SKIP", command=["factory-mutation-runner", "--max-mutants", "20"],
            tool_name="factory-mutation-runner", version="1.0", exit_code=0,
            stdout=canonical_json({"changed_python": python_paths, "mutants": 0}),
            detail="no supported mutation operator occurs in a changed hunk; mutation is not applicable",
        )

    env = ctx.get("env") if isinstance(ctx.get("env"), dict) else {}
    default_total = int(policy.get("max_mutants_total", 60))
    max_mutants = int(env.get("FACTORY_MAX_MUTANTS", env.get("max_mutants", default_total)))
    max_mutants = min(max(max_mutants, 1), 200)
    per_hunk_cap = int(policy.get("max_mutants_per_hunk", 20))
    per_hunk_cap = min(max(per_hunk_cap, 1), 20)
    min_per_hunk = int(threshold_cfg.get("min_mutants_per_hunk", policy.get("min_mutants_per_hunk", 1)))
    # The budget is dealt round-robin across mutatable hunks, so a global cap
    # can never starve the last hunk. Every hunk gets its minimum before any
    # hunk gets a second mutant.
    selected, _hunk_generated = _select_mutants(
        candidates, max_mutants, per_hunk_cap, min_per_hunk
    )
    truncated = len(selected) != len(candidates)

    # Only a hunk with a supported operator can carry a test gap. A
    # comment/docstring-only hunk has nothing to mutate by definition, and a
    # hunk with only relocated code has no new behaviour; both are recorded as
    # ineligible rather than failed. The minimum applies to mutatable hunks
    # only, so the cap can never manufacture a below-minimum hunk.
    source_hunks = {
        (path, hunk)
        for path, count in hunk_counts.items()
        if path in python_paths
        for hunk in range(1, count + 1)
    }
    mutatable_by_hunk: dict[tuple[str, int], int] = {}
    selected_by_hunk: dict[tuple[str, int], int] = {}
    for item in candidates:
        key = (item["path"], int(item["hunk"]))
        mutatable_by_hunk[key] = mutatable_by_hunk.get(key, 0) + 1
    for item in selected:
        key = (item["path"], int(item["hunk"]))
        selected_by_hunk[key] = selected_by_hunk.get(key, 0) + 1
    mutatable_hunks = {
        (path, hunk)
        for path, hunk in source_hunks
        if mutatable_by_hunk.get((path, hunk), 0) > 0
    }
    comment_only_hunks = sorted(
        f"{path}#{hunk}" for path, hunk in (source_hunks & comment_only)
    )
    not_applicable_hunks = [
        f"{path}#{hunk}"
        for path, hunk in sorted(source_hunks - mutatable_hunks - comment_only)
    ]
    starved_hunks = {
        (path, hunk)
        for path, hunk in mutatable_hunks
        if selected_by_hunk.get((path, hunk), 0) < min_per_hunk
    }
    hunk_errors = [
        f"{path} hunk {hunk}: {selected_by_hunk.get((path, hunk), 0)} mutants, minimum {min_per_hunk}"
        for path, hunk in sorted(starved_hunks)
    ]

    pytest = _pytest_command(worktree)
    if pytest is None:
        return make_proof(
            "MUTATION", ctx, result="TOOL_ABSENT",
            command=["python", "-m", "pytest"], tool_name="pytest", version="absent",
            detail="pytest is unavailable; generated mutants cannot be evaluated",
            stdout="pytest unavailable",
        )
    version = _pytest_version(pytest, ctx, worktree)
    added_tests, _added_lines_unused = _test_nodeids(diff, worktree)
    selectors = sorted({test["nodeid"] for test in added_tests})
    selection_cfg = policy.get("test_selection") if isinstance(policy.get("test_selection"), dict) else {}
    wide_targets = _covering_targets(worktree, python_paths, selectors, selection_cfg)
    if not selectors and not wide_targets:
        return make_proof(
            "MUTATION", ctx, result="SKIP", command=["factory-mutation-runner"],
            tool_name="factory-mutation-runner", version="1.1", exit_code=0,
            stdout=canonical_json({"changed_python": python_paths, "mutants": 0}),
            detail="no added test and no covering test file for the changed module; mutation is not applicable",
        )
    if selectors:
        pytest_args = [*pytest, "-q", *selectors]
        wide_args = [*pytest, "-q", *wide_targets] if wide_targets else []
    else:
        pytest_args = [*pytest, "-q", *wide_targets]
        wide_args = []

    timeout_seconds = min(max(int(policy.get("timeout_s", 600)), 1), 1800)
    start = time.monotonic()
    summaries = []
    killed = 0
    survivors = 0
    invalid = 0
    with tempfile.TemporaryDirectory(prefix="factory-mutation-worktree-") as tmp:
        sandbox = Path(tmp) / "repo"
        try:
            shutil.copytree(
                worktree, sandbox, symlinks=True,
                ignore=shutil.ignore_patterns(
                    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
                ),
            )
        except OSError as exc:
            return make_proof(
                "MUTATION", ctx, result="FAIL",
                command=["factory-mutation-runner", "--max-mutants", str(max_mutants)],
                tool_name="factory-mutation-runner", exit_code=1,
                stdout=f"{type(exc).__name__}: {exc}",
                detail=f"cannot create isolated mutation sandbox: {exc}",
            )
        # A green baseline is necessary: otherwise every mutant may appear
        # killed by an unrelated environment/test failure.
        baseline = run_process(
            pytest_args, ctx=ctx, cwd=sandbox, timeout=min(timeout_seconds, 90),
            env=_test_env(
                sandbox, ctx, "sqlite",
                debug=True, sqlite_url="sqlite:///test-db.sqlite3",
            ),
        )
        if baseline["error"]:
            return make_proof(
                "MUTATION", ctx, result="TOOL_ABSENT",
                command=pytest_args, tool_name="pytest", version=version,
                stdout=baseline["stderr"], detail=f"pytest could not start: {baseline['error']}",
            )
        combined = (baseline["stdout"] or "") + (baseline["stderr"] or "")
        if baseline["exit"] == 5 or "no tests ran" in combined.lower():
            return make_proof(
                "MUTATION", ctx, result="SKIP",
                command=pytest_args, tool_name="pytest", version=version,
                exit_code=baseline["exit"], stdout=combined,
                detail="no relevant tests collected; mutation is not applicable",
            )
        if baseline["exit"] != 0:
            return make_proof(
                "MUTATION", ctx, result="FAIL",
                command=pytest_args, tool_name="pytest", version=version,
                exit_code=baseline["exit"],
                stdout=combined,
                detail="relevant test baseline fails before mutation; kill scores would be invalid",
            )
        if wide_args:
            wide_baseline = run_process(
                wide_args, ctx=ctx, cwd=sandbox,
                timeout=min(timeout_seconds, 120),
                env=_test_env(
                    sandbox, ctx, "sqlite",
                    debug=True, sqlite_url="sqlite:///test-db.sqlite3",
                ),
            )
            wide_combined = (wide_baseline["stdout"] or "") + (wide_baseline["stderr"] or "")
            if wide_baseline["exit"] == 5 or "no tests ran" in wide_combined.lower():
                wide_args = []
            elif wide_baseline["error"] or wide_baseline["exit"] != 0:
                return make_proof(
                    "MUTATION", ctx, result="FAIL",
                    command=wide_args, tool_name="pytest", version=version,
                    exit_code=wide_baseline["exit"], stdout=wide_combined,
                    detail="covering-test baseline fails before mutation; mutation scores would be invalid",
                )
        for index, item in enumerate(selected):
            remaining = timeout_seconds - (time.monotonic() - start)
            if remaining <= 0:
                invalid += 1
                summaries.append({
                    "index": index, "path": item["path"], "hunk": item["hunk"],
                    "operator": item["kind"], "outcome": "timeout_budget_exhausted",
                })
                continue
            mutant = _mutate(source_texts[item["path"]], item)
            if mutant is None:
                invalid += 1
                summaries.append({
                    "index": index, "path": item["path"], "hunk": item["hunk"],
                    "operator": item["kind"], "outcome": "mutation_application_failed",
                })
                continue
            mutated_path = sandbox / item["path"]
            original = source_texts[item["path"]]
            mutated_path.write_text(mutant, encoding="utf-8")
            env_for_run = {
                **_test_env(
                    sandbox, ctx, "sqlite",
                    debug=True, sqlite_url="sqlite:///test-db.sqlite3",
                ),
                "FACTORY_MUTANT_INDEX": str(index),
            }
            proc = run_process(
                pytest_args, ctx=ctx, cwd=sandbox,
                timeout=max(1, min(90, remaining)),
                env=env_for_run,
            )
            used = "fast"
            # A survivor of the task's own tests is re-run against the tests
            # that cover the changed module before it is called a survivor.
            if (
                wide_args
                and not proc["timed_out"]
                and not proc["error"]
                and proc["exit"] == 0
            ):
                remaining_wide = timeout_seconds - (time.monotonic() - start)
                if remaining_wide <= 0:
                    invalid += 1
                    outcome = "timeout_budget_exhausted"
                else:
                    proc = run_process(
                        wide_args, ctx=ctx, cwd=sandbox,
                        timeout=max(1, min(90, remaining_wide)),
                        env=env_for_run,
                    )
                    used = "wide"
            mutated_path.write_text(original, encoding="utf-8")
            if proc["timed_out"]:
                invalid += 1
                outcome = "timed_out"
            elif proc["error"]:
                invalid += 1
                outcome = "tool_error"
            elif proc["exit"] == 0:
                survivors += 1
                outcome = "survived"
            else:
                killed += 1
                outcome = "killed"
            summaries.append({
                "index": index, "path": item["path"], "hunk": item["hunk"],
                "line": getattr(item["node"], "lineno", None),
                "operator": item["kind"], "outcome": outcome,
                "selection": used,
            })

    total_generated = len(candidates)
    total_run = len(selected)
    ratio = (killed / total_run) if total_run else 0.0
    ratio_threshold = float(threshold_cfg.get("kill_ratio", 1.0))
    risk_policy, risk_error = read_json(policy_path(ctx, "risk-map.json"))
    risk_policy = risk_policy if isinstance(risk_policy, dict) else {}
    high_survivors = []
    for item in summaries:
        if item["outcome"] != "survived":
            continue
        if _risk_tier(item["path"], risk_policy, tier) in {"high", "max"}:
            high_survivors.append(item)
    threshold_fail = ratio < ratio_threshold
    policy_errors = list(hunk_errors)
    if threshold_fail:
        policy_errors.append(f"kill ratio {ratio:.3f} is below tier threshold {ratio_threshold:.3f}")
    if high_survivors:
        policy_errors.append(f"{len(high_survivors)} survivor(s) remain in high/max risk hunks")
    if starved_hunks:
        policy_errors.append(
            "MUTATION_TRUNCATED: the budget starved "
            f"{len(starved_hunks)} mutatable hunk(s) below the minimum of {min_per_hunk} "
            f"(generated {total_generated}, ran {total_run}, cap {max_mutants})"
        )
    if total_run == 0:
        policy_errors.append("MUTATION_NO_MUTANTS: no mutants were executed")
    if invalid:
        policy_errors.append(f"{invalid} mutant run(s) were invalid or timed out")
    if risk_error:
        policy_errors.append(f"risk map unavailable: {risk_error}")
    evidence = {
        "tier": tier,
        "max_mutants": max_mutants,
        "per_hunk_cap": per_hunk_cap,
        "total_mutatable": total_generated,
        "total_generated": total_run,
        "killed": killed,
        "survivors": survivors,
        "invalid": invalid,
        "kill_ratio": ratio,
        "threshold": ratio_threshold,
        "per_hunk": {
            f"{path}#{hunk}": {
                "mutatable": mutatable_by_hunk.get((path, hunk), 0),
                "generated": selected_by_hunk.get((path, hunk), 0),
            }
            for path, hunk in sorted(source_hunks)
        },
        "truncated": truncated,
        "relocated_sites_excluded": relocated_sites,
        "comment_only_hunks": comment_only_hunks,
        "not_applicable_hunks": not_applicable_hunks,
        "starved_hunks": [f"{path}#{hunk}" for path, hunk in sorted(starved_hunks)],
        "test_selection": {
            "fast": selectors,
            "wide": wide_targets,
            "wide_used": bool(wide_args),
        },
        "mutants": summaries,
        "errors": policy_errors,
    }
    result = "FAIL" if policy_errors else "PASS"
    command = pytest_args
    artifacts: list[dict] = []
    task_id = str(ctx.get("task_id") or "")
    if task_id:
        text = canonical_json(evidence)
        try:
            artifact_path = evidence_dir(task_id) / "artifacts" / "MUTATION.json"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(text, encoding="utf-8")
            artifacts = [{
                "path": str(artifact_path),
                "sha256": sha256(text),
                "kind": "mutation-evidence",
            }]
        except (FactoryError, OSError):
            pass
    return make_proof(
        "MUTATION", ctx, result=result, command=command,
        tool_name="pytest+factory-ast-mutator", version=version,
        exit_code=1 if policy_errors else 0, stdout=canonical_json(evidence),
        artifacts=artifacts,
        detail="; ".join(policy_errors) if policy_errors else (
            f"killed {killed}/{total_run} mutants ({ratio:.1%}); tier threshold {ratio_threshold:.1%}; "
            f"bounded by --max-mutants {max_mutants}"
        ),
        network="host-required",
    )
