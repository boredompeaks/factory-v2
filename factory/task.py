"""Task lifecycle operations and policy-backed validation."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .state import active_lane_claims, load_task, save_task, update_task, validate_lane
from .util import (
    FactoryError,
    evidence_dir,
    git_output,
    load_policy,
    locked_file,
    read_json,
    task_dir,
    tasks_root,
    utc_now,
    write_json,
    ZERO_SHA,
)


KINDS = {"DOC", "FIX", "FEATURE", "SECURITY"}
DEFAULT_MODE = {
    "DOC": "DOCS",
    "FIX": "BUGFIX",
    "FEATURE": "FEATURE",
    "SECURITY": "BUGFIX",
}


def _validate_mode(mode: str, phase: str | None = None) -> None:
    policies = load_policy("modes.json")
    modes = policies.get("modes", {})
    if mode not in modes:
        raise FactoryError(f"unknown mode {mode!r}; expected one of {', '.join(sorted(modes))}")
    mode_phase = modes[mode].get("phase")
    if phase and mode_phase not in (phase, "ANY"):
        raise FactoryError(f"mode {mode} belongs to phase {mode_phase}, not {phase}")


def validate_dispatch(kind: str, scopes: list[str]) -> list[dict[str, Any]]:
    """Validate task classification and return matching policy dispatch rules."""
    if kind not in KINDS:
        raise FactoryError(f"unsupported task kind {kind!r}; expected {', '.join(sorted(KINDS))}")
    policy = load_policy("dispatch-rules.json")
    rules = policy.get("rules", [])
    known_kinds = {
        item
        for rule in rules
        for item in rule.get("match", {}).get("task_kind", [])
    }
    if kind not in known_kinds:
        raise FactoryError(f"task kind {kind} is not covered by dispatch-rules.json")
    if not scopes or any(not isinstance(scope, str) or not scope.strip() for scope in scopes):
        raise FactoryError("at least one non-empty scope glob is required")
    # Dispatch rules are constraints on subsequent routing. Keeping each matching
    # rule visible here lets callers make that routing decision without inventing
    # policy or silently dropping a matching condition.
    from .scope import path_matches

    matched = []
    for rule in rules:
        match = rule.get("match", {})
        if kind in match.get("task_kind", []) and any(
            path_matches(scope, pattern)
            for scope in scopes
            for pattern in match.get("scope_globs", [])
        ):
            matched.append(rule)
    return matched


def _next_task_id() -> str:
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    root = tasks_root()
    root.mkdir(parents=True, exist_ok=True)
    prefix = f"T-{date}-"
    numbers = []
    for path in root.glob(f"{prefix}*/task.json"):
        match = re.fullmatch(rf"{re.escape(prefix)}(\d+)", path.parent.name)
        if match:
            numbers.append(int(match.group(1)))
    return f"{prefix}{max(numbers, default=0) + 1:03d}"


def create_task(
    *,
    kind: str,
    lane: str,
    scopes: list[str],
    worktree: str | None = None,
    branch: str | None = None,
    mode: str | None = None,
    tier: str = "none",
    task_id: str | None = None,
) -> dict[str, Any]:
    validate_lane(lane)
    if tier not in {"none", "high", "max"}:
        raise FactoryError("tier must be none, high, or max")
    dispatch = validate_dispatch(kind, scopes)
    selected_mode = mode or DEFAULT_MODE.get(kind)
    if selected_mode is None:
        raise FactoryError(f"no default mode configured for {kind}")
    _validate_mode(selected_mode, "BUILD")
    root = Path(worktree or Path.cwd()).expanduser().resolve()
    creation_root = tasks_root()
    creation_root.mkdir(parents=True, exist_ok=True)
    with locked_file(creation_root / ".create.lock"):
        task_id = task_id or _next_task_id()
        path = task_dir(task_id)
        if path.exists():
            raise FactoryError(f"task already exists: {task_id}")
        base = git_output(["rev-parse", "HEAD"], cwd=root, fallback=ZERO_SHA) or ZERO_SHA
        selected_branch = branch or f"task/{task_id}"
        task = {
            "task_id": task_id,
            "kind": kind,
            "lane": lane,
            "worktree": str(root),
            "branch": selected_branch,
            "base_sha": base,
            "scope": scopes,
            "phase": "BUILD",
            "mode": selected_mode,
            "tier": tier,
            "created_at": utc_now(),
        }
        # Validate the JSON shape before writing. Dispatch matches remain policy
        # derived and are returned, not added to the unsigned task contract.
        save_task(task)
    return {"task": task, "path": str(path / "task.json"), "dispatch_rules": [r.get("rule_id") for r in dispatch]}


def task_for_worktree(worktree: str | None = None) -> str | None:
    """Return the newest task whose recorded worktree contains this directory."""
    root = Path(worktree or Path.cwd()).expanduser().resolve()
    candidates: list[tuple[str, str]] = []
    for path in tasks_root().glob("*/task.json"):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        recorded = task.get("worktree")
        task_id = task.get("task_id")
        if not isinstance(recorded, str) or not isinstance(task_id, str):
            continue
        try:
            recorded_root = Path(recorded).expanduser().resolve()
        except OSError:
            continue
        if recorded_root == root or root.is_relative_to(recorded_root):
            candidates.append((str(task.get("created_at", "")), task_id))
    if not candidates:
        return None
    return max(candidates)[1]


def show_task(task_id: str) -> dict[str, Any]:
    return load_task(task_id)


def set_phase(task_id: str, target: str, evidence: list[str] | None = None) -> dict[str, Any]:
    evidence = evidence or []
    policy = load_policy("phases.json")
    allowed = [
        transition
        for transition in policy.get("transitions", [])
        if transition.get("from") == target or transition.get("to") == target
    ]

    def update(task: dict[str, Any]) -> dict[str, Any]:
        current = task.get("phase")
        transition = next(
            (
                item
                for item in allowed
                if item.get("from") == current and item.get("to") == target
            ),
            None,
        )
        if not transition:
            raise FactoryError(f"phase transition {current!r} -> {target!r} is not in policies/phases.json")
        if not evidence:
            raise FactoryError(
                f"phase transition requires evidence refs per policies/phases.json: "
                f"{transition.get('requires', [])}"
            )
        if current == "BUILD" and target == "GATE":
            claims = active_lane_claims()
            if claims:
                owners = ", ".join(
                    f"{item.get('lane')}={item.get('task_id')}" for item in claims
                )
                raise FactoryError(f"BUILD -> GATE requires no active lane claims ({owners})")
            gate_record = read_json(evidence_dir(task_id) / "gate.json", required=False)
            if (
                not isinstance(gate_record, dict)
                or gate_record.get("release") is not True
                or gate_record.get("result") != "PASS"
            ):
                raise FactoryError("BUILD -> GATE requires a passing `factory gate --release` record")
            from .gate import verify

            verification = verify(task_id)
            if verification.get("result") != "PASS":
                raise FactoryError("BUILD -> GATE requires verified proofs and a matching policy_hash")
        elif current == "GATE" and target == "BUILD":
            release_record = read_json(evidence_dir(task_id) / "release.json", required=False)
            if not isinstance(release_record, dict) or release_record.get("result") not in {
                "MERGED",
                "REFUSED",
            }:
                raise FactoryError("GATE -> BUILD requires a completed MERGED or REFUSED release record")
        elif current == "GATE" and target == "GATE":
            release_record = read_json(evidence_dir(task_id) / "release.json", required=False)
            if not isinstance(release_record, dict) or release_record.get("result") != "REFUSED":
                raise FactoryError("GATE -> GATE requires a recorded REFUSED release")
        previous_mode = task.get("mode")
        selected_mode = (
            previous_mode
            if current == "GATE" and target == "GATE"
            else "RELEASE" if target == "GATE" else DEFAULT_MODE.get(task.get("kind"))
        )
        if not selected_mode:
            raise FactoryError(f"no default BUILD mode for task kind {task.get('kind')!r}")
        _validate_mode(selected_mode, target)
        task["phase"] = target
        task["mode"] = selected_mode
        # Keep the task.json contract limited to the unsigned task shape. The
        # transition evidence is a separate plain-JSON audit record.
        phase_path = task_dir(task_id) / "phase-records.json"
        records = read_json(phase_path, required=False) or []
        records.append(
            {
                "from": current,
                "to": target,
                "from_mode": previous_mode,
                "to_mode": selected_mode,
                "evidence": evidence,
                "recorded_at": utc_now(),
            }
        )
        write_json(phase_path, records)
        return task

    return update_task(task_id, update)


def set_mode(task_id: str, mode: str) -> dict[str, Any]:
    def update(task: dict[str, Any]) -> dict[str, Any]:
        _validate_mode(mode, task.get("phase"))
        task["mode"] = mode
        return task

    return update_task(task_id, update)


def claim(task_id: str, by: str) -> dict[str, Any]:
    from .state import claim_task

    return claim_task(task_id, by)


def unclaim(task_id: str) -> dict[str, Any]:
    from .state import release_claim

    return release_claim(task_id)
