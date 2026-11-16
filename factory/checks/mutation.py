"""Bounded AOR/ROR/LCR AST mutation runner."""

from __future__ import annotations

import ast
import os
import shutil
import time
import tempfile
from pathlib import Path

from . import (
    canonical_json,
    changed_paths,
    get_diff,
    make_proof,
    policy_path,
    read_json,
    repo_path,
    run_process,
    worktree_path,
)
from .static import _added_lines
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
}
_LCR = {ast.And: ast.Or, ast.Or: ast.And}


def _parse_hunks(diff: str) -> tuple[dict[str, dict[int, int]], dict[str, int]]:
    line_to_hunk: dict[str, dict[int, int]] = {}
    hunk_counts: dict[str, int] = {}
    current = None
    hunk_id = 0
    new_line = 0
    in_hunk = False
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            current = raw[4:].split("\t", 1)[0].strip()
            if current.startswith("b/"):
                current = current[2:]
            if current == "/dev/null":
                current = None
            if current:
                current = current.replace("\\", "/")
                line_to_hunk.setdefault(current, {})
                hunk_counts.setdefault(current, 0)
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
            new_line += 1
        elif marker == " ":
            new_line += 1
    return line_to_hunk, hunk_counts


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
            detail="no changed non-test Python source files; AOR/ROR/LCR are not applicable",
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
    operators &= {"AOR", "ROR", "LCR"}
    if not operators:
        return make_proof("MUTATION", ctx, result="FAIL", detail=f"no AOR/ROR/LCR operators enabled for tier {tier}")

    diff_proc_full, diff_error = get_diff(ctx, 0)
    if diff_error:
        return make_proof("MUTATION", ctx, result="FAIL", detail=f"cannot read diff: {diff_error}")
    diff = diff_proc_full["stdout"]
    line_hunks, hunk_counts = _parse_hunks(diff)
    candidates = []
    parse_failures = []
    per_hunk_total: dict[tuple[str, int], int] = {}
    source_texts = {}
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
        sites = [site for site in _operator_sites(tree, line_hunks.get(path, {})) if site["kind"] in operators]
        for site in sites:
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
            detail="no AOR/ROR/LCR operator occurs in a changed hunk; mutation is not applicable",
        )

    env = ctx.get("env") if isinstance(ctx.get("env"), dict) else {}
    max_mutants = int(env.get("FACTORY_MAX_MUTANTS", env.get("max_mutants", 20)))
    max_mutants = min(max(max_mutants, 1), 100)
    per_hunk_cap = int(policy.get("max_mutants_per_hunk", 20))
    per_hunk_cap = min(max(per_hunk_cap, 1), 20)
    # Keep the earliest stable sites, while explicitly failing if a cap omits
    # any policy-generated mutant.
    candidates.sort(key=lambda item: (item["path"], item["hunk"], getattr(item["node"], "lineno", 0), item["kind"], item["operator_index"]))
    selected = []
    hunk_generated: dict[tuple[str, int], int] = {}
    for item in candidates:
        key = (item["path"], int(item["hunk"]))
        hunk_generated[key] = hunk_generated.get(key, 0) + 1
        if hunk_generated[key] > per_hunk_cap or len(selected) >= max_mutants:
            continue
        selected.append(item)
    truncated = len(selected) != len(candidates)

    # Every affected hunk is accounted for, including a hunk without a
    # supported operator. Policy min_mutants_per_hunk is fail-closed.
    source_hunks = {
        (path, hunk)
        for path, count in hunk_counts.items()
        if path in python_paths
        for hunk in range(1, count + 1)
    }
    min_per_hunk = int(threshold_cfg.get("min_mutants_per_hunk", policy.get("min_mutants_per_hunk", 1)))
    mutatable_by_hunk: dict[tuple[str, int], int] = {}
    selected_by_hunk: dict[tuple[str, int], int] = {}
    for item in candidates:
        key = (item["path"], int(item["hunk"]))
        mutatable_by_hunk[key] = mutatable_by_hunk.get(key, 0) + 1
    for item in selected:
        key = (item["path"], int(item["hunk"]))
        selected_by_hunk[key] = selected_by_hunk.get(key, 0) + 1
    hunk_errors = [
        f"{path} hunk {hunk}: {selected_by_hunk.get((path, hunk), 0)} mutants, minimum {min_per_hunk}"
        for path, hunk in sorted(source_hunks)
        if selected_by_hunk.get((path, hunk), 0) < min_per_hunk
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
    pytest_args = [*pytest, "-q", *(selectors if selectors else [])]

    timeout_seconds = min(max(int(policy.get("timeout_s", 300)), 1), 300)
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
            env=_test_env(sandbox, ctx, "sqlite"),
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
            proc = run_process(
                pytest_args, ctx=ctx, cwd=sandbox,
                timeout=max(1, min(90, remaining)),
                env={**_test_env(sandbox, ctx, "sqlite"), "FACTORY_MUTANT_INDEX": str(index)},
            )
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
    if truncated:
        policy_errors.append(
            f"MUTATION_TRUNCATED: generated {total_generated}, ran {total_run} (cap {max_mutants}, per-hunk cap {per_hunk_cap})"
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
        "mutants": summaries,
        "errors": policy_errors,
    }
    result = "FAIL" if policy_errors else "PASS"
    command = pytest_args
    return make_proof(
        "MUTATION", ctx, result=result, command=command,
        tool_name="pytest+factory-ast-mutator", version=version,
        exit_code=1 if policy_errors else 0, stdout=canonical_json(evidence),
        detail="; ".join(policy_errors) if policy_errors else (
            f"killed {killed}/{total_run} mutants ({ratio:.1%}); tier threshold {ratio_threshold:.1%}; "
            f"bounded by --max-mutants {max_mutants}"
        ),
        network="host-required",
    )
