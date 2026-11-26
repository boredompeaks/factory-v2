"""Base/head state comparison for the "do not make it worse" checks.

RADON and BANDIT used to judge the whole changed file at head. A pre-existing
rank-F function therefore failed every task that touched its file, even when
the diff never went near it. This module gives both checks one source of truth
for a *delta*: it reads the exact blobs at ``base`` and ``head``, runs the tool
once per side, and classifies each block as new, crossed, worsened, residual or
removed.

Hygiene is the delta (do not create or worsen a hot spot). Pre-existing state
is debt, which ``factory/checks/deviation.py`` makes the author own explicitly.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from . import (
    changed_paths,
    executable,
    is_test_path,
    repo_path,
    run_process,
    tool_version,
)

CEILING_RANKS = {"C", "D", "E", "F"}


def product_python(ctx: dict) -> str | None:
    """Resolve the interpreter that owns the lane's dependencies."""
    root = repo_path(ctx)
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


def module_prefix(ctx: dict, python: str | None, module: str):
    """Return ``(argv_prefix, version)`` for a tool, preferring the product venv."""
    if python:
        probe = run_process(
            [python, "-m", module, "--version"], ctx=ctx,
            cwd=repo_path(ctx), timeout=30,
        )
        if probe["exit"] == 0:
            text = (probe["stdout"] or probe["stderr"]).strip().splitlines()
            return [python, "-m", module], (text[0][:160] if text else "version query unavailable")
    binary = executable(module)
    if not binary:
        return None, None
    return [binary], None


def _git(ctx: dict, *args: str, timeout: int = 60) -> dict:
    root = repo_path(ctx)
    return run_process(
        ["git", "-C", str(root), *args], ctx=ctx, cwd=root, timeout=timeout,
    )


def rename_map(ctx: dict) -> dict[str, str]:
    """Map head paths to their base path when git reports an exact rename."""
    proc = _git(ctx, "diff", "--name-status", "-M50", f"{ctx.get('base')}..{ctx.get('head')}")
    if proc["error"] or proc["exit"] != 0:
        return {}
    result: dict[str, str] = {}
    for line in proc["stdout"].splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].startswith("R"):
            result[parts[2].replace("\\", "/")] = parts[1].replace("\\", "/")
    return result


def changed_hunks(ctx: dict, path: str, renames: dict[str, str] | None = None) -> list[tuple[int, int]]:
    """New-side line ranges the diff adds or rewrites in ``path``.

    ``-U0`` keeps the ranges tight: a hunk is the lines the author actually
    touched, which is what decides whether a pre-existing block is "in play".
    The diff is taken between the two blobs, so an exact rename yields no
    hunks and a rename with edits yields only the edited ranges.
    """
    base = str(ctx.get("base"))
    head = str(ctx.get("head"))
    base_path = (renames or {}).get(path, path)
    if _show(ctx, base, base_path) is None:
        return []
    proc = _git(
        ctx, "--no-pager", "diff", "--no-color", "--unified=0",
        f"{base}:{base_path}", f"{head}:{path}",
    )
    if proc["error"] or proc["exit"] != 0:
        return []
    ranges: list[tuple[int, int]] = []
    for line in proc["stdout"].splitlines():
        if not line.startswith("@@"):
            continue
        plus = line.split(" +", 1)[1].split(" @@", 1)[0]
        start, _, count = plus.partition(",")
        try:
            first = int(start)
            length = int(count) if count else 1
        except ValueError:
            continue
        if length <= 0:
            continue
        ranges.append((first, first + length - 1))
    return ranges


def _show(ctx: dict, ref: str, path: str) -> str | None:
    proc = _git(ctx, "show", f"{ref}:{path}", timeout=45)
    if proc["error"] or proc["exit"] != 0:
        return None
    return proc["stdout"]


