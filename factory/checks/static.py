"""Static, policy, and copied-tool check runners."""

from __future__ import annotations

import ast
import datetime as _datetime
import hashlib
import json
import os
import re
import socket
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from . import (
    CHECK_RUNNER_VERSION,
    canonical_json,
    changed_paths,
    ctx_path,
    executable,
    get_diff,
    glob_match,
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
from ..util import FactoryError, evidence_dir


def _skip(check: str, ctx: dict, reason: str, command=None, tool="factory-checks") -> dict:
    return make_proof(
        check, ctx, result="SKIP", command=command, tool_name=tool,
        detail=reason, stdout=reason,
    )


def _absent(check: str, ctx: dict, reason: str, command=None, tool="") -> dict:
    return make_proof(
        check, ctx, result="TOOL_ABSENT", command=command,
        tool_name=tool or (command[0] if command else "factory-checks"),
        version="absent", detail=reason, stdout=reason, exit_code=None,
    )


def _diff_paths(check: str, ctx: dict):
    paths, proc, error = changed_paths(ctx)
    if error:
        if proc and proc.get("error") and "No such file" in proc["error"]:
            return None, _absent(check, ctx, f"git is unavailable: {proc['error']}", proc["command"], "git")
        return None, make_proof(
            check, ctx, result="FAIL",
            command=proc["command"] if proc else [],
            tool_name="git", version=tool_version("git", ctx=ctx),
            exit_code=proc.get("exit") if proc else None,
            stdout=proc.get("stdout", "") if proc else error,
            detail=f"cannot determine changed paths: {error}",
        )
    return paths, None


def _diff_text(check: str, ctx: dict, unified: int = 0):
    proc, error = get_diff(ctx, unified)
    if error:
        return None, make_proof(
            check, ctx, result="FAIL", command=proc["command"] if proc else [],
            tool_name="git", version=tool_version("git", ctx=ctx),
            exit_code=proc.get("exit") if proc else None,
            stdout=proc.get("stdout", "") if proc else error,
            detail=f"cannot inspect git diff: {error}",
        )
    return proc["stdout"], None


def _added_lines(diff_text: str) -> list[dict]:
    rows: list[dict] = []
    path = None
    line_no = 0
    in_hunk = False
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:].split("\t", 1)[0].strip()
            if path.startswith("b/"):
                path = path[2:]
            if path == "/dev/null":
                path = None
            in_hunk = False
            continue
        match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", raw)
        if match:
            line_no = int(match.group(1))
            in_hunk = True
            continue
        if not in_hunk or path is None or raw.startswith("\\"):
            continue
        marker = raw[:1] if raw else " "
        if marker == "+":
            rows.append({"path": path.replace("\\", "/"), "line": line_no, "text": raw[1:]})
            line_no += 1
        elif marker == " ":
            line_no += 1
    return rows


def _tool_failure(check: str, ctx: dict, proc: dict, tool: str, version: str, detail: str) -> dict:
    result = "FLAKY" if proc.get("timed_out") else "FAIL"
    return make_proof(
        check, ctx, result=result, command=proc.get("command"),
        tool_name=tool, version=version, exit_code=proc.get("exit"),
        stdout=proc.get("stdout", "") + proc.get("stderr", ""),
        detail=detail, network="not_isolated",
    )


def _json_from_output(output: str):
    try:
        return json.loads(output), None
    except (TypeError, json.JSONDecodeError) as exc:
        return None, str(exc)


def _tool_script(ctx: dict, filename: str) -> Path:
    factory_root = Path(__file__).resolve().parents[2]
    candidate = factory_root / "tools" / filename
    if candidate.is_file():
        return candidate
    root = repo_path(ctx)
    return (root / "tools" / filename) if root else candidate


def _script_version(script: Path) -> str:
    try:
        text = script.read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    match = re.search(r"(?m)^(?:VERSION|TOOL_VERSION)\s*=\s*['\"]([^'\"]+)", text)
    return match.group(1) if match else "unknown"


def _is_deleted(path: str, worktree: Path) -> bool:
    return not (worktree / path).is_file()


def _python_changed(paths: list[str], worktree: Path, include_tests: bool = False) -> list[str]:
    return [
        path for path in paths
        if path.endswith(".py") and (include_tests or not is_test_path(path))
        and not _is_deleted(path, worktree)
    ]


def check_scope(ctx: dict) -> dict:
    paths, problem = _diff_paths("SCOPE", ctx)
    if problem:
        return problem
    globs = ctx.get("scope") or []
    if not isinstance(globs, (list, tuple)):
        return make_proof(
            "SCOPE", ctx, result="FAIL", detail="ctx.scope must be a list of POSIX globs",
            stdout=canonical_json(globs),
        )
    violations = [
        path for path in paths
        if not any(glob_match(path, pattern) for pattern in globs)
    ]
    result = "FAIL" if violations else "PASS"
    detail = (
        f"{len(violations)} changed path(s) outside scope: {', '.join(violations)}"
        if violations else f"all {len(paths)} changed path(s) match ctx.scope"
    )
    return make_proof(
        "SCOPE", ctx, result=result,
        command=["git", "-C", str(repo_path(ctx)), "diff", "--name-only", "--no-renames",
                 f"{ctx.get('base')}..{ctx.get('head')}"],
        tool_name="git", version=tool_version("git", ctx=ctx),
        exit_code=1 if violations else 0,
        stdout=canonical_json({"paths": paths, "scope": list(globs), "violations": violations}),
        detail=detail,
    )


def check_test_infra(ctx: dict) -> dict:
    paths, problem = _diff_paths("TEST_INFRA", ctx)
    if problem:
        return problem
    gate, error = read_json(policy_path(ctx, "gate.json"))
    if error:
        return make_proof(
            "TEST_INFRA", ctx, result="FAIL", detail=error, stdout=error,
        )
    infra_globs = gate.get("test_infra_paths", []) if isinstance(gate, dict) else []
    scope = ctx.get("scope") or []
    touched = [path for path in paths if any(glob_match(path, pat) for pat in infra_globs)]
    violations = [
        path for path in touched
        if not any(glob_match(path, pat) for pat in scope)
    ]
    return make_proof(
        "TEST_INFRA", ctx, result="FAIL" if violations else "PASS",
        command=["git", "-C", str(repo_path(ctx)), "diff", "--name-only", "--no-renames",
                 f"{ctx.get('base')}..{ctx.get('head')}"],
        tool_name="git", version=tool_version("git", ctx=ctx),
        exit_code=1 if violations else 0,
        stdout=canonical_json({"changed": paths, "test_infra": touched, "out_of_scope": violations}),
        detail=(
            "test-infrastructure path(s) changed outside scope: " + ", ".join(violations)
            if violations else f"no out-of-scope test-infrastructure changes ({len(touched)} touched)"
        ),
    )


