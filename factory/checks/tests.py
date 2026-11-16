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

from ..util import FactoryError, evidence_dir
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
    sha256,
    temp_diff,
    tool_version,
    worktree_path,
)
from .static import _added_lines

DEFAULT_SQLITE_TEST_URL = "sqlite:///test-db.sqlite3"
DJANGO_TEST_BASE_NAMES = {
    "TestCase",
    "TransactionTestCase",
    "SimpleTestCase",
    "APITestCase",
    "LiveServerTestCase",
}


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


def _pytest_command(root: Path, ctx: dict | None = None) -> list[str] | None:
    candidates = [
        root / ".venv" / "bin" / "python",
        root / "backend" / ".venv" / "bin" / "python",
        root / "backend" / "venv" / "bin" / "python",
        root / ".venv" / "Scripts" / "python.exe",
        root / "backend" / ".venv" / "Scripts" / "python.exe",
    ]
    env = (ctx or {}).get("env") if isinstance((ctx or {}).get("env"), dict) else {}
    hint = env.get("FACTORY_SUITE_PYTHON")
    if hint:
        candidates.insert(0, Path(str(hint)).expanduser())
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


def _database_env(
    engine: str,
    postgres_url: str | None = None,
    *,
    debug: bool = False,
    sqlite_url: str = "sqlite:///:memory:",
) -> dict[str, str]:
    debug_value = "true" if debug else "false"
    if engine == "sqlite":
        return {
            "DATABASE_URL": sqlite_url,
            "DATABASE_ENGINE": "sqlite",
            "DB_ENGINE": "sqlite",
            "DJANGO_DEBUG": debug_value,
        }
    return {
        "DATABASE_URL": postgres_url or "",
        "DATABASE_ENGINE": "postgres",
        "DB_ENGINE": "postgres",
        "DJANGO_DEBUG": debug_value,
    }


def _test_env(
    root: Path,
    ctx: dict,
    engine: str,
    postgres_url: str | None = None,
    *,
    debug: bool = False,
    sqlite_url: str = "sqlite:///:memory:",
) -> dict[str, str]:
    extra = _database_env(
        engine, postgres_url, debug=debug, sqlite_url=sqlite_url,
    )
    extra["PYTEST_ADDOPTS"] = "-p no:cacheprovider"
    if "DJANGO_SETTINGS_MODULE" not in extra:
        for settings_file in (
            root / "backend" / "config" / "settings.py",
            root / "config" / "settings.py",
        ):
            if settings_file.is_file():
                extra["DJANGO_SETTINGS_MODULE"] = "config.settings"
                break
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


def _suite_policy(ctx: dict) -> dict:
    doc, _error = read_json(policy_path(ctx, "check-runners.json"))
    if isinstance(doc, dict) and isinstance(doc.get("suite_runner"), dict):
        return doc["suite_runner"]
    return {}


def _suite_python(root: Path, ctx: dict, policy: dict) -> tuple[str | None, str]:
    """Resolve the interpreter that owns the project's Django install.

    Resolution hints come from check-runners.json (suite_runner) so a caller
    can point the runner at an existing venv that is never copied into the
    isolated sandbox. FACTORY_SUITE_PYTHON wins when explicitly supplied.
    """
    env = ctx.get("env") if isinstance(ctx.get("env"), dict) else {}
    configured = env.get("FACTORY_SUITE_PYTHON")
    if configured and Path(str(configured)).expanduser().is_file():
        return str(Path(str(configured)).expanduser()), ""
    candidates = policy.get("python_candidates") or [
        "backend/venv/bin/python",
        "backend/venv/bin/python3",
        "backend/.venv/bin/python",
        ".venv/bin/python",
        "venv/bin/python",
    ]
    for relative in candidates:
        candidate = root / str(relative)
        if candidate.is_file():
            return str(candidate), ""
    if os.sys.executable and Path(os.sys.executable).is_file():
        return os.sys.executable, "project virtualenv not found; fell back to the harness interpreter"
    return None, "no usable Python interpreter found for the Django suite"


def _manage_py(root: Path, policy: dict) -> Path | None:
    candidates = policy.get("manage_py_candidates") or ["backend/manage.py", "manage.py"]
    return next((path for rel in candidates if (path := root / str(rel)).is_file()), None)


def _django_version(python: str, cwd: Path, ctx: dict) -> str:
    probe = run_process(
        [python, "-c", "import django; print(django.get_version())"],
        ctx=ctx, cwd=cwd, timeout=20,
    )
    text = (probe["stdout"] or probe["stderr"]).strip().splitlines()
    return f"django {text[0][:80]}" if probe["exit"] == 0 and text else "version query unavailable"


