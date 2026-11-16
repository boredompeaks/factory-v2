"""Render compact role cards from the copied policy files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
POLICIES = ROOT / "policies"


def load(name: str) -> dict:
    with (POLICIES / name).open(encoding="utf-8") as stream:
        return json.load(stream)


def card_for(role: str) -> dict:
    kinds = load("task-kinds.json")
    dispatch = load("dispatch-rules.json")
    checks = load("check-runners.json")["checks"]
    severity = load("severity.json")
    gate = load("gate.json")

    test_rules = {
        check_id: {
            "scope": rule.get("scope"),
            "emits": rule.get("emits", []),
        }
        for check_id, rule in checks.items()
        if role in rule.get("runners", [])
    }
    return {
        "role": role,
        "scope_rules": {
            "factory_edit_ban": dispatch["factory_edit_ban"]["paths"],
            "task_scope": "Use only the assigned task scope in the writable worktree.",
            "doc_paths": kinds.get("DOC", {}).get("allowed_path_globs", []),
            "doc_denies": kinds.get("DOC", {}).get("denied_path_globs", []),
        },
        "test_rules": test_rules,
        "severity_caps": {
            "scale": severity["severity_scale"],
            "default": severity["default"],
            "blocking": severity["blocking_severities"],
            "residual": severity["residual_severities"],
        },
        "gate_sets_by_kind": gate["gate_sets_by_kind"],
        "proof_rule": gate["proof_rule"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True)
    args = parser.parse_args(argv)
    role = args.role
    if not (ROOT / "agents" / f"{role}.md").is_file():
        parser.error(f"unknown role: {role}")
    output = json.dumps(card_for(role), separators=(",", ":"), ensure_ascii=False)
    if len(output.encode("utf-8")) >= 4096:
        print("policy card exceeds 4KB", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