def check_convention(ctx: dict) -> dict:
    paths, problem = _diff_paths("CONVENTION", ctx)
    if problem:
        return problem
    worktree = worktree_path(ctx)
    if worktree is None:
        return make_proof("CONVENTION", ctx, result="FAIL", detail="ctx.worktree is required")
    files = _python_changed(paths, worktree, include_tests=True)
    if not files:
        return _skip("CONVENTION", ctx, "no changed Python files to lint")
    ruff = executable("ruff")
    if not ruff:
        return _absent("CONVENTION", ctx, "ruff binary is not on PATH", ["ruff", "check"], "ruff")
    command = [ruff, "check", "--output-format=json", *files]
    proc = run_process(command, ctx=ctx, cwd=worktree, timeout=180)
    version = tool_version("ruff", ctx=ctx, cwd=worktree)
    if proc["error"]:
        return _tool_failure("CONVENTION", ctx, proc, "ruff", version, proc["error"])
    doc, parse_error = _json_from_output(proc["stdout"])
    if parse_error:
        return _tool_failure(
            "CONVENTION", ctx, proc, "ruff", version,
            f"ruff output was not valid JSON: {parse_error}",
        )
    violations = doc if isinstance(doc, list) else []
    result = "PASS" if proc["exit"] == 0 and not violations else "FAIL"
    return make_proof(
        "CONVENTION", ctx, result=result, command=command, tool_name="ruff",
        version=version, exit_code=proc["exit"], stdout=proc["stdout"],
        detail=f"ruff checked {len(files)} changed Python file(s); {len(violations)} violation(s)",
    )


_SECRET_PATTERNS = [
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b")),
    (
        "credential-assignment",
        re.compile(
            r"""(?i)\b(?:api[_-]?key|client[_-]?secret|password|passwd|access[_-]?token|secret[_-]?key)\b"""
            r"""\s*[:=]\s*(['"])(?![<${])(?!(?:changeme|example|dummy|placeholder|your[_ -])\b)[^'"\s]{16,}\1"""
        ),
    ),
]


def _baseline_allows(baseline: dict, path: str, text: str, rule: str) -> bool:
    for entry in baseline.get("allowlist", []) or []:
        if not isinstance(entry, dict):
            continue
        ep = str(entry.get("path") or entry.get("file") or "")
        if ep and not glob_match(path, ep):
            continue
        erule = str(entry.get("rule") or entry.get("id") or "")
        if erule and erule not in {rule, "*"}:
            continue
        match = entry.get("regex") or entry.get("pattern")
        if match:
            try:
                if re.search(str(match), text):
                    return True
            except re.error:
                continue
    return False


def check_secrets(ctx: dict) -> dict:
    diff, problem = _diff_text("SECRETS", ctx, 0)
    if problem:
        return problem
    added = _added_lines(diff)
    if not added:
        return _skip("SECRETS", ctx, "no added lines to scan")
    baseline, error = read_json(policy_path(ctx, "secrets-baseline.json"))
    if error:
        return make_proof("SECRETS", ctx, result="FAIL", detail=error, stdout=error)

    gitleaks = executable("gitleaks")
    if gitleaks:
        with tempfile.TemporaryDirectory(prefix="factory-gitleaks-") as tmp:
            root = Path(tmp) / "source"
            root.mkdir()
            by_path: dict[str, list[str]] = {}
            for item in added:
                safe = PurePosixPath(item["path"])
                if safe.is_absolute() or ".." in safe.parts:
                    continue
                by_path.setdefault(safe.as_posix(), []).append(item["text"])
            for relative, lines in sorted(by_path.items()):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("\n".join(lines) + "\n", encoding="utf-8")
            report_path = Path(tmp) / "gitleaks.json"
            command = [
                gitleaks, "detect", "--no-git", "--source", str(root),
                "--report-format", "json", "--report-path", str(report_path), "--redact",
            ]
            proc = run_process(command, ctx=ctx, cwd=worktree_path(ctx), timeout=120)
            version = tool_version("gitleaks", ctx=ctx, cwd=worktree_path(ctx))
            if proc["error"]:
                return _tool_failure("SECRETS", ctx, proc, "gitleaks", version, proc["error"])
            if proc["exit"] not in (0, 1):
                return _tool_failure(
                    "SECRETS", ctx, proc, "gitleaks", version,
                    f"gitleaks exited {proc['exit']} before producing a trustworthy scan result",
                )
            try:
                findings = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else []
            except (OSError, json.JSONDecodeError) as exc:
                return _tool_failure("SECRETS", ctx, proc, "gitleaks", version, f"invalid report: {exc}")
            if not isinstance(findings, list):
                return _tool_failure("SECRETS", ctx, proc, "gitleaks", version, "gitleaks report is not a JSON list")
            if proc["exit"] == 1 and not findings:
                return _tool_failure(
                    "SECRETS", ctx, proc, "gitleaks", version,
                    "gitleaks reported a finding/error exit but its JSON report was empty",
                )
            unbaselined = []
            for finding in findings:
                if not isinstance(finding, dict):
                    unbaselined.append({"rule": "unknown"})
                    continue
                path = str(finding.get("File") or finding.get("file") or "")
                rule = str(finding.get("RuleID") or finding.get("rule") or "")
                secret = str(finding.get("Secret") or "")
                fp = str(finding.get("Fingerprint") or finding.get("fingerprint") or "")
                known = any(
                    isinstance(hit, dict)
                    and (
                        (hit.get("fingerprint") and hit["fingerprint"] == fp)
                        or (hit.get("sha256") and hit["sha256"] == sha256(secret))
                    )
                    for hit in baseline.get("hits", []) or []
                )
                if not known and not _baseline_allows(baseline, path, secret, rule):
                    unbaselined.append(
                        {"path": path, "rule": rule, "line": finding.get("StartLine") or finding.get("line")}
                    )
            return make_proof(
                "SECRETS", ctx, result="PASS" if not unbaselined else "FAIL",
                command=command, tool_name="gitleaks", version=version,
                exit_code=0 if not unbaselined else 1,
                stdout=canonical_json(unbaselined),
                detail=f"gitleaks scanned added-line snapshots; {len(unbaselined)} unbaselined finding(s)",
            )

    findings = []
    for item in added:
        for rule, pattern in _SECRET_PATTERNS:
            if pattern.search(item["text"]) and not _baseline_allows(
                baseline, item["path"], item["text"], rule
            ):
                findings.append({"path": item["path"], "line": item["line"], "rule": rule})
    return make_proof(
        "SECRETS", ctx, result="FAIL" if findings else "PASS",
        command=["factory-regex-secret-scan", "added-lines"],
        tool_name="factory-regex-secret-scan", version=CHECK_RUNNER_VERSION,
        exit_code=1 if findings else 0, stdout=canonical_json(findings),
        detail=(
            f"gitleaks is absent; high-signal added-line scan found {len(findings)} hit(s)"
            if findings else "gitleaks is absent; high-signal added-line scan found no hits"
        ),
    )


def check_smell(ctx: dict) -> dict:
    diff, problem = _diff_text("SMELL", ctx, 0)
    if problem:
        return problem
    if not _added_lines(diff) and not diff.strip():
        return _skip("SMELL", ctx, "no diff to analyze")
    script = _tool_script(ctx, "smell.py")
    if not script.is_file():
        return _skip("SMELL", ctx, f"copied tool is not present: {script}")
    command = [
        os.fspath(__import__("sys").executable), str(script),
        "--diff", "-", "--repo", str(repo_path(ctx)),
    ]
    if ctx.get("head"):
        command.extend(["--head", str(ctx["head"])])
    proc = run_process(
        command, ctx=ctx, cwd=worktree_path(ctx), timeout=120, input_text=diff,
    )
    version = _script_version(script)
    if proc["error"]:
        return _tool_failure("SMELL", ctx, proc, "smell.py", version, proc["error"])
    doc, parse_error = _json_from_output(proc["stdout"])
    if parse_error or not isinstance(doc, dict):
        return _tool_failure("SMELL", ctx, proc, "smell.py", version, f"tool emitted invalid JSON: {parse_error}")
    findings = doc.get("findings") or []
    result = "PASS" if proc["exit"] == 0 and not findings else "FAIL"
    return make_proof(
        "SMELL", ctx, result=result, command=command, tool_name="smell.py",
        version=version, exit_code=proc["exit"], stdout=proc["stdout"],
        detail=f"smell analyzer scanned {doc.get('files_scanned', 0)} file(s); {len(findings)} finding(s)",
    )


