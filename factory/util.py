"""Shared filesystem, policy, git, and JSON helpers for the factory CLI."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence


REPO_ROOT = Path(__file__).resolve().parent.parent
ZERO_SHA = "0" * 40


class FactoryError(Exception):
    """An expected, user-facing factory error."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def state_root() -> Path:
    configured = os.environ.get("FACTORY_HOME")
    return Path(configured).expanduser().resolve() if configured else Path.home() / ".factory"


def tasks_root() -> Path:
    configured = os.environ.get("FACTORY_TASKS_ROOT")
    return Path(configured).expanduser().resolve() if configured else state_root() / "tasks"


def task_dir(task_id: str) -> Path:
    validate_task_id(task_id)
    return tasks_root() / task_id


def task_path(task_id: str) -> Path:
    return task_dir(task_id) / "task.json"


def evidence_root() -> Path:
    configured = os.environ.get("FACTORY_EVIDENCE_ROOT")
    return Path(configured).expanduser().resolve() if configured else state_root() / "evidence"


def evidence_dir(task_id: str) -> Path:
    validate_task_id(task_id)
    return evidence_root() / task_id


def proofs_dir(task_id: str) -> Path:
    return evidence_dir(task_id) / "proofs"


def validate_task_id(task_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", task_id):
        raise FactoryError(f"invalid task id: {task_id!r}")


def read_json(path: Path, *, required: bool = True) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        if not required:
            return None
        raise FactoryError(f"file not found: {path}") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise FactoryError(f"cannot read JSON {path}: {exc}") from exc


def write_json(path: Path, value: Any, *, pretty: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
        if pretty
        else canonical_json(value)
    )
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


@contextlib.contextmanager
def locked_file(path: Path) -> Iterator[Any]:
    """Take an advisory exclusive lock, creating the lock file if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield stream
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def policy_path(name: str) -> Path:
    return REPO_ROOT / "policies" / name


def load_policy(name: str, *, default: Any = None) -> Any:
    path = policy_path(name)
    if not path.exists() and default is not None:
        return default
    return read_json(path)


def policy_hash(root: Path | None = None) -> str:
    policy_root = (root or REPO_ROOT) / "policies"
    files = sorted(path for path in policy_root.rglob("*") if path.is_file())
    content = {
        path.relative_to(root or REPO_ROOT).as_posix(): sha256_bytes(path.read_bytes())
        for path in files
    }
    return sha256_json(content)


def run(
    argv: Sequence[str],
    *,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    check: bool = False,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(argv),
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        raise FactoryError(f"executable not found: {argv[0]}") from exc
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise FactoryError(f"{' '.join(argv)} failed ({result.returncode}): {detail}")
    return result


def git_output(
    args: Sequence[str],
    *,
    cwd: Path | str | None = None,
    fallback: str = "",
) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd is not None else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        return fallback
    return result.stdout.strip() if result.returncode == 0 else fallback


def git_metadata(root: Path | str, base: str | None = None) -> dict[str, str]:
    root_path = Path(root)
    head = git_output(["rev-parse", "HEAD"], cwd=root_path, fallback=ZERO_SHA)
    tree = git_output(["rev-parse", "HEAD^{tree}"], cwd=root_path, fallback=ZERO_SHA)
    return {
        "base": base or ZERO_SHA,
        "head": head or ZERO_SHA,
        "tree": tree or ZERO_SHA,
    }


def current_task_id(explicit: str | None = None) -> str:
    task_id = explicit or os.environ.get("FACTORY_TASK_ID")
    if task_id:
        validate_task_id(task_id)
        return task_id
    raise FactoryError("task id required (pass it explicitly or set FACTORY_TASK_ID)")


def resolve_worktree(task: dict[str, Any]) -> Path:
    candidate = task.get("worktree") or os.getcwd()
    path = Path(candidate).expanduser()
    return path.resolve()
