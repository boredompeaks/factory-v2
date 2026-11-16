#!/usr/bin/env python3
"""Sync agent persona models from ``policies/models.json``.

``policies/models.json`` is the single source of truth for agent model ids.
Each persona file in ``agents/`` and ``.opencode/agents/`` is mapped to a
policy role through ``persona_model_map`` and its frontmatter ``model:`` line
is rewritten to ``provider/model[#variant]``.

Usage:
    python tools/sync_agent_models.py           # rewrite drifted files
    python tools/sync_agent_models.py --check   # print drift, exit 1 if any

Only the frontmatter ``model:`` line is touched; all other bytes, including
the rest of the frontmatter, are preserved.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = REPO_ROOT / "policies" / "models.json"
AGENT_DIRS = (REPO_ROOT / "agents", REPO_ROOT / ".opencode" / "agents")


class SyncError(Exception):
    """Raised when a persona file or policy cannot be synced."""


def load_policy(path: Path = POLICY_PATH) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def role_model_id(role_name: str, policy: dict) -> str:
    try:
        role = policy["roles"][role_name]
    except KeyError as exc:
        raise SyncError(f"policy has no role {role_name!r}") from exc

    provider = role.get("provider")
    model = role.get("model")
    if not provider or not model:
        raise SyncError(f"role {role_name!r} needs provider and model")

    model_id = f"{provider}/{model}"
    variant = role.get("variant")
    if variant:
        model_id = f"{model_id}#{variant}"
    return model_id


def _frontmatter_bounds(lines: list[str], rel: str) -> tuple[int, int]:
    if not lines or lines[0].strip() != "---":
        raise SyncError(f"{rel}: missing opening frontmatter fence")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return 1, index
    raise SyncError(f"{rel}: missing closing frontmatter fence")


def replace_model_line(text: str, model_id: str, rel: str) -> tuple[str, str]:
    """Return ``(new_text, old_model_id)`` with only model: replaced."""
    lines = text.splitlines(keepends=True)
    start, end = _frontmatter_bounds(lines, rel)

    for index in range(start, end):
        if lines[index].startswith("model:"):
            body = lines[index].rstrip("\r\n")
            line_ending = lines[index][len(body) :]
            old_value = body[len("model:") :].strip()
            lines[index] = f"model: {model_id}{line_ending}"
            return "".join(lines), old_value

    raise SyncError(f"{rel}: missing model: line in frontmatter")


def iter_personas(directories: tuple[Path, ...] = AGENT_DIRS):
    for directory in directories:
        if not directory.is_dir():
            raise SyncError(f"agent directory not found: {directory}")
        for path in sorted(directory.glob("*.md")):
            yield path


def sync(check: bool = False, policy_path: Path = POLICY_PATH) -> int:
    policy = load_policy(policy_path)
    persona_map = policy.get("persona_model_map")
    if not isinstance(persona_map, dict) or not persona_map:
        raise SyncError("policy is missing a non-empty persona_model_map")

    drift: list[tuple[str, str, str]] = []
    errors: list[str] = []

    for path in iter_personas():
        rel = path.relative_to(REPO_ROOT).as_posix()
        persona = path.stem
        role_name = persona_map.get(persona)
        if role_name is None:
            errors.append(f"{rel}: persona {persona!r} is not in persona_model_map")
            continue

        desired = role_model_id(role_name, policy)
        with path.open(encoding="utf-8", newline="") as handle:
            text = handle.read()
        try:
            new_text, old = replace_model_line(text, desired, rel)
        except SyncError as exc:
            errors.append(str(exc))
            continue

        if new_text != text:
            drift.append((rel, old, desired))
            if not check:
                with path.open("w", encoding="utf-8", newline="") as handle:
                    handle.write(new_text)

    for rel, old, desired in drift:
        print(f"{rel}: {old} -> {desired}")

    for message in errors:
        print(f"error: {message}", file=sys.stderr)

    if errors:
        return 2
    if check and drift:
        print(f"model drift: {len(drift)} file(s)", file=sys.stderr)
        return 1
    if not check and drift:
        print(f"updated {len(drift)} file(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift without writing; exit 1 when any file differs",
    )
    args = parser.parse_args(argv)
    try:
        return sync(check=args.check)
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