def check_arch(ctx: dict) -> dict:
    diff, problem = _diff_text("ARCH", ctx, 0)
    if problem:
        return problem
    if not diff.strip():
        return _skip("ARCH", ctx, "no diff to analyze")
    script = _tool_script(ctx, "arch_check.py")
    if not script.is_file():
        return _skip("ARCH", ctx, f"copied tool is not present: {script}")
    layers = policy_path(ctx, "layers.json")
    if not layers.is_file():
        return _skip("ARCH", ctx, f"architecture layer policy is not present: {layers}")
    command = [
        os.fspath(__import__("sys").executable), str(script),
        "--repo", str(repo_path(ctx)), "--base", str(ctx.get("base")),
        "--head", str(ctx.get("head")), "--layers", str(layers),
    ]
    env = ctx.get("env") if isinstance(ctx.get("env"), dict) else {}
    allow = env.get("FACTORY_ARCHITECTURE_ALLOW")
    if allow:
        if isinstance(allow, (dict, list)):
            allow = canonical_json(allow)
        command.extend(["--allow", str(allow)])
    proc = run_process(command, ctx=ctx, cwd=worktree_path(ctx), timeout=120)
    version = _script_version(script)
    if proc["error"]:
        return _tool_failure("ARCH", ctx, proc, "arch_check.py", version, proc["error"])
    doc, parse_error = _json_from_output(proc["stdout"])
    if parse_error or not isinstance(doc, dict):
        return _tool_failure("ARCH", ctx, proc, "arch_check.py", version, f"tool emitted invalid JSON: {parse_error}")
    findings = doc.get("findings") or []
    result = "PASS" if proc["exit"] == 0 and not findings else "FAIL"
    return make_proof(
        "ARCH", ctx, result=result, command=command, tool_name="arch_check.py",
        version=version, exit_code=proc["exit"], stdout=proc["stdout"],
        detail=f"architecture analyzer scanned {doc.get('files_scanned', 0)} file(s); {len(findings)} finding(s)",
    )


def _run_diff_tool(check: str, ctx: dict, script_name: str, args: list[str]) -> dict:
    diff, problem = _diff_text(check, ctx, 0)
    if problem:
        return problem
    if not diff.strip():
        return _skip(check, ctx, "no diff to analyze")
    script = _tool_script(ctx, script_name)
    if not script.is_file():
        return _skip(check, ctx, f"copied tool is not present: {script}")
    command = [os.fspath(__import__("sys").executable), str(script), *args]
    if script_name == "oracle_provenance.py":
        command.extend(["--diff", "-", "--repo", str(repo_path(ctx))])
        if ctx.get("head"):
            command.extend(["--head", str(ctx["head"])])
        input_text = diff
    elif script_name == "guard_delta.py":
        command.extend([
            "--repo", str(repo_path(ctx)),
            "--base", str(ctx.get("base")), "--head", str(ctx.get("head")),
        ])
        risk_map = policy_path(ctx, "risk-map.json")
        if risk_map.is_file():
            command.extend(["--risk-map", str(risk_map)])
        input_text = None
    else:
        return _skip(check, ctx, f"{script_name} has no recognized CLI integration")
    proc = run_process(
        command, ctx=ctx, cwd=worktree_path(ctx), timeout=120, input_text=input_text,
    )
    version = _script_version(script)
    if proc["error"]:
        return _tool_failure(check, ctx, proc, script_name, version, proc["error"])
    doc, parse_error = _json_from_output(proc["stdout"])
    if parse_error or not isinstance(doc, dict):
        return _tool_failure(check, ctx, proc, script_name, version, f"tool emitted invalid JSON: {parse_error}")
    findings = doc.get("findings") or []
    result = "PASS" if proc["exit"] == 0 and not findings else "FAIL"
    return make_proof(
        check, ctx, result=result, command=command, tool_name=script_name,
        version=version, exit_code=proc["exit"], stdout=proc["stdout"],
        detail=f"{script_name} reported {len(findings)} finding(s)",
    )


def check_oracle_provenance(ctx: dict) -> dict:
    return _run_diff_tool("ORACLE_PROVENANCE", ctx, "oracle_provenance.py", [])


def check_guard_delta(ctx: dict) -> dict:
    return _run_diff_tool("GUARD_DELTA", ctx, "guard_delta.py", [])