def _run_radon(ctx: dict, root: Path, files: list[str]):
    if not files:
        return {}, None, "not-run"
    prefix, version = module_prefix(ctx, product_python(ctx), "radon")
    if prefix is None:
        return None, {
            "command": ["radon", "cc", "-s", "-j", *files],
            "exit": None,
            "stdout": "",
            "detail": "radon is unavailable: no product venv module and no radon binary on PATH",
            "version": "absent",
        }, "absent"
    command = [*prefix, "cc", "-s", "-j", *files]
    proc = run_process(command, ctx=ctx, cwd=root, timeout=240)
    if proc["error"] or proc["exit"] != 0:
        return None, {
            "command": command,
            "exit": proc["exit"],
            "stdout": proc["stdout"] + proc["stderr"],
            "detail": proc["error"] or "radon exited non-zero",
            "version": version or tool_version("radon", ctx=ctx, cwd=root),
        }, version or "unknown"
    try:
        doc = json.loads(proc["stdout"])
    except json.JSONDecodeError as exc:
        return None, {
            "command": command, "exit": proc["exit"], "stdout": proc["stdout"],
            "detail": f"radon emitted invalid JSON: {exc}",
            "version": version or "unknown",
        }, version or "unknown"
    if not isinstance(doc, dict):
        return None, {
            "command": command, "exit": proc["exit"], "stdout": proc["stdout"],
            "detail": "radon emitted a non-object JSON document",
            "version": version or "unknown",
        }, version or "unknown"
    return doc, None, version or "unknown"


def _python_paths(ctx: dict) -> tuple[list[str] | None, dict | None]:
    paths, proc, error = changed_paths(ctx)
    if error:
        return None, {
            "command": (proc or {}).get("command", []),
            "exit": (proc or {}).get("exit"),
            "stdout": (proc or {}).get("stdout", error),
            "detail": f"cannot determine changed paths: {error}",
        }
    return [
        path for path in paths
        if path.endswith(".py") and not is_test_path(path)
    ], None


def block_key(block: dict) -> tuple[str, str, int]:
    return (
        str(block.get("type") or ""),
        str(block.get("name") or ""),
        int(block.get("col_offset") or 0),
    )


def block_record(block: dict) -> dict:
    return {
        "type": block.get("type"),
        "name": block.get("name"),
        "col_offset": block.get("col_offset"),
        "lineno": block.get("lineno"),
        "endline": block.get("endline"),
        "complexity": block.get("complexity"),
        "rank": block.get("rank"),
    }


def radon_state(ctx: dict) -> tuple[dict | None, dict | None]:
    """Return ``{"base": {...}, "head": {...}, "renames": {...}}`` or an error."""
    paths, error = _python_paths(ctx)
    if error:
        return None, error
    assert paths is not None
    if not paths:
        return {"base": {}, "head": {}, "files": [], "renames": {}}, None
    renames = rename_map(ctx)
    base_tmp = tempfile.TemporaryDirectory(prefix="factory-radon-base-")
    head_tmp = tempfile.TemporaryDirectory(prefix="factory-radon-head-")
    base_files: list[str] = []
    head_files: list[str] = []
    with base_tmp as base_dir, head_tmp as head_dir:
        base_root, head_root = Path(base_dir), Path(head_dir)
        for path in paths:
            head_text = _show(ctx, str(ctx.get("head")), path)
            if head_text is not None:
                (head_root / path).parent.mkdir(parents=True, exist_ok=True)
                (head_root / path).write_text(head_text, encoding="utf-8", errors="replace")
                head_files.append(path)
            base_path = renames.get(path, path)
            base_text = _show(ctx, str(ctx.get("base")), base_path)
            if base_text is None:
                continue
            (base_root / base_path).parent.mkdir(parents=True, exist_ok=True)
            (base_root / base_path).write_text(base_text, encoding="utf-8", errors="replace")
            base_files.append(base_path)
        base_doc, problem, _ = _run_radon(ctx, base_root, base_files)
        if problem:
            return None, problem
        head_doc, problem, version = _run_radon(ctx, head_root, head_files)
        if problem:
            return None, problem

    def _index(doc: dict) -> dict[str, list[dict]]:
        index: dict[str, list[dict]] = {}
        for key, blocks in (doc or {}).items():
            relative = Path(key).as_posix().replace("\\", "/")
            if not isinstance(blocks, list):
                continue
            index[relative] = [block for block in blocks if isinstance(block, dict)]
        return index

    return {
        "base": _index(base_doc or {}),
        "head": _index(head_doc or {}),
        "files": paths,
        "renames": renames,
        "version": version,
    }, None


def classify(base_blocks: list[dict], head_blocks: list[dict]) -> dict:
    """Split head blocks into new / crossed / worsened / residual, by identity."""
    base_by_key = {block_key(block): block for block in base_blocks}
    result: dict[str, list] = {
        "new": [], "crossed": [], "worsened": [], "residual": [], "improved": [],
    }
    for head in head_blocks:
        if str(head.get("rank", "")).upper() not in CEILING_RANKS:
            continue
        base = base_by_key.get(block_key(head))
        record = {"base": block_record(base) if base else None, "head": block_record(head)}
        if base is None:
            result["new"].append(record)
            continue
        if str(base.get("rank", "")).upper() not in CEILING_RANKS:
            result["crossed"].append(record)
            continue
        if int(head.get("complexity") or 0) > int(base.get("complexity") or 0):
            result["worsened"].append(record)
        elif int(head.get("complexity") or 0) < int(base.get("complexity") or 0):
            result["improved"].append(record)
        else:
            result["residual"].append(record)
    return result


