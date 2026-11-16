"""Test-suite, test-integrity, boot, and repeatability check runners."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath

from . import (
    canonical_json,
    changed_paths,
    ctx_path,
    executable,
    get_diff,
    is_test_path,
    make_proof,
    policy_path,
    read_json,
    repo_path,
    run_process,
    temp_diff,
    tool_version,
    worktree_path,
)
from .static import _added_lines


def _skip(check: str, ctx: dict, reason: str) -> dict:
    return make_proof(check, ctx, result="SKIP", detail=reason, stdout=reason)


def _absent(check: str, ctx: dict, reason: str, command=None, tool: str = "pytest") -> dict:
    return make_proof(
        check, ctx, result="TOOL_ABSENT", command=command,
        tool_name=tool, version="absent", detail=reason, stdout=reason,
    )


def _failed(check: str, ctx: dict, command: list[str], proc: dict, tool: str, version: str, detail: str) -> dict:
    result = "FLAKY" if proc.get("timed_out") else "FAIL"
    return make_proof(
        check, ctx, result=result, command=command or proc.get("command"),
        tool_name=tool, version=version, exit_code=proc.get("exit"),
        stdout=(proc.get("stdout") or "") + (proc.get("stderr") or ""),
        detail=detail, network="host-required",
    )


def _pytest_command(root: Path) -> list[str] | None:
    candidates = [
        root / ".venv" / "bin" / "python",
        root / "backend" / ".venv" / "bin" / "python",
        root / "backend" / "venv" / "bin" / "python",
        root / ".venv" / "Scripts" / "python.exe",
        root / "backend" / ".venv" / "Scripts" / "python.exe",
    ]
    python = next((str(p) for p in candidates if p.is_file()), os.sys.executable)
    probe = run_process([python, "-m", "pytest", "--version"], cwd=root, timeout=15)
    if probe["exit"] == 0:
        return [python, "-m", "pytest"]
    pytest = executable("pytest")
    if pytest and run_process([pytest, "--version"], cwd=root, timeout=15)["exit"] == 0:
        return [pytest]
    return None


def _pytest_version(command: list[str], ctx: dict, cwd: Path) -> str:
    probe = run_process([*command, "--version"], ctx=ctx, cwd=cwd, timeout=15)
    text = (probe["stdout"] or probe["stderr"]).strip().splitlines()
    return text[0][:160] if probe["exit"] == 0 and text else "version query unavailable"


def _sandbox_copy(source: Path, destination: Path) -> tuple[bool, str]:
    ignored_names = {
        ".git", ".venv", "venv", "node_modules", "__pycache__",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
        ".coverage", "htmlcov",
    }
    ignored = shutil.ignore_patterns(
        ".git", ".venv", "venv", "node_modules", "__pycache__",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
        ".coverage", "htmlcov",
    )
    try:
        shutil.copytree(source, destination, symlinks=True, ignore=ignored)
        for current, dirs, files in os.walk(source, followlinks=False):
            dirs[:] = [name for name in dirs if name not in ignored_names]
            for name in [*dirs, *files]:
                link = Path(current) / name
                if not link.is_symlink():
                    continue
                relative = link.relative_to(source)
                mirrored = destination / relative
                if not mirrored.is_symlink():
                    continue
                target = link.resolve(strict=False)
                try:
                    inside = target.is_relative_to(source)
                except (AttributeError, RuntimeError):
                    inside = False
                if not inside:
                    return False, f"symlink escapes the worktree: {relative}"
                mirror_target = destination / target.relative_to(source)
                link_value = os.path.relpath(mirror_target, mirrored.parent)
                mirrored.unlink()
                mirrored.symlink_to(link_value, target_is_directory=target.is_dir())
    except (OSError, RuntimeError) as exc:
        return False, f"cannot make isolated test snapshot: {type(exc).__name__}: {exc}"
    return True, ""


def _service_reachable(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _postgres_target(ctx: dict) -> tuple[str | None, str | None, int | None]:
    env = ctx.get("env") if isinstance(ctx.get("env"), dict) else {}
    configured = env.get("FACTORY_POSTGRES_URL")
    if configured and re.match(r"^(?:postgres|postgresql)://", str(configured), re.I):
        match = re.search(r"@([^:/?#]+)(?::(\d+))?", str(configured))
        host = match.group(1) if match else "127.0.0.1"
        port = int(match.group(2) or 5432) if match else 5432
        # Never let a gate run test fixtures against an arbitrary production or
        # developer database URL. Only the loopback test services are eligible.
        if host.lower() in {"localhost", "127.0.0.1", "::1"} and _service_reachable(host, port):
            return str(configured), host, port
    for port in (5432, 5433):
        if _service_reachable("127.0.0.1", port):
            password = "pg2a_local_test_only" if port == 5432 else "pg2b_local_test_only"
            url = f"postgresql://perfume:{password}@127.0.0.1:{port}/perfume_store"
            return url, "127.0.0.1", port
    return None, None, None


def _database_env(engine: str, postgres_url: str | None = None) -> dict[str, str]:
    if engine == "sqlite":
        return {
            "DATABASE_URL": "sqlite:///:memory:",
            "DATABASE_ENGINE": "sqlite",
            "DB_ENGINE": "sqlite",
            "DJANGO_DEBUG": "false",
        }
    return {
        "DATABASE_URL": postgres_url or "",
        "DATABASE_ENGINE": "postgres",
        "DB_ENGINE": "postgres",
        "DJANGO_DEBUG": "false",
    }


def _test_env(root: Path, ctx: dict, engine: str, postgres_url: str | None = None) -> dict[str, str]:
    extra = _database_env(engine, postgres_url)
    extra["PYTEST_ADDOPTS"] = "-p no:cacheprovider"
    source_roots = [worktree_path(ctx), repo_path(ctx)]
    inherited = []
    ctx_env = ctx.get("env") if isinstance(ctx.get("env"), dict) else {}
    for entry in (ctx_env.get("PYTHONPATH") or os.environ.get("PYTHONPATH", "")).split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry).expanduser()
        mapped = False
        for source in source_roots:
            if source is None:
                continue
            try:
                relative = candidate.resolve().relative_to(source.resolve())
            except (OSError, ValueError):
                continue
            replacement = root / relative
            if replacement.exists():
                inherited.append(str(replacement))
            mapped = True
            break
        if not mapped:
            inherited.append(entry)
    for default in (root, root / "backend", root / "src", root / "backend" / "src"):
        if default.is_dir():
            inherited.insert(0, str(default))
    extra["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(inherited))
    return extra


def check_suite(ctx: dict) -> dict:
    root = worktree_path(ctx)
    if root is None or not root.is_dir():
        return make_proof("SUITE", ctx, result="FAIL", detail="ctx.worktree is missing or not a directory")
    pytest = _pytest_command(root)
    if pytest is None:
        return _absent("SUITE", ctx, "pytest is not importable from the project interpreter and no pytest executable is on PATH", ["python", "-m", "pytest"], "pytest")
    version = _pytest_version(pytest, ctx, root)
    base_command = [*pytest, "-q"]
    timeout = int((ctx.get("env") or {}).get("FACTORY_TEST_TIMEOUT", 1800)) if isinstance(ctx.get("env"), dict) else 1800
    timeout = min(max(timeout, 1), 3600)
    runs = []
    pg_url, pg_host, pg_port = _postgres_target(ctx)
    pg_proc = None
    with tempfile.TemporaryDirectory(prefix="factory-suite-sandbox-") as tmp:
        sandbox = Path(tmp) / "repo"
        ok, copy_error = _sandbox_copy(root, sandbox)
        if not ok:
            return make_proof(
                "SUITE", ctx, result="FAIL", command=base_command,
                tool_name="pytest", version=version, exit_code=1,
                stdout=copy_error, detail=copy_error, network="host-required",
            )
        sqlite_env = _test_env(sandbox, ctx, "sqlite")
        sqlite_proc = run_process(base_command, ctx=ctx, cwd=sandbox, timeout=timeout, env=sqlite_env)
        runs.append({"engine": "sqlite", "exit": sqlite_proc["exit"], "timed_out": sqlite_proc["timed_out"]})
        if sqlite_proc["error"]:
            if "No module named" in sqlite_proc["error"]:
                return _absent("SUITE", ctx, f"pytest runtime dependency unavailable: {sqlite_proc['error']}", base_command, "pytest")
            return _failed("SUITE", ctx, base_command, sqlite_proc, "pytest", version, sqlite_proc["error"])
        if sqlite_proc["timed_out"]:
            return _failed("SUITE", ctx, base_command, sqlite_proc, "pytest", version, "sqlite suite timed out")

        if pg_url:
            pg_command = [*pytest, "-q"]
            pg_env = _test_env(sandbox, ctx, "postgres", pg_url)
            pg_proc = run_process(
                pg_command, ctx=ctx, cwd=sandbox, timeout=timeout, env=pg_env,
            )
            runs.append({"engine": "postgres", "exit": pg_proc["exit"], "timed_out": pg_proc["timed_out"]})
            if pg_proc["error"]:
                return _failed("SUITE", ctx, pg_command, pg_proc, "pytest", version, f"postgres suite error: {pg_proc['error']}")
            if pg_proc["timed_out"]:
                return _failed("SUITE", ctx, pg_command, pg_proc, "pytest", version, "postgres suite timed out")

    failing = [run for run in runs if run["exit"] != 0]
    all_output = {
        "sqlite": (sqlite_proc["stdout"] or "") + (sqlite_proc["stderr"] or ""),
        "postgres": ((pg_proc["stdout"] or "") + (pg_proc["stderr"] or "")) if pg_proc else None,
        "runs": runs,
    }
    first_command = base_command
    if failing and not any(run["exit"] == 0 for run in runs):
        result = "FAIL"
    elif failing:
        result = "FAIL"
    else:
        result = "PASS"
    run_labels = ", ".join("{}={}".format(run["engine"], run["exit"]) for run in runs)
    detail = (
        f"pytest suite results: {run_labels}"
        + (f"; postgres endpoint {pg_host}:{pg_port} was reachable" if pg_url else "; postgres service was not reachable")
    )
    return make_proof(
        "SUITE", ctx, result=result, command=first_command, tool_name="pytest",
        version=version, exit_code=1 if failing else 0,
        stdout=canonical_json(all_output), detail=detail, network="host-required",
        env_extra={"DATABASE_ENGINE": "sqlite,postgres" if pg_url else "sqlite"},
    )


def _test_defs(source: str) -> tuple[dict[str, ast.AST], dict[str, int]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}, {}
    tests: dict[str, ast.AST] = {}
    counts: dict[str, int] = {}

    def walk(node: ast.AST, classes: list[str]):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, classes + [child.name])
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child.name.startswith("test"):
                    key = ".".join([*classes, child.name])
                    tests[key] = child
                    count = 0
                    for item in ast.walk(child):
                        if isinstance(item, ast.Assert):
                            count += 1
                        elif isinstance(item, ast.Call):
                            name = _dotted(item.func).split(".")[-1]
                            if name.startswith("assert") or name in {"raises", "warns", "fail"}:
                                count += 1
                    counts[key] = count
                walk(child, classes)
            else:
                walk(child, classes)

    walk(tree, [])
    return tests, counts


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _test_nodeids(diff: str, worktree: Path) -> tuple[list[dict], list[dict]]:
    added = _added_lines(diff)
    added_by_path: dict[str, set[int]] = {}
    for item in added:
        added_by_path.setdefault(item["path"], set()).add(item["line"])
    definitions = []
    for path in sorted(added_by_path):
        if not path.endswith(".py") or not is_test_path(path):
            continue
        file_path = worktree / path
        if not file_path.is_file():
            continue
        source = file_path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        def visit(node: ast.AST, classes: list[str]):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.ClassDef):
                    visit(child, classes + [child.name])
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if child.name.startswith("test") and child.lineno in added_by_path[path]:
                        suffix = "::".join([*classes, child.name])
                        definitions.append({
                            "path": path,
                            "name": child.name,
                            "qualified": ".".join([*classes, child.name]),
                            "nodeid": f"{path}::{suffix}",
                            "line": child.lineno,
                        })
                    visit(child, classes)
                else:
                    visit(child, classes)

        visit(tree, [])
    return definitions, added


def _load_removal_reason_file(ctx: dict, worktree: Path):
    env = ctx.get("env") if isinstance(ctx.get("env"), dict) else {}
    candidates = []
    if env.get("FACTORY_TEST_REMOVAL_REASONS"):
        candidates.append(Path(str(env["FACTORY_TEST_REMOVAL_REASONS"])).expanduser())
    candidates.extend([
        worktree / "test-removal-reasons.json",
        worktree / ".factory" / "test-removal-reasons.json",
        worktree / "evidence" / "test-removal-reasons.json",
    ])
    for path in candidates:
        if path.is_file():
            return read_json(path)
    return None, "test-removal-reasons.json was not provided"


def _removal_entries(doc) -> list[dict]:
    if isinstance(doc, list):
        return [x for x in doc if isinstance(x, dict)]
    if isinstance(doc, dict):
        value = doc.get("removals") or doc.get("test_removals") or doc.get("entries")
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        result = []
        for test_id, entry in doc.items():
            if isinstance(entry, dict):
                result.append({"test_id": test_id, **entry})
        return result
    return []


def _tautology_hits(ctx: dict, added: list[dict]) -> list[dict]:
    rules, error = read_json(policy_path(ctx, "assert-rules.json"))
    if error:
        return [{"error": error}]
    findings = []
    for item in added:
        extension = Path(item["path"]).suffix.lower()
        languages = (
            ["python", "unittest"] if extension == ".py"
            else ["typescript"] if extension in {".ts", ".tsx"}
            else ["javascript"] if extension in {".js", ".jsx"}
            else []
        )
        if not languages:
            continue
        for rule in rules.get("tautology_patterns", []):
            if not set(languages).intersection(rule.get("lang", [])):
                continue
            try:
                matched = re.search(rule["regex"], item["text"].strip())
            except (KeyError, re.error):
                matched = None
            if matched:
                findings.append({
                    "path": item["path"], "line": item["line"],
                    "rule": rule.get("id"), "reason": rule.get("reason"),
                })
    return findings


def check_test_set(ctx: dict) -> dict:
    paths, proc, error = changed_paths(ctx)
    if error:
        return make_proof(
            "TEST_SET", ctx, result="FAIL",
            command=proc["command"] if proc else [], tool_name="git",
            version=tool_version("git", ctx=ctx), exit_code=proc.get("exit") if proc else None,
            stdout=proc.get("stdout", "") if proc else error,
            detail=f"cannot determine changed paths: {error}",
        )
    worktree = worktree_path(ctx)
    root = repo_path(ctx)
    if worktree is None or root is None:
        return make_proof("TEST_SET", ctx, result="FAIL", detail="ctx.worktree and repo_root are required")
    diff_proc, diff_error = get_diff(ctx, 0)
    if diff_error:
        return make_proof("TEST_SET", ctx, result="FAIL", detail=f"cannot inspect diff: {diff_error}")
    diff = diff_proc["stdout"]
    test_paths = sorted(p for p in paths if is_test_path(p) and p.endswith(".py"))
    if not test_paths:
        return _skip("TEST_SET", ctx, "no changed Python test files")
    added = _added_lines(diff)
    tautologies = _tautology_hits(ctx, added)
    added_tests, _ = _test_nodeids(diff, worktree)
    added_test_names = {entry["nodeid"] for entry in added_tests}
    errors = []
    if any("error" in item for item in tautologies):
        errors.extend(item["error"] for item in tautologies if "error" in item)
        tautologies = []
    if tautologies:
        errors.append(f"{len(tautologies)} added assertion line(s) match tautology rules")
    assertion_free = []
    for test in added_tests:
        source = (worktree / test["path"]).read_text(encoding="utf-8", errors="replace")
        definitions, assertion_counts = _test_defs(source)
        if assertion_counts.get(test["qualified"], 0) == 0:
            assertion_free.append({"path": test["path"], "test_id": test["qualified"]})
    if assertion_free:
        errors.append(f"{len(assertion_free)} added test(s) contain no assertion")
    added_by_path: dict[str, set[int]] = {}
    for item in added:
        added_by_path.setdefault(item["path"], set()).add(item["line"])

    # Newly-added skips and xfails weaken the suite and are rejected.
    weakening = []
    for item in added:
        if item["path"] not in test_paths:
            continue
        if re.search(r"pytest\.mark\.(?:skip|skipif|xfail)|pytest\.(?:skip|xfail)\s*\(|unittest\.skip", item["text"]):
            weakening.append({"path": item["path"], "line": item["line"], "text": item["text"].strip()})
    if weakening:
        errors.append(f"{len(weakening)} added skip/xfail construct(s)")

    reason_policy, policy_error = read_json(policy_path(ctx, "test-removal-reasons.json"))
    if policy_error:
        errors.append(policy_error)
        reason_codes = {}
    else:
        reason_codes = reason_policy.get("reason_codes", {})
    reason_doc, reason_file_error = _load_removal_reason_file(ctx, worktree)
    entries = _removal_entries(reason_doc)

    removed_tests = []
    lowered_assertions = []
    for path in test_paths:
        source_head = (worktree / path).read_text(encoding="utf-8", errors="replace") if (worktree / path).is_file() else ""
        head_defs, head_counts = _test_defs(source_head)
        old = run_process(
            ["git", "-C", str(root), "show", f"{ctx.get('base')}:{path}"],
            ctx=ctx, cwd=root, timeout=30,
        )
        source_base = old["stdout"] if old["exit"] == 0 else ""
        base_defs, base_counts = _test_defs(source_base)
        for test_id in sorted(set(base_defs) - set(head_defs)):
            removed_tests.append({"path": path, "test_id": test_id})
        for test_id in sorted(set(base_defs) & set(head_defs)):
            if head_counts.get(test_id, 0) < base_counts.get(test_id, 0):
                lowered_assertions.append({
                    "path": path, "test_id": test_id,
                    "base": base_counts.get(test_id, 0), "head": head_counts.get(test_id, 0),
                })
    if lowered_assertions:
        errors.append(f"{len(lowered_assertions)} existing test(s) have fewer assertions")
    if removed_tests and reason_file_error:
        errors.append(f"{len(removed_tests)} test(s) removed without {reason_file_error}")
    reason_errors = []
    for removed in removed_tests:
        matching = [
            entry for entry in entries
            if str(entry.get("test_id") or entry.get("id") or "").strip() == removed["test_id"]
        ]
        if len(matching) != 1:
            reason_errors.append(f"{removed['test_id']}: expected exactly one removal reason entry")
            continue
        entry = matching[0]
        code = str(entry.get("reason_code") or "")
        spec = reason_codes.get(code)
        if not isinstance(spec, dict):
            reason_errors.append(f"{removed['test_id']}: unknown reason_code {code!r}")
            continue
        if not str(entry.get("reason") or "").strip():
            reason_errors.append(f"{removed['test_id']}: reason text is required")
        covered_by = entry.get("covered_by")
        if spec.get("requires_covered_by") and (
            not isinstance(covered_by, str) or covered_by.strip() not in added_test_names
        ):
            reason_errors.append(f"{removed['test_id']}: covered_by must exactly name an added test")
    if reason_errors:
        errors.extend(reason_errors)

    evidence = {
        "test_paths": test_paths,
        "added_tests": added_tests,
        "assertion_free": assertion_free,
        "removed_tests": removed_tests,
        "lowered_assertions": lowered_assertions,
        "tautologies": tautologies,
        "weakening": weakening,
        "reason_errors": reason_errors,
        "errors": errors,
    }
    return make_proof(
        "TEST_SET", ctx, result="FAIL" if errors else "PASS",
        command=["git", "-C", str(root), "diff", "--unified=0", f"{ctx.get('base')}..{ctx.get('head')}"],
        tool_name="factory-test-set-validator", version="1.0",
        exit_code=1 if errors else 0, stdout=canonical_json(evidence),
        detail="; ".join(errors) if errors else f"checked {len(test_paths)} changed test file(s), {len(added_tests)} added test(s)",
    )


def _archive_revision(root: Path, ref: str, target: Path) -> tuple[bool, str]:
    try:
        archive = subprocess.run(
            ["git", "-C", str(root), "archive", ref],
            capture_output=True, timeout=180, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"git archive failed: {exc}"
    if archive.returncode != 0:
        return False, archive.stderr.decode("utf-8", "replace").strip() or f"git archive exit {archive.returncode}"
    try:
        with tarfile.open(fileobj=__import__("io").BytesIO(archive.stdout), mode="r:") as tf:
            target_root = target.resolve()
            for member in tf.getmembers():
                member_path = PurePosixPath(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    return False, f"unsafe archive path: {member.name}"
                output = (target_root / member.name).resolve()
                if target_root not in output.parents and output != target_root:
                    return False, f"unsafe archive path: {member.name}"
                if member.isdir():
                    output.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    output.parent.mkdir(parents=True, exist_ok=True)
                    source = tf.extractfile(member)
                    if source is not None:
                        output.write_bytes(source.read())
    except (OSError, tarfile.TarError) as exc:
        return False, f"cannot unpack git archive: {exc}"
    return True, ""


def check_base_fail_head_pass(ctx: dict) -> dict:
    root, worktree = repo_path(ctx), worktree_path(ctx)
    if root is None or worktree is None or not ctx.get("base"):
        return make_proof("BASE_FAIL_HEAD_PASS", ctx, result="FAIL", detail="repo_root, worktree, and base are required")
    diff_proc, diff_error = get_diff(ctx, 0)
    if diff_error:
        return make_proof("BASE_FAIL_HEAD_PASS", ctx, result="FAIL", detail=f"cannot inspect diff: {diff_error}")
    definitions, _added = _test_nodeids(diff_proc["stdout"], worktree)
    if not definitions:
        return _skip("BASE_FAIL_HEAD_PASS", ctx, "no added Python pytest tests")
    pytest = _pytest_command(worktree)
    if pytest is None:
        return _absent(
            "BASE_FAIL_HEAD_PASS", ctx,
            "pytest is unavailable; cannot run added tests against base and head",
            ["python", "-m", "pytest"], "pytest",
        )
    version = _pytest_version(pytest, ctx, worktree)
    head_tests = [
        item for item in definitions
        if (worktree / item["path"]).is_file()
    ]
    if not head_tests:
        return make_proof("BASE_FAIL_HEAD_PASS", ctx, result="FAIL", detail="added pytest definitions are not present at head")
    if len(head_tests) > 100:
        return make_proof("BASE_FAIL_HEAD_PASS", ctx, result="FAIL", detail="more than 100 added test nodes; bounded runner refuses")
    timeout = 180
    head_results = []
    base_results = []
    with tempfile.TemporaryDirectory(prefix="factory-base-tests-") as tmp:
        base_root = Path(tmp) / "base"
        base_root.mkdir()
        ok, error = _archive_revision(root, str(ctx["base"]), base_root)
        if not ok:
            return make_proof("BASE_FAIL_HEAD_PASS", ctx, result="FAIL", detail=f"cannot create base test sandbox: {error}")
        head_root = Path(tmp) / "head"
        ok, error = _sandbox_copy(worktree, head_root)
        if not ok:
            return make_proof("BASE_FAIL_HEAD_PASS", ctx, result="FAIL", detail=error)
        for path in sorted({item["path"] for item in head_tests}):
            source = head_root / path
            target = base_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        for item in head_tests:
            base_nodeid = item["nodeid"]
            base_proc = run_process(
                [*pytest, "-q", "--tb=short", base_nodeid],
                ctx=ctx, cwd=base_root, timeout=timeout,
                env=_test_env(base_root, ctx, "sqlite"),
            )
            head_proc = run_process(
                [*pytest, "-q", "--tb=short", item["nodeid"]],
                ctx=ctx, cwd=head_root, timeout=timeout,
                env=_test_env(head_root, ctx, "sqlite"),
            )
            base_text = (base_proc["stdout"] or "") + (base_proc["stderr"] or "")
            head_text = (head_proc["stdout"] or "") + (head_proc["stderr"] or "")
            assertion_failure = base_proc["exit"] not in (0, None) and bool(
                re.search(r"\b(?:AssertionError|assert\s+.+(?:==|!=|is|not))\b", base_text)
            )
            base_results.append({
                "nodeid": base_nodeid, "exit": base_proc["exit"],
                "assertion_failure": assertion_failure,
                "timed_out": base_proc["timed_out"],
                "tail": base_text[-1200:],
            })
            head_results.append({
                "nodeid": item["nodeid"], "exit": head_proc["exit"],
                "timed_out": head_proc["timed_out"],
                "tail": head_text[-1200:],
            })
    failures = []
    flaky = False
    for base_result, head_result in zip(base_results, head_results):
        if base_result["timed_out"] or head_result["timed_out"]:
            flaky = True
        if not base_result["assertion_failure"]:
            failures.append(
                f"{base_result['nodeid']} did not fail on base with an assertion "
                f"(exit={base_result['exit']}, assertion_failure={base_result['assertion_failure']})"
            )
        if head_result["exit"] != 0:
            failures.append(f"{head_result['nodeid']} did not pass on head (exit={head_result['exit']})")
    evidence = {"base": base_results, "head": head_results, "errors": failures}
    result = "FLAKY" if flaky else "FAIL" if failures else "PASS"
    first = head_tests[0]["nodeid"]
    command = [*pytest, "-q", "--tb=short", first]
    return make_proof(
        "BASE_FAIL_HEAD_PASS", ctx, result=result, command=command,
        tool_name="pytest", version=version, exit_code=1 if failures or flaky else 0,
        stdout=canonical_json(evidence),
        detail="; ".join(failures) if failures else f"{len(head_tests)} added test(s) fail by assertion on base and pass on head",
        network="host-required",
        env_extra={"DATABASE_ENGINE": "sqlite"},
    )


def _tier_config(ctx: dict) -> dict:
    tiers, error = read_json(policy_path(ctx, "tiers.json"))
    if error or not isinstance(tiers, dict):
        return {}
    return tiers.get(str(ctx.get("tier") or "none"), {})


def check_thread_race(ctx: dict) -> dict:
    tier = str(ctx.get("tier") or "none")
    if tier == "none":
        return _skip("THREAD_RACE", ctx, "tier none configures zero thread-race repetitions")
    cfg = _tier_config(ctx)
    if not cfg:
        return make_proof(
            "THREAD_RACE", ctx, result="FAIL",
            detail=f"tiers.json has no configuration for tier {tier!r}",
        )
    env = ctx.get("env") if isinstance(ctx.get("env"), dict) else {}
    runner = str(env.get("FACTORY_RUNNER") or env.get("runner") or "builder")
    repetitions = (cfg.get("thread_race_repetitions_by_runner") or {}).get(runner)
    if repetitions is None:
        repetitions = cfg.get("thread_race_repetitions", 0)
    workers = int(cfg.get("thread_race_workers", 1) or 1)
    repetitions = min(max(int(repetitions or 0), 0), 20)
    workers = min(max(workers, 1), 4)
    if repetitions == 0:
        return _skip("THREAD_RACE", ctx, f"tier {tier} configures zero repetitions for runner {runner}")
    root = worktree_path(ctx)
    if root is None:
        return make_proof("THREAD_RACE", ctx, result="FAIL", detail="ctx.worktree is required")
    postgres_url, host, port = _postgres_target(ctx)
    if not postgres_url:
        return _absent("THREAD_RACE", ctx, "postgres test service is not reachable on localhost:5432 or :5433", ["pytest", "-q"], "postgres")
    pytest = _pytest_command(root)
    if pytest is None:
        return _absent("THREAD_RACE", ctx, "pytest is unavailable", ["python", "-m", "pytest"], "pytest")
    version = _pytest_version(pytest, ctx, root)
    command = [*pytest, "-q"]
    if workers > 1:
        help_proc = run_process([*pytest, "--help"], ctx=ctx, cwd=root, timeout=20)
        help_text = (help_proc["stdout"] or "") + (help_proc["stderr"] or "")
        if help_proc["exit"] != 0 or not re.search(r"(?:^|\s)-n\s+NUM|--numprocesses", help_text):
            return _absent(
                "THREAD_RACE", ctx,
                f"tier requires {workers} parallel workers but pytest-xdist is unavailable",
                [*pytest, "-n", str(workers)], "pytest-xdist",
            )
        command.extend(["-n", str(workers)])
    timeout = int(env.get("FACTORY_TEST_TIMEOUT", 1800))
    total = repetitions
    outcomes = []
    with tempfile.TemporaryDirectory(prefix="factory-race-sandbox-") as tmp:
        for index in range(total):
            sandbox = Path(tmp) / f"repo-{index}"
            ok, copy_error = _sandbox_copy(root, sandbox)
            if not ok:
                return make_proof(
                    "THREAD_RACE", ctx, result="FAIL", command=command,
                    tool_name="pytest", version=version, exit_code=1,
                    stdout=copy_error, detail=copy_error, network="host-required",
                )
            extra = _test_env(sandbox, ctx, "postgres", postgres_url)
            extra["FACTORY_RACE_RUN_ID"] = str(index)
            proc = run_process(command, ctx=ctx, cwd=sandbox, timeout=timeout, env=extra)
            outcomes.append({
                "run": index, "exit": proc["exit"], "timed_out": proc["timed_out"],
                "error": proc["error"],
                "output_tail": ((proc["stdout"] or "") + (proc["stderr"] or ""))[-800:],
            })
    outcomes.sort(key=lambda item: item["run"])
    passed = [item for item in outcomes if item["exit"] == 0 and not item["timed_out"]]
    failed = [item for item in outcomes if item["exit"] not in (0, None) or item["timed_out"] or item["error"]]
    if failed and passed:
        result = "FLAKY"
    elif failed:
        result = "FAIL"
    else:
        result = "PASS"
    detail = (
        f"ran {total} PostgreSQL pytest suite repetitions with {workers} worker(s) "
        f"against {host}:{port}; {len(failed)} failed"
    )
    return make_proof(
        "THREAD_RACE", ctx, result=result, command=command, tool_name="pytest",
        version=version, exit_code=0 if not failed else 1,
        stdout=canonical_json(outcomes), detail=detail, network="host-required",
        env_extra={"DATABASE_ENGINE": "postgres"},
    )


def check_boottest(ctx: dict) -> dict:
    root = worktree_path(ctx)
    if root is None or not root.is_dir():
        return make_proof("BOOTTEST", ctx, result="FAIL", detail="ctx.worktree is missing or not a directory")
    python = next(
        (
            str(candidate) for candidate in (
                root / ".venv" / "bin" / "python",
                root / "backend" / ".venv" / "bin" / "python",
                root / "backend" / "venv" / "bin" / "python",
            ) if candidate.is_file()
        ),
        os.sys.executable,
    )
    manager = next((p for p in (root / "manage.py", root / "backend" / "manage.py") if p.is_file()), None)
    if manager:
        with tempfile.TemporaryDirectory(prefix="factory-boottest-sandbox-") as tmp:
            sandbox = Path(tmp) / "repo"
            ok, copy_error = _sandbox_copy(root, sandbox)
            if not ok:
                return make_proof("BOOTTEST", ctx, result="FAIL", detail=copy_error, stdout=copy_error)
            sandbox_manager = sandbox / manager.relative_to(root)
            cwd = sandbox_manager.parent
            command = [python, str(sandbox_manager), "check"]
            extra = {"DEBUG": "False", "DJANGO_DEBUG": "false"}
            proc = run_process(command, ctx=ctx, cwd=cwd, timeout=180, env=extra)
        version = tool_version("python", ctx=ctx, cwd=cwd)
        if proc["error"]:
            if "No module named" in proc["error"]:
                return _absent("BOOTTEST", ctx, f"manage.py runtime dependency unavailable: {proc['error']}", command, "python")
            return _failed("BOOTTEST", ctx, command, proc, "python", version, proc["error"])
        if proc["exit"] == 1 and "No module named" in proc["stderr"]:
            return _absent("BOOTTEST", ctx, f"Django dependency unavailable: {proc['stderr'].strip()}", command, "python")
        result = "PASS" if proc["exit"] == 0 else "FAIL"
        return make_proof(
            "BOOTTEST", ctx, result=result, command=command, tool_name="python",
            version=version, exit_code=proc["exit"],
            stdout=proc["stdout"] + proc["stderr"],
            detail=f"manage.py check with DEBUG=False exited {proc['exit']}",
            network="host-required", env_extra={"DEBUG": "False"},
        )

    paths, diff_proc, error = changed_paths(ctx)
    if error:
        return make_proof("BOOTTEST", ctx, result="FAIL", detail=f"cannot determine changed paths: {error}")
    files = [
        root / path for path in paths
        if path.endswith(".py") and not is_test_path(path) and (root / path).is_file()
    ]
    if not files:
        return _skip("BOOTTEST", ctx, "no manage.py or changed Python implementation files for smoke testing")
    module_names = []
    for file_path in files:
        rel = file_path.relative_to(root).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts and all(part.isidentifier() for part in parts):
            module_names.append(".".join(parts))
    if not module_names:
        return _skip("BOOTTEST", ctx, "changed Python files do not map to importable module names")
    code = (
        "import importlib; "
        f"mods={module_names!r}; "
        "[importlib.import_module(name) for name in mods]; "
        "print('imported', len(mods), 'module(s)')"
    )
    command = [python, "-c", code]
    with tempfile.TemporaryDirectory(prefix="factory-import-sandbox-") as tmp:
        sandbox = Path(tmp) / "repo"
        ok, copy_error = _sandbox_copy(root, sandbox)
        if not ok:
            return make_proof("BOOTTEST", ctx, result="FAIL", command=command, detail=copy_error, stdout=copy_error)
        proc = run_process(command, ctx=ctx, cwd=sandbox, timeout=180, env={"DEBUG": "False", "DJANGO_DEBUG": "false"})
    version = tool_version("python", ctx=ctx, cwd=root)
    if proc["error"]:
        if "No module named" in proc["error"]:
            return _absent("BOOTTEST", ctx, f"import-smoke dependency unavailable: {proc['error']}", command, "python")
        return _failed("BOOTTEST", ctx, command, proc, "python", version, proc["error"])
    if proc["exit"] != 0 and "ModuleNotFoundError" in proc["stderr"]:
        return _absent("BOOTTEST", ctx, f"import-smoke dependency unavailable: {proc['stderr'].strip()}", command, "python")
    return make_proof(
        "BOOTTEST", ctx, result="PASS" if proc["exit"] == 0 else "FAIL",
        command=command, tool_name="python", version=version, exit_code=proc["exit"],
        stdout=proc["stdout"] + proc["stderr"],
        detail=f"import smoke for {len(module_names)} changed module(s) exited {proc['exit']}",
        network="host-required", env_extra={"DEBUG": "False"},
    )