def _suite_env(
    manage_dir: Path,
    ctx: dict,
    engine: str,
    postgres_url: str | None,
    policy: dict,
) -> dict[str, str]:
    """Canonical Django suite env: DEBUG on, an explicit DATABASE_URL, and
    never DJANGO_ENV (a settings test asserts the ambient name is absent)."""
    sqlite_url = str(policy.get("sqlite_url") or DEFAULT_SQLITE_TEST_URL)
    return {
        "DJANGO_SETTINGS_MODULE": str(policy.get("settings_module") or "config.settings"),
        "DJANGO_DEBUG": "true",
        "PYTHONPATH": str(manage_dir),
        "DATABASE_URL": postgres_url if engine == "postgres" else sqlite_url,
    }


def _parse_django_suite(text: str) -> dict:
    ran = re.findall(r"^Ran (\d+) tests? in ", text, re.MULTILINE)
    failed_ids: list[str] = []
    total_failed = 0
    for match in re.finditer(r"^(?:FAIL|ERROR): .+? \((.+?)\)", text, re.MULTILINE):
        total_failed += 1
        label = match.group(1)
        if label not in failed_ids and len(failed_ids) < 10:
            failed_ids.append(label)
    return {
        "tests": int(ran[-1]) if ran else None,
        "ok": bool(re.search(r"^OK(?:\s|\(|$)", text, re.MULTILINE))
        and not re.search(r"^FAILED\b", text, re.MULTILINE),
        "failed_total": total_failed,
        "failed_ids": failed_ids,
    }


def _write_suite_artifact(ctx: dict, name: str, text: str) -> dict | None:
    task_id = str(ctx.get("task_id") or "")
    if not task_id:
        return None
    try:
        path = evidence_dir(task_id) / "artifacts" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except (OSError, FactoryError):
        return None
    return {"path": str(path), "sha256": sha256(text)}


def check_suite(ctx: dict) -> dict:
    """Run the canonical Django test suite on sqlite and, when reachable, postgres."""
    root = worktree_path(ctx)
    if root is None or not root.is_dir():
        return make_proof("SUITE", ctx, result="FAIL", detail="ctx.worktree is missing or not a directory")
    policy = _suite_policy(ctx)
    python, python_error = _suite_python(root, ctx, policy)
    if python is None:
        return _absent("SUITE", ctx, python_error, ["python", "manage.py", "test"], "django")
    manage = _manage_py(root, policy)
    if manage is None:
        return make_proof(
            "SUITE", ctx, result="FAIL", tool_name="django", version="absent",
            detail="no manage.py found in the worktree; the canonical Django runner cannot start",
        )
    timeout = int((ctx.get("env") or {}).get("FACTORY_TEST_TIMEOUT", 1800)) if isinstance(ctx.get("env"), dict) else 1800
    timeout = min(max(timeout, 1), 3600)
    pg_url, pg_host, pg_port = _postgres_target(ctx)
    engines = ["sqlite"] + (["postgres"] if pg_url else [])
    runs: list[dict] = []
    artifacts: list[dict] = []
    commands: list[list[str]] = []
    with tempfile.TemporaryDirectory(prefix="factory-suite-sandbox-") as tmp:
        sandbox = Path(tmp) / "repo"
        ok, copy_error = _sandbox_copy(root, sandbox)
        if not ok:
            return make_proof(
                "SUITE", ctx, result="FAIL", tool_name="django", version="unavailable",
                exit_code=1, stdout=copy_error, detail=copy_error, network="host-required",
            )
        sandbox_manage = sandbox / manage.relative_to(root)
        cwd = sandbox_manage.parent
        version = _django_version(python, cwd, ctx)
        for engine in engines:
            command = [python, str(sandbox_manage), "test", "--noinput"]
            commands.append(command)
            env = _suite_env(cwd, ctx, engine, pg_url, policy)
            proc = run_process(command, ctx=ctx, cwd=cwd, timeout=timeout, env=env)
            log = (proc["stdout"] or "") + (proc["stderr"] or "")
            parsed = _parse_django_suite(log)
            run = {
                "engine": engine,
                "exit": proc["exit"],
                "timed_out": proc["timed_out"],
                "error": proc["error"],
                "tests": parsed["tests"],
                "ok": parsed["ok"],
                "failed_total": parsed["failed_total"],
                "failed_ids": parsed["failed_ids"],
            }
            runs.append(run)
            artifact = _write_suite_artifact(ctx, f"SUITE-{engine}.log", log)
            if artifact:
                artifacts.append(artifact)
        first_command = commands[0]

    failing = [run for run in runs if run["exit"] != 0]
    timed_out = [run for run in runs if run["timed_out"]]
    unparsed = [run for run in runs if not run["ok"] and not run["timed_out"] and run["exit"] == 0]
    if timed_out:
        result = "FLAKY"
    elif failing or unparsed:
        result = "FAIL"
    else:
        result = "PASS"
    run_labels = ", ".join(
        "{}={}{}".format(
            run["engine"],
            run["exit"],
            f" ({run['tests']} tests, {'OK' if run['ok'] else 'FAILED'})",
        )
        for run in runs
    )
    failed_ids = [f"{run['engine']}:{test_id}" for run in runs for test_id in run["failed_ids"]]
    detail = (
        f"django suite results: {run_labels}"
        + (f"; postgres endpoint {pg_host}:{pg_port} was reachable" if pg_url else "; postgres service was not reachable")
        + (f"; failed: {', '.join(failed_ids[:10])}" if failed_ids else "")
        + (f"; {python_error}" if python_error else "")
    )
    return make_proof(
        "SUITE", ctx, result=result, command=first_command, tool_name="django",
        version=version, exit_code=1 if failing or unparsed else 0,
        stdout=canonical_json({
            "engines": {run["engine"]: run for run in runs},
            "runs": runs,
        }),
        detail=detail, network="host-required", artifacts=artifacts,
        env_extra={"DATABASE_ENGINE": "sqlite,postgres" if pg_url else "sqlite"},
    )