def overlaps(block: dict, hunks: list[tuple[int, int]]) -> bool:
    start = int(block.get("lineno") or 0)
    end = int(block.get("endline") or start)
    return any(first <= end and start <= last for first, last in hunks)


def per_file(state: dict) -> dict[str, dict]:
    """Classify every changed head file against its base counterpart."""
    renames = state.get("renames") or {}
    base_map = state.get("base") or {}
    head_map = state.get("head") or {}
    result: dict[str, dict] = {}
    for path in state.get("files") or []:
        base_path = renames.get(path, path)
        result[path] = classify(base_map.get(base_path, []), head_map.get(path, []))
    return result


def bandit_state(ctx: dict) -> tuple[dict | None, dict | None]:
    """Count MEDIUM/HIGH Bandit issues per (path, test_id) at base and head."""
    paths, error = _python_paths(ctx)
    if error:
        return None, error
    assert paths is not None
    if not paths:
        return {"base": {}, "head": {}, "files": [], "renames": {}}, None
    renames = rename_map(ctx)
    prefix, version = module_prefix(ctx, product_python(ctx), "bandit")
    if prefix is None:
        return None, {
            "command": ["bandit", "-f", "json", "-q"],
            "exit": None,
            "stdout": "",
            "detail": "bandit is unavailable: no product venv module and no bandit binary on PATH",
            "version": "absent",
        }

    def _scan(root: Path, files: list[str]):
        if not files:
            return {}, None
        command = [*prefix, "-r", "-f", "json", "-q", *files]
        proc = run_process(command, ctx=ctx, cwd=root, timeout=240)
        if proc["error"]:
            return None, {
                "command": command, "exit": proc["exit"],
                "stdout": proc["stdout"] + proc["stderr"],
                "detail": proc["error"], "version": version or "unknown",
            }
        try:
            doc = json.loads(proc["stdout"] or "{}")
        except json.JSONDecodeError as exc:
            return None, {
                "command": command, "exit": proc["exit"], "stdout": proc["stdout"],
                "detail": f"bandit emitted invalid JSON: {exc}",
                "version": version or "unknown",
            }
        counts: dict[str, int] = {}
        for issue in (doc or {}).get("results") or []:
            if not isinstance(issue, dict):
                continue
            if str(issue.get("issue_severity", "")).upper() not in {"MEDIUM", "HIGH"}:
                continue
            key = f"{issue.get('filename')}::{issue.get('test_id')}"
            counts[key] = counts.get(key, 0) + 1
        return counts, None

    with tempfile.TemporaryDirectory(prefix="factory-bandit-base-") as base_dir, \
            tempfile.TemporaryDirectory(prefix="factory-bandit-head-") as head_dir:
        base_root, head_root = Path(base_dir), Path(head_dir)
        base_files: list[str] = []
        head_files: list[str] = []
        for path in paths:
            head_text = _show(ctx, str(ctx.get("head")), path)
            if head_text is not None:
                (head_root / path).parent.mkdir(parents=True, exist_ok=True)
                (head_root / path).write_text(head_text, encoding="utf-8", errors="replace")
                head_files.append(path)
            base_path = renames.get(path, path)
            base_text = _show(ctx, str(ctx.get("base")), base_path)
            if base_text is None:
                continue
            (base_root / base_path).parent.mkdir(parents=True, exist_ok=True)
            (base_root / base_path).write_text(base_text, encoding="utf-8", errors="replace")
            base_files.append(base_path)
        base_counts, problem = _scan(base_root, base_files)
        if problem:
            return None, problem
        head_counts, problem = _scan(head_root, head_files)
        if problem:
            return None, problem
    return {
        "base": base_counts or {}, "head": head_counts or {},
        "files": paths, "renames": renames,
    }, None


def bandit_delta(state: dict) -> dict:
    base, head = state["base"], state["head"]
    new: list[dict] = []
    for key, count in head.items():
        before = base.get(key, 0)
        if count > before:
            new.append({"key": key, "base": before, "head": count})
    removed = [
        {"key": key, "base": count, "head": head.get(key, 0)}
        for key, count in base.items()
        if head.get(key, 0) < count
    ]
    return {"new": new, "removed": removed}
