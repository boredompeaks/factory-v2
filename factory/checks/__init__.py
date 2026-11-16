"""Deterministic check runners for Factory v2.

Runners are deliberately evidence-oriented: subprocess output and inspected
diffs determine the result; callers cannot supply a claimed result.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

CHECK_RUNNER_VERSION = "1.0"
FIXED_ENV = {
    "TZ": "UTC",
    "PYTHONHASHSEED": "0",
    "LC_ALL": "C",
    "PYTHONDONTWRITEBYTECODE": "1",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def ctx_path(ctx: dict, key: str, fallback: str | Path | None = None) -> Path | None:
    value = ctx.get(key, fallback)
    return Path(value).expanduser().resolve() if value else None


def repo_path(ctx: dict) -> Path | None:
    return ctx_path(ctx, "repo_root") or ctx_path(ctx, "worktree")


def worktree_path(ctx: dict) -> Path | None:
    return ctx_path(ctx, "worktree") or repo_path(ctx)


def policy_path(ctx: dict, name: str) -> Path:
    policies = ctx_path(ctx, "policies_dir")
    if policies is None:
        root = repo_path(ctx)
        policies = (root / "policies") if root else Path.cwd() / "policies"
    return policies / name


def process_env(ctx: dict, extra: dict | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update(FIXED_ENV)
    supplied = ctx.get("env")
    if isinstance(supplied, dict):
        env.update({str(key): str(value) for key, value in supplied.items() if value is not None})
    if extra:
        env.update({str(key): str(value) for key, value in extra.items() if value is not None})
    env.update(FIXED_ENV)
    return env


def run_process(
    command: Iterable[str],
    *,
    ctx: dict | None = None,
    cwd: str | Path | None = None,
    timeout: int | float = 120,
    env: dict | None = None,
    input_text: str | None = None,
) -> dict:
    argv = [str(part) for part in command]
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            env=process_env(ctx or {}, env),
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, float(timeout)),
            check=False,
        )
        return {
            "command": argv,
            "exit": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "timed_out": False,
            "error": None,
        }
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return {
            "command": argv,
            "exit": None,
            "stdout": out,
            "stderr": err,
            "timed_out": True,
            "error": f"timed out after {timeout}s",
        }
    except OSError as exc:
        return {
            "command": argv,
            "exit": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def executable(name: str) -> str | None:
    return shutil.which(name)


def tool_version(name: str, *, ctx: dict | None = None, cwd: str | Path | None = None) -> str:
    path = executable(name)
    if path is None:
        return "absent"
    result = run_process([path, "--version"], ctx=ctx or {}, cwd=cwd, timeout=10)
    text = (result["stdout"] or result["stderr"]).strip().splitlines()
    if result["exit"] == 0 and text:
        return text[0][:160]
    return "version query unavailable"


def make_proof(
    check: str,
    ctx: dict,
    *,
    result: str,
    command: Iterable[str] | None = None,
    tool_name: str = "factory-checks",
    version: str = CHECK_RUNNER_VERSION,
    exit_code: int | None = None,
    stdout: str = "",
    detail: str = "",
    artifacts: list[dict] | None = None,
    seed: int | None = None,
    network: str = "not_isolated",
    env_extra: dict | None = None,
) -> dict:
    if result not in {"PASS", "FAIL", "SKIP", "TOOL_ABSENT", "FLAKY"}:
        result = "FAIL"
        detail = f"invalid runner result normalized to FAIL; {detail}".strip()
    proof_env = dict(FIXED_ENV)
    proof_env["network"] = network
    if env_extra:
        # Never echo arbitrary caller environment (which may contain secrets).
        proof_env.update(
            {
                str(key): str(value)
                for key, value in env_extra.items()
                if str(key) in {"DATABASE_ENGINE", "DEBUG", "DJANGO_SETTINGS_MODULE", "HYPOTHESIS_SEED"}
            }
        )
    return {
        "check": check,
        "task_id": str(ctx.get("task_id") or ""),
        "base": ctx.get("base"),
        "head": ctx.get("head"),
        "tree": ctx.get("tree"),
        "command": [str(part) for part in (command or [])],
        "tool": {"name": tool_name, "version": version, "image": None},
        "env": proof_env,
        "seed": seed if seed is not None else ctx.get("seed"),
        "exit": exit_code,
        "stdout_sha256": sha256(stdout),
        "artifacts": sorted(artifacts or [], key=lambda item: canonical_json(item)),
        "result": result,
        "detail": detail,
    }


def git_command(ctx: dict, *args: str) -> list[str] | None:
    root = repo_path(ctx)
    if root is None:
        return None
    return ["git", "-C", str(root), *args]


def get_diff(ctx: dict, unified: int = 0) -> tuple[dict | None, str | None]:
    root = repo_path(ctx)
    base, head = ctx.get("base"), ctx.get("head")
    if root is None or not base or not head:
        return None, "repo_root/worktree, base, and head are required"
    command = [
        "git", "-C", str(root), "--no-pager", "diff", "--no-color",
        f"--unified={int(unified)}", f"{base}..{head}",
    ]
    result = run_process(command, ctx=ctx, cwd=root, timeout=45)
    if result["error"]:
        return result, result["error"]
    if result["exit"] != 0:
        return result, (result["stderr"].strip() or f"git diff exited {result['exit']}")
    return result, None


def changed_paths(ctx: dict) -> tuple[list[str] | None, dict | None, str | None]:
    root = repo_path(ctx)
    base, head = ctx.get("base"), ctx.get("head")
    if root is None or not base or not head:
        return None, None, "repo_root/worktree, base, and head are required"
    command = [
        "git", "-C", str(root), "diff", "--name-only", "--no-renames",
        f"{base}..{head}",
    ]
    result = run_process(command, ctx=ctx, cwd=root, timeout=45)
    if result["error"]:
        return None, result, result["error"]
    if result["exit"] != 0:
        return None, result, result["stderr"].strip() or f"git diff exited {result['exit']}"
    paths = sorted({line.strip().replace("\\", "/") for line in result["stdout"].splitlines() if line.strip()})
    return paths, result, None


def glob_match(path: str, pattern: str) -> bool:
    """Match POSIX repository paths; ** may span directories."""
    import re

    path = path.replace("\\", "/").lstrip("./")
    pattern = str(pattern).replace("\\", "/").lstrip("./")
    pieces: list[str] = ["^"]
    index = 0
    while index < len(pattern):
        if pattern[index:index + 2] == "**":
            index += 2
            if index < len(pattern) and pattern[index] == "/":
                pieces.append("(?:.*/)?")
                index += 1
            else:
                pieces.append(".*")
        elif pattern[index] == "*":
            pieces.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            pieces.append("[^/]")
            index += 1
        else:
            pieces.append(re.escape(pattern[index]))
            index += 1
    pieces.append("$")
    return re.match("".join(pieces), path) is not None


def is_test_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    name = p.rsplit("/", 1)[-1]
    return (
        name in {"tests.py", "test.py"}
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
        or "/tests/" in p
        or "/__tests__/" in p
    )


def read_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"cannot read valid JSON at {path}: {exc}"


def temp_diff(ctx: dict, diff_text: str):
    """Context manager yielding a diff path, kept outside the target worktree."""
    import tempfile

    class DiffFile:
        def __init__(self):
            self.temp = tempfile.TemporaryDirectory(prefix="factory-check-diff-")
            self.path = Path(self.temp.name) / "change.diff"
            self.path.write_text(diff_text, encoding="utf-8")

        def __enter__(self):
            return self.path

        def __exit__(self, *_):
            self.temp.cleanup()

    return DiffFile()


def runner_failure(check: str, ctx: dict, exc: BaseException) -> dict:
    return make_proof(
        check, ctx, result="FAIL", tool_name="factory-checks",
        exit_code=None, stdout=f"{type(exc).__name__}: {exc}",
        detail=f"runner error: {type(exc).__name__}: {exc}",
    )


def clean_checkout_problem(ctx: dict) -> dict | None:
    """Reject dirty/mismatched source before a runner reads its worktree."""
    worktree = worktree_path(ctx)
    if worktree is None or not ctx.get("head"):
        return None
    root = repo_path(ctx) or worktree
    commands = [
        ["git", "-C", str(root), "rev-parse", "--verify", f"{ctx['head']}^{{commit}}"],
        ["git", "-C", str(worktree), "rev-parse", "--verify", "HEAD"],
        ["git", "-C", str(worktree), "rev-parse", "--verify", "HEAD^{tree}"],
        ["git", "-C", str(worktree), "status", "--porcelain", "--untracked-files=all"],
    ]
    outputs = []
    for command in commands:
        proc = run_process(command, ctx=ctx, cwd=worktree, timeout=15)
        outputs.append({"command": command, "exit": proc["exit"], "stdout": proc["stdout"], "stderr": proc["stderr"], "error": proc["error"]})
        if proc["error"] or proc["exit"] != 0:
            return {
                "command": command,
                "stdout": canonical_json(outputs),
                "detail": f"cannot verify clean worktree: {proc['error'] or proc['stderr'].strip()}",
                "exit": proc["exit"],
            }
    expected_head = outputs[0]["stdout"].strip()
    actual_head = outputs[1]["stdout"].strip()
    actual_tree = outputs[2]["stdout"].strip()
    dirty = outputs[3]["stdout"].strip()
    problems = []
    if expected_head != actual_head:
        problems.append(f"worktree HEAD {actual_head} does not match requested head {expected_head}")
    if ctx.get("tree") and str(ctx["tree"]).lower() != actual_tree.lower():
        problems.append(f"worktree tree {actual_tree} does not match requested tree {ctx['tree']}")
    if dirty:
        problems.append("worktree is not clean (tracked or untracked changes present)")
    if problems:
        return {
            "command": commands[-1],
            "stdout": canonical_json(outputs),
            "detail": "; ".join(problems),
            "exit": 1,
        }
    return None


from . import static as _static  # noqa: E402
from . import tests as _tests  # noqa: E402
from . import mutation as _mutation  # noqa: E402
from . import fuzz as _fuzz  # noqa: E402
from . import dast as _dast  # noqa: E402

CHECKS = {
    "SCOPE": _static.check_scope,
    "SUITE": _tests.check_suite,
    "TEST_SET": _tests.check_test_set,
    "TEST_INFRA": _static.check_test_infra,
    "BASE_FAIL_HEAD_PASS": _tests.check_base_fail_head_pass,
    "MUTATION": _mutation.check_mutation,
    "CHANGELOG": _static.check_changelog,
    "SECRETS": _static.check_secrets,
    "CONVENTION": _static.check_convention,
    "SMELL": _static.check_smell,
    "GATE_CONTROLS": _static.check_gate_controls,
    "POLICY_DIGEST": _static.check_policy_digest,
    "ARCH": _static.check_arch,
    "ORACLE_PROVENANCE": _static.check_oracle_provenance,
    "GUARD_DELTA": _static.check_guard_delta,
    "AUTHZ_MATRIX": _static.check_authz_matrix,
    "SEMGREP": _static.check_semgrep,
    "THREAD_RACE": _tests.check_thread_race,
    "SCHEMA_FUZZ": _fuzz.check_schema_fuzz,
    "BOOTTEST": _tests.check_boottest,
    "ZIZMOR": _static.check_zizmor,
    "DAST_BASELINE": _dast.check_dast_baseline,
    "AGT_PARITY": _static.check_agt_parity,
    "AGT_AUDIT": _static.check_agt_audit,
    "RADON": _static.check_radon,
    "BANDIT": _static.check_bandit,
}


def run_check(check_id: str, ctx: dict) -> dict:
    """Run one registered check and return its harness-generated Proof."""
    runner = CHECKS.get(check_id)
    if runner is None:
        return make_proof(
            str(check_id), ctx, result="FAIL", tool_name="factory-checks",
            detail=f"unknown check id: {check_id}",
        )
    try:
        checkout_problem = clean_checkout_problem(ctx)
        if checkout_problem:
            return make_proof(
                check_id, ctx, result="FAIL", command=checkout_problem["command"],
                tool_name="git", version=tool_version("git", ctx=ctx),
                exit_code=checkout_problem["exit"], stdout=checkout_problem["stdout"],
                detail=checkout_problem["detail"],
            )
        proof = runner(ctx)
        if not isinstance(proof, dict) or proof.get("check") != check_id:
            raise TypeError(f"runner for {check_id} did not return its Proof")
        return proof
    except BaseException as exc:  # Keep the gate fail-closed at its boundary.
        return runner_failure(check_id, ctx, exc)


__all__ = ["CHECKS", "run_check"]