def _pytest_invocation(pytest: list[str], tree: Path) -> tuple[list[str], Path]:
    """Run pytest independent of any per-worktree pytest.ini shim.

    The shim (DJANGO_SETTINGS_MODULE/pythonpath/testpaths) is a lane-local,
    untracked file. Pointing --rootdir and the target at backend/ and running
    from backend/ keeps the harness deterministic even when the shim is absent.
    """
    backend = tree / "backend"
    if backend.is_dir():
        return [*pytest, "-q", "--rootdir", str(backend), str(backend)], backend
    return [*pytest, "-q", "--rootdir", str(tree), str(tree)], tree


def check_suite_pytest(ctx: dict) -> dict:
    """Legacy pytest suite, recorded as a non-blocking SUITE_PYTEST diagnostic."""
    root = worktree_path(ctx)
    if root is None or not root.is_dir():
        return make_proof("SUITE_PYTEST", ctx, result="FAIL", detail="ctx.worktree is missing or not a directory")
    pytest_cmd = _pytest_command(root, ctx)
    if pytest_cmd is None:
        return _absent(
            "SUITE_PYTEST", ctx,
            "pytest is not importable from the project interpreter and no pytest executable is on PATH",
            ["python", "-m", "pytest"], "pytest",
        )
    version = _pytest_version(pytest_cmd, ctx, root)
    timeout = int((ctx.get("env") or {}).get("FACTORY_TEST_TIMEOUT", 1800)) if isinstance(ctx.get("env"), dict) else 1800
    timeout = min(max(timeout, 1), 3600)
    runs = []
    pg_url, pg_host, pg_port = _postgres_target(ctx)
    pg_proc = None
    outputs: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="factory-suite-pytest-sandbox-") as tmp:
        sandbox = Path(tmp) / "repo"
        ok, copy_error = _sandbox_copy(root, sandbox)
        if not ok:
            return make_proof(
                "SUITE_PYTEST", ctx, result="FAIL", tool_name="pytest", version=version,
                exit_code=1, stdout=copy_error, detail=copy_error, network="host-required",
            )
        sqlite_command, sqlite_cwd = _pytest_invocation(pytest_cmd, sandbox)
        sqlite_env = _test_env(
            sandbox, ctx, "sqlite", debug=True, sqlite_url=DEFAULT_SQLITE_TEST_URL,
        )
        sqlite_proc = run_process(sqlite_command, ctx=ctx, cwd=sqlite_cwd, timeout=timeout, env=sqlite_env)
        outputs["sqlite"] = (sqlite_proc["stdout"] or "") + (sqlite_proc["stderr"] or "")
        runs.append({"engine": "sqlite", "exit": sqlite_proc["exit"], "timed_out": sqlite_proc["timed_out"]})
        if sqlite_proc["error"]:
            if "No module named" in sqlite_proc["error"]:
                return _absent(
                    "SUITE_PYTEST", ctx,
                    f"pytest runtime dependency unavailable: {sqlite_proc['error']}",
                    sqlite_command, "pytest",
                )
            return _failed(
                "SUITE_PYTEST", ctx, sqlite_command, sqlite_proc, "pytest", version,
                sqlite_proc["error"],
            )
        if sqlite_proc["timed_out"]:
            return _failed(
                "SUITE_PYTEST", ctx, sqlite_command, sqlite_proc, "pytest", version,
                "sqlite pytest suite timed out",
            )
        if pg_url:
            pg_command, pg_cwd = _pytest_invocation(pytest_cmd, sandbox)
            pg_env = _test_env(
                sandbox, ctx, "postgres", pg_url, debug=True,
                sqlite_url=DEFAULT_SQLITE_TEST_URL,
            )
            pg_proc = run_process(pg_command, ctx=ctx, cwd=pg_cwd, timeout=timeout, env=pg_env)
            outputs["postgres"] = (pg_proc["stdout"] or "") + (pg_proc["stderr"] or "")
            runs.append({"engine": "postgres", "exit": pg_proc["exit"], "timed_out": pg_proc["timed_out"]})
            if pg_proc["error"]:
                return _failed(
                    "SUITE_PYTEST", ctx, pg_command, pg_proc, "pytest", version,
                    f"postgres pytest suite error: {pg_proc['error']}",
                )
            if pg_proc["timed_out"]:
                return _failed(
                    "SUITE_PYTEST", ctx, pg_command, pg_proc, "pytest", version,
                    "postgres pytest suite timed out",
                )
        first_command = sqlite_command

    failing = [run for run in runs if run["exit"] != 0]
    all_output = {
        "sqlite": outputs.get("sqlite"),
        "postgres": outputs.get("postgres"),
        "runs": runs,
    }
    result = "FAIL" if failing else "PASS"
    run_labels = ", ".join("{}={}".format(run["engine"], run["exit"]) for run in runs)
    detail = (
        f"pytest diagnostic suite (recorded, never blocking): {run_labels}"
        + (f"; postgres endpoint {pg_host}:{pg_port} was reachable" if pg_url else "; postgres service was not reachable")
    )
    return make_proof(
        "SUITE_PYTEST", ctx, result=result, command=first_command, tool_name="pytest",
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


def _django_module_path(path: str, manage_rel: str) -> str | None:
    posix = path.replace("\\", "/")
    prefix = "" if manage_rel in {"", "."} else manage_rel.strip("/")
    if prefix:
        if not posix.startswith(prefix + "/"):
            return None
        posix = posix[len(prefix) + 1:]
    if not posix.endswith(".py"):
        return None
    parts = PurePosixPath(posix).with_suffix("").parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(part.isidentifier() for part in parts):
        return None
    return ".".join(parts)


def _class_bases(source: str) -> dict[str, list[str]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    return {
        node.name: [_dotted(base) for base in node.bases]
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }


def _is_django_test_class(name: str, bases: dict[str, list[str]], seen: set[str] | None = None) -> bool:
    seen = set(seen or ())
    if name in seen:
        return False
    seen.add(name)
    for base in bases.get(name, []):
        tail = base.rsplit(".", 1)[-1]
        if tail in DJANGO_TEST_BASE_NAMES:
            return True
        if tail in bases and _is_django_test_class(tail, bases, seen):
            return True
    return False


def _django_entry(item: dict, manage_rel: str, worktree: Path) -> dict | None:
    source_path = worktree / item["path"]
    if not source_path.is_file():
        return None
    module = _django_module_path(item["path"], manage_rel)
    if module is None:
        return None
    classes = item["qualified"].split(".")[:-1]
    if not classes:
        return None
    bases = _class_bases(source_path.read_text(encoding="utf-8", errors="replace"))
    if not _is_django_test_class(classes[-1], bases):
        return None
    return {
        **item,
        "module": module,
        "label": ".".join([module, *classes, item["name"]]),
        "runner": "django",
    }


def _django_block_results(text: str) -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {}
    # unittest separates each failure/error traceback with a 70-character rule.
    for block in re.split(r"^={70}\s*$", text, flags=re.MULTILINE):
        stripped = block.strip()
        match = re.match(r"^(FAIL|ERROR): .+? \((.+?)\)", stripped, re.MULTILINE)
        if not match:
            continue
        kind, label = match.group(1), match.group(2)
        results.setdefault(label, []).append(
            {"kind": kind, "assertion_error": "AssertionError" in block}
        )
    return results


def _collection_error(text: str) -> bool:
    return bool(
        re.search(
            r"unittest\.loader\._FailedTest|No module named|ImportError while (?:importing|loading)|ERROR collecting|errors? during collection",
            text,
        )
    )


def _parse_import_names(text: str) -> list[str]:
    names: list[str] = []
    for match in re.finditer(r"No module named '([^']+)'", text):
        name = match.group(1)
        if name not in names:
            names.append(name)
    return names


def _module_candidates(module: str, rel_roots: list[str]) -> list[str]:
    tail = PurePosixPath(*module.split("."))
    candidates: list[str] = []
    for rel_root in rel_roots:
        base = PurePosixPath(rel_root) if rel_root not in {"", "."} else PurePosixPath("")
        candidates.append(str(base / f"{tail}.py"))
        candidates.append(str(base / tail / "__init__.py"))
    return list(dict.fromkeys(candidates))


def _materialize_imports(
    head_tree: Path,
    base_tree: Path,
    text: str,
    materialized: dict[str, list[str]],
    rel_roots: list[str],
) -> list[str]:
    copied: list[str] = []
    for module in _parse_import_names(text):
        if module in materialized:
            continue
        for relative in _module_candidates(module, rel_roots):
            source = head_tree / relative
            target = base_tree / relative
            if not source.is_file():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            materialized.setdefault(module, []).append(relative)
            copied.append(relative)
            break
    return copied


def _run_django_batch(
    ctx: dict,
    python: str,
    tree: Path,
    manage_rel: str,
    labels: list[str],
    timeout: int,
) -> tuple[dict, str]:
    manage = tree / manage_rel / "manage.py"
    env = _suite_env(manage.parent, ctx, "sqlite", None, _suite_policy(ctx))
    command = [python, str(manage), "test", *labels, "--noinput"]
    proc = run_process(command, ctx=ctx, cwd=manage.parent, timeout=timeout, env=env)
    return proc, (proc["stdout"] or "") + (proc["stderr"] or "")


def _run_django_batch_with_imports(
    ctx: dict,
    python: str,
    tree: Path,
    manage_rel: str,
    labels: list[str],
    timeout: int,
    head_tree: Path | None,
    materialized: dict[str, list[str]],
    rel_roots: list[str],
) -> tuple[dict, str, int]:
    attempts = 0
    while True:
        proc, text = _run_django_batch(ctx, python, tree, manage_rel, labels, timeout)
        if head_tree is None or attempts >= 5 or not _collection_error(text):
            return proc, text, attempts
        copied = _materialize_imports(head_tree, tree, text, materialized, rel_roots)
        if not copied:
            return proc, text, attempts
        attempts += 1


def _classify_django_base(label: str, text: str, exit_code: int | None) -> tuple[str, str]:
    blocks = _django_block_results(text)
    if label in blocks:
        if any(block["kind"] == "FAIL" for block in blocks[label]):
            return "ASSERTION", "base failure is a unittest FAIL for the label"
        return "NON_FALSIFIABLE", "error"
    if _collection_error(text):
        return "NON_FALSIFIABLE", "collection error"
    if exit_code == 0:
        return "NON_FALSIFIABLE", "base-green"
    return "NON_FALSIFIABLE", f"label absent from failure output (exit={exit_code})"


def _django_head_passed(label: str, text: str, exit_code: int | None) -> bool:
    if label in _django_block_results(text):
        return False
    if _collection_error(text):
        return False
    return exit_code == 0


def _pytest_node_command(pytest_cmd: list[str], tree: Path, nodeids: list[str]) -> tuple[list[str], Path]:
    backend = tree / "backend"
    if backend.is_dir():
        stripped = [nodeid.removeprefix("backend/") for nodeid in nodeids]
        return [*pytest_cmd, "-q", "--tb=short", "--rootdir", str(backend), *stripped], backend
    return [*pytest_cmd, "-q", "--tb=short", "--rootdir", str(tree), *nodeids], tree


def _classify_pytest_base(text: str, exit_code: int | None) -> tuple[str, str]:
    if exit_code == 0:
        return "NON_FALSIFIABLE", "base-green"
    if _collection_error(text):
        return "NON_FALSIFIABLE", "collection error"
    if re.search(r"\bAssertionError\b", text):
        return "ASSERTION", "base failure contains AssertionError"
    return "NON_FALSIFIABLE", f"non-assertion failure (exit={exit_code})"


def check_base_fail_head_pass(ctx: dict) -> dict:
    root, worktree = repo_path(ctx), worktree_path(ctx)
    if root is None or worktree is None or not ctx.get("base"):
        return make_proof("BASE_FAIL_HEAD_PASS", ctx, result="FAIL", detail="repo_root, worktree, and base are required")
    diff_proc, diff_error = get_diff(ctx, 0)
    if diff_error:
        return make_proof("BASE_FAIL_HEAD_PASS", ctx, result="FAIL", detail=f"cannot inspect diff: {diff_error}")
    definitions, _added = _test_nodeids(diff_proc["stdout"], worktree)
    if not definitions:
        return _skip("BASE_FAIL_HEAD_PASS", ctx, "no added Python tests")
    head_tests = [item for item in definitions if (worktree / item["path"]).is_file()]
    if not head_tests:
        return make_proof("BASE_FAIL_HEAD_PASS", ctx, result="FAIL", detail="added test definitions are not present at head")
    if len(head_tests) > 100:
        return make_proof("BASE_FAIL_HEAD_PASS", ctx, result="FAIL", detail="more than 100 added test nodes; bounded runner refuses")

    policy = _suite_policy(ctx)
    python, python_error = _suite_python(worktree, ctx, policy)
    manage = _manage_py(worktree, policy)
    pytest_cmd = _pytest_command(worktree, ctx)
    if pytest_cmd is None and (python is None or manage is None):
        return _absent(
            "BASE_FAIL_HEAD_PASS", ctx,
            "neither the canonical Django runner nor pytest is available; cannot run added tests against base and head",
            ["python", "manage.py", "test"], "django",
        )
    manage_rel = "."
    django_entries: list[dict] = []
    pytest_entries: list[dict] = []
    if python is not None and manage is not None:
        manage_rel = str(manage.parent.relative_to(worktree)) or "."
        for item in head_tests:
            entry = _django_entry(item, manage_rel, worktree)
            (django_entries if entry else pytest_entries).append(entry or item)
    else:
        pytest_entries = list(head_tests)

    timeout = int((ctx.get("env") or {}).get("FACTORY_TEST_TIMEOUT", 180)) if isinstance(ctx.get("env"), dict) else 180
    timeout = min(max(timeout, 1), 1800)
    if django_entries:
        tool_name = "django"
        version = _django_version(python, worktree, ctx)
    elif pytest_entries and pytest_cmd is not None:
        tool_name = "pytest"
        version = _pytest_version(pytest_cmd, ctx, worktree)
    elif python and manage:
        tool_name = "django"
        version = _django_version(python, worktree, ctx)
    else:
        tool_name = "pytest"
        version = "unavailable"
    head_results: list[dict] = []
    base_results: list[dict] = []
    materialized: dict[str, list[str]] = {}
    failures: list[str] = []
    flaky = False

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
            if source.is_file():
                target = base_root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

        rel_roots = list(dict.fromkeys([manage_rel, "."]))
        django_by_path: dict[str, list[dict]] = {}
        for entry in django_entries:
            django_by_path.setdefault(entry["path"], []).append(entry)

        # Head runs first: the base tree is only trusted after every added
        # test is demonstrated green at head.
        for _path, entries in sorted(django_by_path.items()):
            labels = [entry["label"] for entry in entries]
            proc, text = _run_django_batch(ctx, python, head_root, manage_rel, labels, timeout)
            if proc["timed_out"]:
                flaky = True
            for entry in entries:
                passed = _django_head_passed(entry["label"], text, proc["exit"])
                head_results.append({
                    "label": entry["label"], "nodeid": entry["nodeid"],
                    "runner": "django", "exit": proc["exit"], "passed": passed,
                    "timed_out": proc["timed_out"], "tail": text[-1200:],
                })
                if not passed:
                    failures.append(f"{entry['label']} did not pass on head (exit={proc['exit']})")

        for _path, entries in sorted(django_by_path.items()):
            labels = [entry["label"] for entry in entries]
            proc, text, attempts = _run_django_batch_with_imports(
                ctx, python, base_root, manage_rel, labels, timeout,
                head_root, materialized, rel_roots,
            )
            if proc["timed_out"]:
                flaky = True
            for entry in entries:
                fail_kind, reason = _classify_django_base(entry["label"], text, proc["exit"])
                base_results.append({
                    "label": entry["label"], "nodeid": entry["nodeid"],
                    "runner": "django", "exit": proc["exit"], "fail_kind": fail_kind,
                    "reason": reason, "import_retries": attempts,
                    "timed_out": proc["timed_out"], "tail": text[-1200:],
                })
                if fail_kind != "ASSERTION":
                    failures.append(f"{entry['label']} was not falsifiable on base: {reason}")

        for item in pytest_entries:
            if pytest_cmd is None:
                failures.append(f"{item['nodeid']} has no available runner")
                continue
            head_command, head_cwd = _pytest_node_command(pytest_cmd, head_root, [item["nodeid"]])
            head_proc = run_process(
                head_command, ctx=ctx, cwd=head_cwd, timeout=timeout,
                env=_test_env(head_root, ctx, "sqlite", debug=True, sqlite_url=DEFAULT_SQLITE_TEST_URL),
            )
            head_text = (head_proc["stdout"] or "") + (head_proc["stderr"] or "")
            if head_proc["timed_out"]:
                flaky = True
            head_passed = head_proc["exit"] == 0
            head_results.append({
                "label": item["nodeid"], "nodeid": item["nodeid"], "runner": "pytest",
                "exit": head_proc["exit"], "passed": head_passed,
                "timed_out": head_proc["timed_out"], "tail": head_text[-1200:],
            })
            if not head_passed:
                failures.append(f"{item['nodeid']} did not pass on head (exit={head_proc['exit']})")

            attempts = 0
            while True:
                base_command, base_cwd = _pytest_node_command(pytest_cmd, base_root, [item["nodeid"]])
                base_proc = run_process(
                    base_command, ctx=ctx, cwd=base_cwd, timeout=timeout,
                    env=_test_env(base_root, ctx, "sqlite", debug=True, sqlite_url=DEFAULT_SQLITE_TEST_URL),
                )
                base_text = (base_proc["stdout"] or "") + (base_proc["stderr"] or "")
                if attempts >= 5 or base_proc["exit"] == 0 or not _collection_error(base_text):
                    break
                copied = _materialize_imports(head_root, base_root, base_text, materialized, rel_roots)
                if not copied:
                    break
                attempts += 1
            if base_proc["timed_out"]:
                flaky = True
            fail_kind, reason = _classify_pytest_base(base_text, base_proc["exit"])
            base_results.append({
                "label": item["nodeid"], "nodeid": item["nodeid"], "runner": "pytest",
                "exit": base_proc["exit"], "fail_kind": fail_kind, "reason": reason,
                "import_retries": attempts, "timed_out": base_proc["timed_out"],
                "tail": base_text[-1200:],
            })
            if fail_kind != "ASSERTION":
                failures.append(f"{item['nodeid']} was not falsifiable on base: {reason}")

    evidence = {
        "base": base_results,
        "head": head_results,
        "materialized_imports": [
            {"module": module, "paths": paths} for module, paths in sorted(materialized.items())
        ],
        "errors": failures,
    }
    if python_error and django_entries:
        evidence["python_resolution"] = python_error
    result = "FLAKY" if flaky else "FAIL" if failures else "PASS"
    if django_entries:
        first = django_entries[0]["label"]
        command = [python or "python", str(worktree / manage_rel / "manage.py"), "test", first, "--noinput"]
    elif pytest_entries:
        command, _cwd = _pytest_node_command(pytest_cmd or ["pytest"], worktree, [pytest_entries[0]["nodeid"]])
    else:
        command = []
    return make_proof(
        "BASE_FAIL_HEAD_PASS", ctx, result=result, command=command,
        tool_name=tool_name, version=version, exit_code=1 if failures or flaky else 0,
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


def _skip_json(check: str, ctx: dict, reason: str) -> dict:
    """SKIP proof whose stored detail carries a machine-readable skip_reason.

    ``make_proof`` persists only a digest of ``stdout``, so the JSON payload
    must go in ``detail`` to be readable from the saved proof.
    """
    payload = canonical_json({"skip_reason": reason})
    return make_proof(
        check, ctx, result="SKIP", detail=payload, stdout=payload,
    )


def check_thread_race(ctx: dict) -> dict:
    tier = str(ctx.get("tier") or "none")
    if tier == "none":
        return _skip_json("THREAD_RACE", ctx, "tier none configures zero thread-race repetitions")
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
        return _skip_json("THREAD_RACE", ctx, f"tier {tier} configures zero repetitions for runner {runner}")
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


BOOT_REQUIRED_ENV_KEYS = (
    "DJANGO_SECRET_KEY",
    "DJANGO_ENV",
    "DJANGO_ALLOWED_HOSTS",
    "CSRF_TRUSTED_ORIGINS",
    "TOTP_ENCRYPTION_KEY",
    "DATABASE_URL",
)


def _dotenv_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key, value = key.strip(), value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _boot_env(root: Path, manage: Path, ctx: dict) -> tuple[dict[str, str], bool]:
    """Assemble a canonical production boot env from .env.example.

    Only key names are ever reported; the deterministic test keys below are
    test-only placeholders, never a deployment secret.
    """
    env: dict[str, str] = {}
    for candidate in (manage.parent / ".env.example", root / ".env.example"):
        if candidate.is_file():
            env = _dotenv_values(candidate)
            break
    postgres_url, _host, _port = _postgres_target(ctx)
    env.update({
        "DJANGO_DEBUG": "false",
        "DJANGO_ENV": "production",
        "DJANGO_SECRET_KEY": "factory-boottest-only-" + ("x" * 48),
        "TOTP_ENCRYPTION_KEY": "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE=",
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "PYTHONPATH": str(manage.parent),
        "DATABASE_URL": postgres_url or "sqlite:///boottest.sqlite3",
    })
    return env, bool(postgres_url)


def _missing_required_keys(text: str, env: dict[str, str]) -> list[str]:
    missing: list[str] = []
    for key in BOOT_REQUIRED_ENV_KEYS:
        if env.get(key):
            continue
        if key in text and key not in missing:
            missing.append(key)
    return missing


def check_boottest(ctx: dict) -> dict:
    root = worktree_path(ctx)
    if root is None or not root.is_dir():
        return make_proof("BOOTTEST", ctx, result="FAIL", detail="ctx.worktree is missing or not a directory")
    policy = _suite_policy(ctx)
    python, python_error = _suite_python(root, ctx, policy)
    manager = _manage_py(root, policy)
    if manager:
        if python is None:
            return _absent(
                "BOOTTEST", ctx, python_error,
                ["python", "manage.py", "check"], "django",
            )
        with tempfile.TemporaryDirectory(prefix="factory-boottest-sandbox-") as tmp:
            sandbox = Path(tmp) / "repo"
            ok, copy_error = _sandbox_copy(root, sandbox)
            if not ok:
                return make_proof("BOOTTEST", ctx, result="FAIL", detail=copy_error, stdout=copy_error)
            sandbox_manager = sandbox / manager.relative_to(root)
            cwd = sandbox_manager.parent
            command = [python, str(sandbox_manager), "check"]
            env, used_postgres = _boot_env(root, sandbox_manager, ctx)
            proc = run_process(command, ctx=ctx, cwd=cwd, timeout=180, env=env)
            version = _django_version(python, cwd, ctx)
        if proc["error"]:
            if "No module named" in proc["error"]:
                return _absent("BOOTTEST", ctx, f"manage.py runtime dependency unavailable: {proc['error']}", command, "django")
            return _failed("BOOTTEST", ctx, command, proc, "django", version, proc["error"])
        text = (proc["stdout"] or "") + (proc["stderr"] or "")
        if proc["exit"] != 0:
            missing = _missing_required_keys(text, env)
            if missing:
                payload = canonical_json({
                    "missing_keys": missing,
                    "skip_reason": "canonical boot env is missing required key(s): " + ", ".join(missing),
                })
                return make_proof(
                    "BOOTTEST", ctx, result="SKIP", command=command,
                    tool_name="django", version=version, exit_code=proc["exit"],
                    stdout=payload, detail=payload, network="host-required",
                    env_extra={"DEBUG": "False", "DJANGO_SETTINGS_MODULE": "config.settings"},
                )
            return make_proof(
                "BOOTTEST", ctx, result="FAIL", command=command, tool_name="django",
                version=version, exit_code=proc["exit"], stdout=text,
                detail=f"manage.py check with the canonical production env exited {proc['exit']}",
                network="host-required",
                env_extra={"DEBUG": "False", "DJANGO_SETTINGS_MODULE": "config.settings"},
            )
        return make_proof(
            "BOOTTEST", ctx, result="PASS", command=command, tool_name="django",
            version=version, exit_code=proc["exit"], stdout=text,
            detail=(
                "manage.py check with DEBUG=false, DJANGO_ENV=production, and an explicit "
                f"{'postgres' if used_postgres else 'sqlite'} DATABASE_URL exited 0"
                + (f"; {python_error}" if python_error else "")
            ),
            network="host-required",
            env_extra={"DEBUG": "False", "DJANGO_SETTINGS_MODULE": "config.settings"},
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
    command = [python or os.sys.executable, "-c", code]
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