def check_authz_matrix(ctx: dict) -> dict:
    script = _tool_script(ctx, "authz_matrix.py")
    if not script.is_file():
        return _skip("AUTHZ_MATRIX", ctx, f"copied tool is not present: {script}")
    root = worktree_path(ctx)
    if root is None:
        return make_proof("AUTHZ_MATRIX", ctx, result="FAIL", detail="ctx.worktree is required")
    with tempfile.TemporaryDirectory(prefix="factory-authz-") as tmp:
        output = Path(tmp) / "matrix.json"
        python = os.fspath(__import__("sys").executable)
        for candidate in (
            root / "backend" / "venv" / "bin" / "python",
            root / "venv" / "bin" / "python",
        ):
            if candidate.is_file():
                python = str(candidate)
                break
        command = [
            python, str(script),
            "--repo", str(root), "--out", str(output), "--summary",
        ]
        proc = run_process(command, ctx=ctx, cwd=root, timeout=180)
        version = _script_version(script)
        matrix = None
        if output.exists():
            try:
                matrix = json.loads(output.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                matrix = None
    if proc["error"]:
        return _tool_failure("AUTHZ_MATRIX", ctx, proc, "authz_matrix.py", version, proc["error"])
    if proc["exit"] == 2 and (
        "No module named 'django'" in proc["stderr"]
        or 'No module named "django"' in proc["stderr"]
        or "No module named 'rest_framework'" in proc["stderr"]
    ):
        return _absent(
            "AUTHZ_MATRIX", ctx,
            "authz_matrix CLI is present but its Django/DRF runtime dependency is unavailable",
            command, "authz_matrix.py",
        )
    if proc["exit"] == 2 and (
        "neither a Django repo" in proc["stderr"]
        or "control fixture" in proc["stderr"]
    ):
        return _skip(
            "AUTHZ_MATRIX", ctx,
            "not a Django/DRF repo; authorization matrix is not applicable",
            command, "authz_matrix.py",
        )
    if not isinstance(matrix, dict):
        return _tool_failure("AUTHZ_MATRIX", ctx, proc, "authz_matrix.py", version, "tool emitted no valid matrix JSON")
    findings = matrix.get("findings") or []
    result = "PASS" if proc["exit"] == 0 and matrix.get("result") == "PASS" else "FAIL"
    evidence = canonical_json(matrix)
    return make_proof(
        "AUTHZ_MATRIX", ctx, result=result, command=command, tool_name="authz_matrix.py",
        version=version, exit_code=proc["exit"], stdout=evidence,
        detail=f"static URL/method authorization matrix: {len(findings)} finding(s); runtime authz is not exercised",
    )


def check_semgrep(ctx: dict) -> dict:
    paths, problem = _diff_paths("SEMGREP", ctx)
    if problem:
        return problem
    worktree = worktree_path(ctx)
    if worktree is None:
        return make_proof("SEMGREP", ctx, result="FAIL", detail="ctx.worktree is required")
    files = [p for p in paths if (worktree / p).is_file()]
    config = policy_path(ctx, "semgrep.yml")
    if not files:
        return _skip("SEMGREP", ctx, "no changed files remain at head")
    if not config.is_file():
        return make_proof("SEMGREP", ctx, result="FAIL", detail=f"ruleset not found: {config}")
    binary = executable("semgrep")
    if not binary:
        return _absent("SEMGREP", ctx, "semgrep binary is not on PATH", ["semgrep", "--config", str(config), "--json"], "semgrep")
    command = [binary, "--config", str(config), "--json", *files]
    proc = run_process(command, ctx=ctx, cwd=worktree, timeout=240)
    version = tool_version("semgrep", ctx=ctx, cwd=worktree)
    if proc["error"]:
        return _tool_failure("SEMGREP", ctx, proc, "semgrep", version, proc["error"])
    doc, parse_error = _json_from_output(proc["stdout"])
    if parse_error or not isinstance(doc, dict):
        return _tool_failure("SEMGREP", ctx, proc, "semgrep", version, f"invalid JSON output: {parse_error}")
    findings = doc.get("results") or []
    result = "PASS" if proc["exit"] == 0 and not findings else "FAIL"
    return make_proof(
        "SEMGREP", ctx, result=result, command=command, tool_name="semgrep",
        version=version, exit_code=proc["exit"], stdout=proc["stdout"],
        detail=f"semgrep scanned {len(files)} changed file(s); {len(findings)} finding(s)",
    )


def _product_python(ctx: dict) -> str | None:
    """Resolve the product interpreter that owns the lane's dependencies."""
    root = worktree_path(ctx) or repo_path(ctx)
    if root is None:
        return None
    for parts in (
        ("backend", "venv", "bin", "python"),
        ("backend", ".venv", "bin", "python"),
        (".venv", "bin", "python"),
    ):
        candidate = root.joinpath(*parts)
        if candidate.is_file():
            return os.fspath(candidate)
    return None


def _module_prefix(
    ctx: dict, python: str | None, module: str,
) -> tuple[list[str] | None, str | None]:
    """Prefer ``<product python> -m <module>``; fall back to a PATH binary.

    Returns the argv prefix to use and the version string when the module was
    resolved through the product interpreter.
    """
    if python:
        probe = run_process(
            [python, "-m", module, "--version"], ctx=ctx,
            cwd=worktree_path(ctx), timeout=30,
        )
        if probe["exit"] == 0:
            text = (probe["stdout"] or probe["stderr"]).strip().splitlines()
            version = text[0][:160] if text else "version query unavailable"
            return [python, "-m", module], version
    binary = executable(module)
    if not binary:
        return None, None
    return [binary], None


def check_radon(ctx: dict) -> dict:
    paths, problem = _diff_paths("RADON", ctx)
    if problem:
        return problem
    worktree = worktree_path(ctx)
    if worktree is None:
        return make_proof("RADON", ctx, result="FAIL", detail="ctx.worktree is required")
    files = _python_changed(paths, worktree)
    if not files:
        return _skip("RADON", ctx, "no changed non-test Python source files")
    prefix, module_version = _module_prefix(ctx, _product_python(ctx), "radon")
    if prefix is None:
        return _absent(
            "RADON", ctx,
            "radon is unavailable: no product venv module and no radon binary on PATH",
            ["radon", "cc", "-s", "-j", "--min", "C"], "radon",
        )
    command = [*prefix, "cc", "-s", "-j", "--min", "C", *files]
    proc = run_process(command, ctx=ctx, cwd=worktree, timeout=180)
    version = module_version or tool_version("radon", ctx=ctx, cwd=worktree)
    if proc["error"]:
        return _tool_failure("RADON", ctx, proc, "radon", version, proc["error"])
    doc, parse_error = _json_from_output(proc["stdout"])
    if parse_error or not isinstance(doc, dict):
        return _tool_failure("RADON", ctx, proc, "radon", version, f"invalid JSON output: {parse_error}")
    findings = []
    for path, blocks in doc.items():
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if isinstance(block, dict) and str(block.get("rank", "")).upper() in {"C", "D", "E", "F"}:
                findings.append({"path": path, "name": block.get("name"), "complexity": block.get("complexity"), "rank": block.get("rank")})
    return make_proof(
        "RADON", ctx, result="FAIL" if findings else "PASS",
        command=command, tool_name="radon", version=version,
        exit_code=1 if findings else 0, stdout=canonical_json(doc),
        detail=f"radon scanned {len(files)} changed non-test file(s); {len(findings)} rank-C-or-worse block(s)",
    )


def check_bandit(ctx: dict) -> dict:
    paths, problem = _diff_paths("BANDIT", ctx)
    if problem:
        return problem
    worktree = worktree_path(ctx)
    if worktree is None:
        return make_proof("BANDIT", ctx, result="FAIL", detail="ctx.worktree is required")
    files = _python_changed(paths, worktree)
    if not files:
        return _skip("BANDIT", ctx, "no changed non-test Python source files")
    prefix, module_version = _module_prefix(ctx, _product_python(ctx), "bandit")
    if prefix is None:
        return _absent(
            "BANDIT", ctx,
            "bandit is unavailable: no product venv module and no bandit binary on PATH",
            ["bandit", "-f", "json", "-q"], "bandit",
        )
    command = [*prefix, "-r", "-f", "json", "-q", *files]
    proc = run_process(command, ctx=ctx, cwd=worktree, timeout=180)
    version = module_version or tool_version("bandit", ctx=ctx, cwd=worktree)
    if proc["error"]:
        return _tool_failure("BANDIT", ctx, proc, "bandit", version, proc["error"])
    doc, parse_error = _json_from_output(proc["stdout"])
    if parse_error or not isinstance(doc, dict):
        return _tool_failure("BANDIT", ctx, proc, "bandit", version, f"invalid JSON output: {parse_error}")
    findings = [
        issue for issue in (doc.get("results") or [])
        if isinstance(issue, dict)
        and str(issue.get("issue_severity", "")).upper() in {"MEDIUM", "HIGH"}
    ]
    return make_proof(
        "BANDIT", ctx, result="FAIL" if findings else "PASS",
        command=command, tool_name="bandit", version=version,
        exit_code=1 if findings else 0, stdout=proc["stdout"],
        detail=f"bandit scanned {len(files)} changed non-test file(s); {len(findings)} MEDIUM/HIGH issue(s)",
    )


def _materialize_workflows(ctx: dict, ref: str, dest: Path) -> tuple[list[str] | None, dict | None]:
    """Extract ``.github/workflows`` at ``ref`` into ``dest`` safely.

    Returns the POSIX paths of the extracted workflow files (empty when the
    revision has no workflows) and, on failure, an error descriptor for the
    caller's evidence proof.
    """
    root = repo_path(ctx)
    if root is None:
        return None, {"command": [], "exit": None, "stdout": "", "detail": "repo_root is required"}
    listing_command = [
        "git", "-C", str(root), "ls-tree", "-r", "--name-only", str(ref), "--", ".github/workflows",
    ]
    listed = run_process(listing_command, ctx=ctx, cwd=root, timeout=45)
    if listed["error"] or listed["exit"] != 0:
        return None, {
            "command": listing_command,
            "exit": listed["exit"],
            "stdout": listed["stdout"] + listed["stderr"],
            "detail": (
                f"git ls-tree failed while listing workflows at {ref}: "
                f"{listed['error'] or listed['stderr'].strip()}"
            ),
        }
    names = sorted({line.strip() for line in listed["stdout"].splitlines() if line.strip()})
    if not names:
        return [], None
    archive_command = ["git", "-C", str(root), "archive", str(ref), ".github/workflows"]
    try:
        archived = subprocess.run(
            archive_command, cwd=str(root), capture_output=True, timeout=90, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, {
            "command": archive_command, "exit": None, "stdout": "",
            "detail": f"cannot archive workflows at {ref}: {exc}",
        }
    if archived.returncode != 0:
        return None, {
            "command": archive_command, "exit": archived.returncode,
            "stdout": archived.stderr.decode("utf-8", "replace"),
            "detail": f"git archive failed while materializing workflows at {ref}",
        }
    try:
        with tarfile.open(fileobj=__import__("io").BytesIO(archived.stdout), mode="r:") as tf:
            target_root = dest.resolve()
            for member in tf.getmembers():
                member_path = (target_root / member.name).resolve()
                if target_root not in member_path.parents and member_path != target_root:
                    return None, {
                        "command": archive_command, "exit": 1, "stdout": member.name,
                        "detail": f"unsafe path in workflow archive at {ref}: {member.name}",
                    }
                if member.isdir():
                    member_path.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    member_path.parent.mkdir(parents=True, exist_ok=True)
                    source = tf.extractfile(member)
                    if source is not None:
                        member_path.write_bytes(source.read())
                else:
                    return None, {
                        "command": archive_command, "exit": 1, "stdout": member.name,
                        "detail": f"non-regular entry in workflow archive at {ref}: {member.name}",
                    }
    except (OSError, tarfile.TarError) as exc:
        return None, {
            "command": archive_command, "exit": None, "stdout": "",
            "detail": f"cannot unpack workflows at {ref}: {exc}",
        }
    files = sorted(
        PurePosixPath(name).as_posix()
        for name in names
        if (dest / name).is_file()
    )
    return files, None


def _zizmor_fingerprints(tree: Path, findings: list) -> list[dict]:
    """Normalize findings so base and head scans are comparable.

    One fingerprint per finding, anchored at the location zizmor marks
    ``Primary`` (falling back to the first location) so that related or hidden
    annotations do not multiply a single finding.
    """
    tree_root = tree.resolve()
    normalized: dict[str, dict] = {}
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        rule = str(finding.get("ident") or finding.get("rule") or "unknown")
        message_sha256 = sha256(str(finding.get("desc") or ""))
        locations = finding.get("locations")
        if not isinstance(locations, list):
            locations = []
        chosen = next(
            (
                location for location in locations
                if isinstance(location, dict)
                and isinstance(location.get("symbolic"), dict)
                and location["symbolic"].get("kind") == "Primary"
            ),
            locations[0] if locations else {},
        )
        path = ""
        line = 0
        if isinstance(chosen, dict):
            symbolic = chosen.get("symbolic")
            key = symbolic.get("key") if isinstance(symbolic, dict) else None
            local = key.get("Local") if isinstance(key, dict) else None
            verbatim = local.get("verbatim_path") if isinstance(local, dict) else None
            if verbatim:
                raw = Path(str(verbatim))
                if raw.is_absolute():
                    try:
                        path = raw.resolve().relative_to(tree_root).as_posix()
                    except (OSError, ValueError):
                        path = PurePosixPath(str(verbatim).replace("\\", "/")).as_posix()
                else:
                    path = PurePosixPath(str(verbatim).replace("\\", "/")).as_posix()
                while path.startswith("./"):
                    path = path[2:]
            concrete = chosen.get("concrete")
            loc = concrete.get("location") if isinstance(concrete, dict) else None
            start = loc.get("start_point") if isinstance(loc, dict) else None
            if isinstance(start, dict) and isinstance(start.get("row"), int):
                line = start["row"]
        fingerprint = {
            "rule": rule, "path": path, "line": line, "message_sha256": message_sha256,
        }
        normalized[canonical_json(fingerprint)] = fingerprint
    return [normalized[key] for key in sorted(normalized)]


def _scan_workflows(
    ctx: dict, binary: str, tree: Path, files: list[str], label: str,
) -> tuple[list[dict] | None, dict | None]:
    """Run zizmor over one materialized workflow set."""
    if not files:
        return [], None
    command = [binary, "--format", "json", *files]
    proc = run_process(command, ctx=ctx, cwd=tree, timeout=240)
    if proc["error"]:
        return None, {"kind": "error", "command": command, "proc": proc}
    parsed, parse_error = _json_from_output(proc["stdout"])
    if parse_error is not None:
        return None, {"kind": "parse", "command": command, "proc": proc, "error": parse_error}
    if isinstance(parsed, dict):
        raw = parsed.get("findings") or parsed.get("results") or []
    elif isinstance(parsed, list):
        raw = parsed
    else:
        return None, {
            "kind": "parse", "command": command, "proc": proc,
            "error": f"{label} zizmor output is not a JSON list or object",
        }
    if not isinstance(raw, list):
        return None, {
            "kind": "parse", "command": command, "proc": proc,
            "error": f"{label} zizmor findings payload is not a list",
        }
    # zizmor 1.x exits nonzero (a bitflag) when findings exist, so a valid JSON
    # payload is authoritative; a nonzero exit with no payload is an error.
    if proc["exit"] not in (0,) and not raw:
        return None, {"kind": "exit", "command": command, "proc": proc}
    return _zizmor_fingerprints(tree, raw), None


def check_zizmor(ctx: dict) -> dict:
    paths, problem = _diff_paths("ZIZMOR", ctx)
    if problem:
        return problem
    worktree = worktree_path(ctx)
    if worktree is None:
        return make_proof("ZIZMOR", ctx, result="FAIL", detail="ctx.worktree is required")
    base, head = ctx.get("base"), ctx.get("head")
    if not base or not head:
        return make_proof(
            "ZIZMOR", ctx, result="FAIL",
            detail="ctx.base and ctx.head are required for the base/head workflow comparison",
        )
    workflow_changes = [
        p for p in paths if p.startswith(".github/workflows/") and (worktree / p).is_file()
    ]
    binary = executable("zizmor")
    if not binary:
        return _absent("ZIZMOR", ctx, "zizmor binary is not on PATH", ["zizmor", "--format", "json"], "zizmor")
    version = tool_version("zizmor", ctx=ctx, cwd=worktree)
    scans: dict[str, list[dict]] = {}
    with tempfile.TemporaryDirectory(prefix="factory-zizmor-base-") as base_tmp, \
            tempfile.TemporaryDirectory(prefix="factory-zizmor-head-") as head_tmp:
        base_tree = Path(base_tmp)
        head_tree = Path(head_tmp)
        base_files, base_error = _materialize_workflows(ctx, base, base_tree)
        if base_error:
            return make_proof(
                "ZIZMOR", ctx, result="FAIL", command=base_error["command"], tool_name="git",
                version=tool_version("git", ctx=ctx), exit_code=base_error["exit"],
                stdout=base_error["stdout"], detail=base_error["detail"],
            )
        head_files, head_error = _materialize_workflows(ctx, head, head_tree)
        if head_error:
            return make_proof(
                "ZIZMOR", ctx, result="FAIL", command=head_error["command"], tool_name="git",
                version=tool_version("git", ctx=ctx), exit_code=head_error["exit"],
                stdout=head_error["stdout"], detail=head_error["detail"],
            )
        if not head_files:
            reason = "no .github/workflows exists at head; nothing to scan"
            return make_proof(
                "ZIZMOR", ctx, result="SKIP", tool_name="zizmor", version=version,
                stdout=canonical_json({"skip_reason": reason}), detail=reason,
            )
        for label, tree, files in (
            ("base", base_tree, base_files),
            ("head", head_tree, head_files),
        ):
            fingerprints, scan_error = _scan_workflows(ctx, binary, tree, files, label)
            if scan_error:
                proc = scan_error["proc"]
                if scan_error["kind"] == "error":
                    return _tool_failure("ZIZMOR", ctx, proc, "zizmor", version, proc["error"])
                if scan_error["kind"] == "parse":
                    return _tool_failure(
                        "ZIZMOR", ctx, proc, "zizmor", version,
                        f"expected JSON output from {label} scan: {scan_error['error']}",
                    )
                return make_proof(
                    "ZIZMOR", ctx, result="FAIL", command=scan_error["command"],
                    tool_name="zizmor", version=version, exit_code=proc["exit"],
                    stdout=proc["stdout"] + proc["stderr"],
                    detail=(
                        f"zizmor exited {proc['exit']} on the {label} workflow scan "
                        "without reporting a JSON payload"
                    ),
                )
            scans[label] = fingerprints
    base_findings = scans.get("base", [])
    head_findings = scans.get("head", [])
    base_keys = {canonical_json(item) for item in base_findings}
    new_findings = [item for item in head_findings if canonical_json(item) not in base_keys]
    residual_findings = [item for item in head_findings if canonical_json(item) in base_keys]
    result = "FAIL" if new_findings else "PASS"

    def _labels(items: list[dict], limit: int = 10) -> str:
        names = [
            f"{item['rule']}@{item['path']}:{item['line']}"
            for item in items[:limit]
        ]
        text = ", ".join(names)
        if len(items) > limit:
            text += f" (+{len(items) - limit} more)"
        return text

    if new_findings:
        detail = f"{len(new_findings)} new finding(s): {_labels(new_findings)}"
    else:
        detail = f"no new findings; {len(residual_findings)} pre-existing residual"
        if residual_findings:
            detail += f": {_labels(residual_findings)}"
        if not workflow_changes:
            detail += " (no workflow changes at head)"
    if len(detail) > 1900:
        detail = detail[:1880] + "..."
    evidence_text = canonical_json({
        "base_findings": base_findings,
        "head_findings": head_findings,
        "new_findings": new_findings,
        "residual_findings": residual_findings,
        "workflow_changes": workflow_changes,
    })
    artifacts: list[dict] = []
    task_id = ctx.get("task_id")
    if task_id:
        try:
            artifact_path = evidence_dir(str(task_id)) / "artifacts" / "ZIZMOR.json"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(evidence_text, encoding="utf-8")
            artifacts = [{
                "path": str(artifact_path),
                "sha256": sha256(evidence_text),
                "kind": "zizmor-fingerprints",
            }]
        except (FactoryError, OSError) as exc:
            detail = (detail + f"; artifact write failed: {exc}")[:1900]
    return make_proof(
        "ZIZMOR", ctx, result=result, tool_name="zizmor", version=version,
        exit_code=1 if new_findings else 0,
        stdout=evidence_text,
        artifacts=artifacts,
        detail=detail,
    )


def _digest_dir(root: Path) -> tuple[dict[str, str], str]:
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            files[f"policies/{relative}"] = sha256(path.read_bytes())
    return files, sha256(canonical_json(files))


def _git_policy_digest(ctx: dict, ref: str) -> tuple[dict[str, str] | None, str | None]:
    root = repo_path(ctx)
    if root is None:
        return None, "repo_root is required"
    listing = run_process(
        ["git", "-C", str(root), "ls-tree", "-r", "--name-only", "-z", ref, "--", "policies"],
        ctx=ctx, cwd=root, timeout=45,
    )
    if listing["error"] or listing["exit"] != 0:
        return None, listing["error"] or listing["stderr"].strip() or "git ls-tree failed"
    result = {}
    for name in sorted(item for item in listing["stdout"].split("\0") if item):
        try:
            blob = subprocess.run(
                ["git", "-C", str(root), "show", f"{ref}:{name}"],
                cwd=root, capture_output=True, timeout=30, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return None, f"cannot read {name} at {ref}: {exc}"
        if blob.returncode != 0:
            return None, f"cannot read {name} at {ref}: {blob.stderr.decode('utf-8', 'replace').strip()}"
        result[name] = sha256(blob.stdout)
    return result, None


def check_policy_digest(ctx: dict) -> dict:
    root = ctx_path(ctx, "policies_dir")
    if root is None:
        repo = repo_path(ctx)
        root = repo / "policies" if repo else None
    if root is None or not root.is_dir():
        return make_proof("POLICY_DIGEST", ctx, result="FAIL", detail="policies_dir is missing or not a directory")
    files, digest = _digest_dir(root)
    env = ctx.get("env") if isinstance(ctx.get("env"), dict) else {}
    expected = env.get("FACTORY_POLICY_DIGEST") or env.get("policy_digest")
    mismatches = []
    if expected and str(expected) != digest:
        mismatches.append(f"expected policy digest {expected} does not match computed {digest}")
    base_files = None
    base_error = None
    repo = repo_path(ctx)
    policies_are_repository_policies = False
    if repo is not None:
        try:
            policies_are_repository_policies = root.resolve() == (repo / "policies").resolve()
        except OSError:
            policies_are_repository_policies = False
    if ctx.get("base") and policies_are_repository_policies:
        base_files, base_error = _git_policy_digest(ctx, str(ctx["base"]))
    changed = base_files is not None and base_files != files
    control = env.get("gate_control")
    record_paths = []
    control_path = env.get("gate_control_record")
    if control_path:
        record_paths.append(Path(str(control_path)).expanduser())
    worktree = worktree_path(ctx)
    if worktree:
        record_paths.extend(
            [
                worktree / "evidence" / "gate-control.json",
                worktree / ".factory" / "gate-control.json",
            ]
        )
    if changed and not isinstance(control, dict):
        for path in record_paths:
            if path.is_file():
                control, _ = read_json(path)
                if isinstance(control, dict):
                    break
    if changed:
        if not isinstance(control, dict):
            mismatches.append("policy bytes changed since base but no gate-control record was supplied")
        elif str(control.get("policy_digest") or "") != digest:
            mismatches.append("gate-control record policy_digest does not match current policies")
    detail_data = {
        "policy_digest": digest,
        "files": files,
        "changed_since_base": changed,
        "base_digest_error": base_error,
        "mismatches": mismatches,
    }
    return make_proof(
        "POLICY_DIGEST", ctx, result="FAIL" if mismatches else "PASS",
        command=["sha256", "canonical-json", "policies/**"],
        tool_name="factory-policy-digest", version=CHECK_RUNNER_VERSION,
        exit_code=1 if mismatches else 0, stdout=canonical_json(detail_data),
        detail=(
            "; ".join(mismatches) if mismatches else
            f"computed policy digest {digest} over {len(files)} file(s)"
            + (" (unchanged from base)" if base_files is not None and not changed else "")
        ),
    )


def check_gate_controls(ctx: dict) -> dict:
    checks, checks_error = read_json(policy_path(ctx, "checks.json"))
    gate, gate_error = read_json(policy_path(ctx, "gate.json"))
    errors = []
    if checks_error:
        errors.append(checks_error)
    if gate_error:
        errors.append(gate_error)
    if errors:
        return make_proof("GATE_CONTROLS", ctx, result="FAIL", detail="; ".join(errors), stdout=canonical_json(errors))
    if not isinstance(checks, dict) or not isinstance(gate, dict):
        return make_proof(
            "GATE_CONTROLS", ctx, result="FAIL",
            detail="checks.json and gate.json must both contain JSON objects",
            stdout=canonical_json({"checks": type(checks).__name__, "gate": type(gate).__name__}),
        )
    checks_map = checks.get("checks")
    if not isinstance(checks_map, dict):
        return make_proof(
            "GATE_CONTROLS", ctx, result="FAIL",
            detail="checks.json.checks must be a JSON object",
            stdout=canonical_json(checks),
        )
    defined = set(checks_map)
    declared = gate.get("gate_checks", [])
    declared_strings = [item for item in declared if isinstance(item, str)] if isinstance(declared, list) else []
    if isinstance(declared, list) and len(declared_strings) != len(declared):
        errors.append("gate.json.gate_checks must contain only strings")
    declared_set = set(declared_strings)
    duplicates = sorted(item for item in declared_set if declared.count(item) > 1)
    unknown = sorted(declared_set - defined)
    errors.extend(
        [f"gate.json declares unknown checks: {unknown}"] if unknown else []
    )
    errors.extend([f"duplicate gate check ids: {duplicates}"] if duplicates else [])
    if not isinstance(declared, list):
        errors.append("gate.json.gate_checks must be an array")
    for check_id, entry in checks_map.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("predicate"), str) or not entry.get("predicate"):
            errors.append(f"checks.json entry {check_id} lacks a predicate")
    sets_by_kind = gate.get("gate_sets_by_kind")
    if not isinstance(sets_by_kind, dict):
        errors.append("gate.json.gate_sets_by_kind must be an object")
    else:
        for kind, selected in sorted(sets_by_kind.items()):
            if not isinstance(selected, list):
                errors.append(f"gate_sets_by_kind.{kind} must be an array")
                continue
            selected_strings = [item for item in selected if isinstance(item, str)]
            if len(selected_strings) != len(selected):
                errors.append(f"gate_sets_by_kind.{kind} must contain only strings")
                continue
            if len(selected_strings) != len(set(selected_strings)):
                errors.append(f"gate_sets_by_kind.{kind} contains duplicate check ids")
            out_of_gate = sorted(set(selected_strings) - declared_set)
            if out_of_gate:
                errors.append(f"gate_sets_by_kind.{kind} widens gate_checks: {out_of_gate}")
    result_data = {"defined": sorted(defined), "declared": declared, "errors": errors}
    return make_proof(
        "GATE_CONTROLS", ctx, result="FAIL" if errors else "PASS",
        command=["factory-checks", "validate-gate-policy-pair"],
        tool_name="factory-checks", version=CHECK_RUNNER_VERSION,
        exit_code=1 if errors else 0, stdout=canonical_json(result_data),
        detail="; ".join(errors) if errors else f"{len(defined)} check IDs are consistent across checks.json and gate.json",
    )


def _markdown_cells(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [cell.strip() for cell in value.split("|")]


def _parse_path_cell(value: str) -> list[str]:
    value = re.sub(r"<br\s*/?>", ",", value, flags=re.I)
    parts = []
    for part in value.split(","):
        cleaned = part.strip().strip("`").strip()
        if cleaned:
            parts.append(cleaned)
    return parts


def check_changelog(ctx: dict) -> dict:
    paths, problem = _diff_paths("CHANGELOG", ctx)
    if problem:
        return problem
    cfg, error = read_json(policy_path(ctx, "changelog.json"))
    if error:
        return make_proof("CHANGELOG", ctx, result="FAIL", detail=error, stdout=error)
    exempt = list(cfg.get("exempt_paths") or [])
    required_paths = [
        path for path in paths
        if not any(glob_match(path, pattern) for pattern in exempt)
    ]
    changelog_path = str(cfg.get("file") or "CHANGELOG.md")
    worktree = worktree_path(ctx)
    if worktree is None:
        return make_proof("CHANGELOG", ctx, result="FAIL", detail="ctx.worktree is required")
    diff, diff_problem = _diff_text("CHANGELOG", ctx, 0)
    if diff_problem:
        return diff_problem
    added = _added_lines(diff)
    added_rows = []
    for item in added:
        if item["path"] != changelog_path or not item["text"].strip().startswith("|"):
            continue
        cells = _markdown_cells(item["text"])
        if cells and all(re.fullmatch(r"[:\-\s]+", cell or "-") for cell in cells):
            continue
        if cells and cells[0].strip().lower().strip("`") == "task_id":
            continue
        added_rows.append(item)
    rows = []
    changelog = worktree / changelog_path
    if changelog.is_file():
        lines = changelog.read_text(encoding="utf-8", errors="replace").splitlines()
        header_index = next(
            (i for i, line in enumerate(lines) if "task_id" in line.lower() and "paths" in line.lower()),
            None,
        )
        if header_index is not None:
            headers = [item.strip().lower().strip("`") for item in _markdown_cells(lines[header_index])]
            indexes = {name: headers.index(name) for name in cfg.get("required_columns", []) if name in headers}
            added_line_numbers = {item["line"] for item in added_rows}
            for number, line in enumerate(lines, start=1):
                if number not in added_line_numbers or not line.strip().startswith("|"):
                    continue
                cells = _markdown_cells(line)
                if len(cells) < len(headers):
                    cells += [""] * (len(headers) - len(cells))
                row = {key: cells[index] for key, index in indexes.items() if index < len(cells)}
                rows.append(row)
    missing_columns = sorted(set(cfg.get("required_columns", [])) - (set(rows[0]) if rows else set()))
    errors = []
    if required_paths and not rows:
        errors.append(f"no added changelog row in {changelog_path} for non-exempt changed paths")
    if rows and missing_columns:
        errors.append(f"changelog table is missing required columns: {missing_columns}")
    coverage: dict[str, list[str]] = {}
    for index, row in enumerate(rows):
        prefix = f"row {index + 1}"
        task = row.get("task_id", "").strip()
        date = row.get("date", "").strip()
        invariant = row.get("invariant", "").strip()
        attack = row.get("attack_class", "").strip()
        tier = row.get("tier", "").strip()
        row_paths = _parse_path_cell(row.get("paths", ""))
        if not task:
            errors.append(f"{prefix} has empty task_id")
        elif ctx.get("task_id") and task != str(ctx["task_id"]):
            errors.append(f"{prefix} task_id {task!r} does not match {ctx['task_id']!r}")
        try:
            _datetime.date.fromisoformat(date)
        except ValueError:
            errors.append(f"{prefix} date is not ISO YYYY-MM-DD")
        if len(invariant) < 20 or len(re.findall(r"[.!?](?:\s|$)", invariant)) != 1:
            errors.append(f"{prefix} invariant must be one sentence of at least 20 characters")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]+", attack):
            errors.append(f"{prefix} attack_class is invalid")
        if tier not in {"none", "high", "max"}:
            errors.append(f"{prefix} tier is invalid")
        if ctx.get("tier") and tier and tier != str(ctx["tier"]):
            errors.append(f"{prefix} tier {tier!r} does not match ctx.tier {ctx['tier']!r}")
        if not row_paths:
            errors.append(f"{prefix} has no paths")
        for pattern in row_paths:
            matched = [path for path in paths if glob_match(path, pattern)]
            if not matched:
                errors.append(f"{prefix} path pattern {pattern!r} matches no changed path")
            for path in matched:
                coverage.setdefault(path, []).append(pattern)
    uncovered = sorted(path for path in required_paths if path not in coverage)
    if uncovered:
        errors.append(f"non-exempt changed paths absent from added changelog rows: {uncovered}")
    data = {"changed_paths": paths, "added_rows": rows, "coverage": coverage, "errors": errors}
    return make_proof(
        "CHANGELOG", ctx, result="FAIL" if errors else "PASS",
        command=["git", "-C", str(repo_path(ctx)), "diff", "--unified=0", f"{ctx.get('base')}..{ctx.get('head')}"],
        tool_name="factory-changelog-validator", version=CHECK_RUNNER_VERSION,
        exit_code=1 if errors else 0, stdout=canonical_json(data),
        detail="; ".join(errors) if errors else f"{len(rows)} added changelog row(s) cover {len(required_paths)} non-exempt path(s)",
    )


def check_agt_parity(ctx: dict) -> dict:
    root = worktree_path(ctx)
    if root is None:
        return make_proof("AGT_PARITY", ctx, result="FAIL", detail="ctx.worktree is required")
    path = root / ".agt" / "policy.json"
    if not path.is_file():
        # The product worktrees symlink .agt to the factory; a synthetic or
        # unlinked worktree still validates the factory's own policy.
        path = Path(__file__).resolve().parents[2] / ".agt" / "policy.json"
    policy, error = read_json(path)
    failures = []
    if error:
        failures.append(error)
    elif not isinstance(policy, dict):
        failures.append("AGT policy root must be a JSON object")
    else:
        if not isinstance(policy.get("schemaVersion"), int):
            failures.append("schemaVersion must be an integer")
        if not isinstance(policy.get("version"), int):
            failures.append("version must be an integer")
        if policy.get("mode") not in {"enforce", "audit"}:
            failures.append("mode must be enforce or audit")
        if not isinstance(policy.get("denyOnPolicyError"), bool):
            failures.append("denyOnPolicyError must be boolean")
        tools = policy.get("toolPolicies")
        if not isinstance(tools, dict):
            failures.append("toolPolicies must be an object")
        else:
            for key in ("allowedTools", "blockedTools", "reviewTools"):
                if not isinstance(tools.get(key), list) or any(not isinstance(x, str) for x in tools.get(key, [])):
                    failures.append(f"toolPolicies.{key} must be a string array")
            if tools.get("defaultEffect") not in {"allow", "deny"}:
                failures.append("toolPolicies.defaultEffect must be allow or deny")
        resources = policy.get("directResourcePolicies")
        if not isinstance(resources, dict) or not isinstance(resources.get("pathRules"), list):
            failures.append("directResourcePolicies.pathRules must be an array")
        else:
            for index, rule in enumerate(resources["pathRules"]):
                if not isinstance(rule, dict) or rule.get("effect") not in {"allow", "deny", "review"}:
                    failures.append(f"pathRules[{index}] must be a rule object with a valid effect")
    return make_proof(
        "AGT_PARITY", ctx, result="FAIL" if failures else "PASS",
        command=["factory-checks", "validate-agt-policy-shape", str(path)],
        tool_name="factory-agt-validator", version=CHECK_RUNNER_VERSION,
        exit_code=1 if failures else 0,
        stdout=canonical_json({"path": str(path), "errors": failures}),
        detail="; ".join(failures) if failures else "AGT policy JSON has the required structural shape",
    )


def check_agt_audit(ctx: dict) -> dict:
    env = ctx.get("env") if isinstance(ctx.get("env"), dict) else {}
    path = Path(str(env.get("agt_audit_log") or (Path.home() / ".config/opencode/agt/audit-log.json"))).expanduser()
    if not path.is_file():
        return _skip("AGT_AUDIT", ctx, f"AGT audit log is absent: {path}")
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        parsed = json.loads(raw)
        entries = parsed if isinstance(parsed, list) else parsed.get("entries", []) if isinstance(parsed, dict) else []
    except json.JSONDecodeError:
        entries = []
        for number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as exc:
                return make_proof(
                    "AGT_AUDIT", ctx, result="FAIL",
                    command=["factory-checks", "validate-agt-audit-jsonl", str(path)],
                    stdout=raw, detail=f"invalid audit JSONL at line {number}: {exc}",
                )
    if not isinstance(entries, list):
        return make_proof("AGT_AUDIT", ctx, result="FAIL", stdout=raw, detail="audit log must contain an array or JSONL entries")
    errors = []
    deny_count = 0
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"entry {index} is not an object")
            continue
        decision = str(entry.get("decision") or "").lower()
        if decision == "deny":
            deny_count += 1
            if not entry.get("action"):
                errors.append(f"deny entry {index} has no action")
            if not entry.get("decision"):
                errors.append(f"deny entry {index} has no decision")
    env = ctx.get("env") if isinstance(ctx.get("env"), dict) else {}
    golden = env.get("agt_golden_expectations")
    if golden:
        if isinstance(golden, str):
            try:
                golden = json.loads(golden)
            except json.JSONDecodeError:
                golden = [golden]
        if isinstance(golden, dict):
            expects_deny = bool(golden.get("expect_deny") or golden.get("deny"))
        elif isinstance(golden, list):
            expects_deny = any(
                (item.get("decision") if isinstance(item, dict) else item) == "deny"
                for item in golden
            )
        else:
            expects_deny = False
        if expects_deny and deny_count == 0:
            errors.append("golden expectation requires a deny but audit log contains none")
    evidence = {"entries": len(entries), "deny_entries": deny_count, "errors": errors}
    return make_proof(
        "AGT_AUDIT", ctx, result="FAIL" if errors else "PASS",
        command=["factory-checks", "validate-agt-audit", str(path)],
        tool_name="factory-agt-audit-validator", version=CHECK_RUNNER_VERSION,
        exit_code=1 if errors else 0, stdout=canonical_json(evidence),
        detail="; ".join(errors) if errors else f"validated {len(entries)} audit entry/entries; {deny_count} deny",
    )
