"""Path-scope matching and git diff containment checks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .state import load_task
from .util import FactoryError, git_output, resolve_worktree


def _glob_regex(pattern: str) -> re.Pattern[str]:
    pattern = _normalize(pattern)
    out = ["^"]
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    out.append("$")
    return re.compile("".join(out))


def path_matches(path: str, pattern: str) -> bool:
    normalized_path = _normalize(path)
    normalized_pattern = _normalize(pattern)
    return bool(_glob_regex(normalized_pattern).match(normalized_path))


def _normalize(value: str) -> str:
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def in_scope(path: str, scopes: list[str]) -> bool:
    if path.startswith("/") or ".." in Path(path).parts:
        return False
    return any(path_matches(path, pattern) for pattern in scopes)


def changed_paths(task: dict[str, Any], *, base: str | None = None, head: str = "HEAD") -> list[str]:
    root = resolve_worktree(task)
    base_ref = base or task.get("base_sha") or ""
    if not base_ref:
        return []
    result = git_output(
        ["diff", "--name-only", "--diff-filter=ACMRTUXB", f"{base_ref}...{head}", "--"],
        cwd=root,
    )
    if not result:
        result = git_output(
            ["diff", "--name-only", "--diff-filter=ACMRTUXB", base_ref, head, "--"],
            cwd=root,
        )
    return sorted({line.strip().replace("\\", "/") for line in result.splitlines() if line.strip()})


def check_scope(
    task_id: str,
    *,
    paths: list[str] | None = None,
    base: str | None = None,
    head: str = "HEAD",
    staged: bool = False,
) -> dict[str, Any]:
    task = load_task(task_id)
    if paths is not None:
        candidate_paths = sorted(set(paths))
    elif staged:
        root = resolve_worktree(task)
        output = git_output(
            ["diff", "--cached", "--name-only", "--diff-filter=ACMR"], cwd=root
        )
        candidate_paths = sorted(
            {line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()}
        )
    else:
        candidate_paths = sorted(set(changed_paths(task, base=base, head=head)))
    scopes = task.get("scope", [])
    if not isinstance(scopes, list) or not all(isinstance(item, str) for item in scopes):
        raise FactoryError(f"task {task_id} has malformed scope")
    violations = [path for path in candidate_paths if not in_scope(path, scopes)]
    return {
        "check": "SCOPE",
        "task_id": task_id,
        "result": "FAIL" if violations else "PASS",
        "paths": candidate_paths,
        "scope": scopes,
        "violations": violations,
        "detail": f"{len(violations)} of {len(candidate_paths)} changed path(s) outside task scope",
    }
