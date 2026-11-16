"""Plain-JSON task state and lane/K1 concurrency records."""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any

from .util import (
    FactoryError,
    load_policy,
    locked_file,
    read_json,
    state_root,
    task_dir,
    task_path,
    tasks_root,
    write_json,
)


def load_task(task_id: str) -> dict[str, Any]:
    value = read_json(task_path(task_id))
    if not isinstance(value, dict):
        raise FactoryError(f"task file must contain an object: {task_path(task_id)}")
    return value


def save_task(task: dict[str, Any]) -> None:
    task_id = str(task.get("task_id", ""))
    path = task_path(task_id)
    with locked_file(path.with_suffix(".lock")):
        write_json(path, task)


def update_task(task_id: str, updater: Any) -> dict[str, Any]:
    path = task_path(task_id)
    with locked_file(path.with_suffix(".lock")):
        task = read_json(path)
        if not isinstance(task, dict):
            raise FactoryError(f"task file must contain an object: {path}")
        updated = updater(task)
        write_json(path, updated)
        return updated


def all_tasks() -> list[dict[str, Any]]:
    root = tasks_root()
    results = []
    if not root.exists():
        return results
    for path in sorted(root.glob("*/task.json")):
        try:
            data = read_json(path)
        except FactoryError:
            continue
        if isinstance(data, dict):
            results.append(data)
    return results


def _lane_claim_path(lane: str) -> Path:
    digest = hashlib.sha256(lane.encode("utf-8")).hexdigest()[:24]
    return state_root() / "lane-claims" / f"{digest}.json"


def claim_task(task_id: str, by: str) -> dict[str, Any]:
    task = load_task(task_id)
    lane = task.get("lane")
    if not isinstance(lane, str) or not lane:
        raise FactoryError("task has no lane to claim")
    lane_path = _lane_claim_path(lane)
    lock_path = state_root() / "lane-claims" / ".lock"
    with locked_file(lock_path):
        current = read_json(lane_path, required=False)
        if current and current.get("task_id") != task_id:
            raise FactoryError(
                f"lane {lane} is already claimed by task {current.get('task_id')} "
                f"({current.get('by', 'unknown')})"
            )
        if current and current.get("by") != by:
            raise FactoryError(
                f"lane {lane} is already claimed for task {task_id} by {current.get('by', 'unknown')}"
            )
        claim = {
            "task_id": task_id,
            "lane": lane,
            "by": by,
            "claimed_at": (current or {}).get("claimed_at"),
        }
        if not claim["claimed_at"]:
            from .util import utc_now

            claim["claimed_at"] = utc_now()
        write_json(lane_path, claim)
        return claim


def release_claim(task_id: str) -> dict[str, Any]:
    task = load_task(task_id)
    lane = task.get("lane")
    if not isinstance(lane, str):
        raise FactoryError("task has no lane")
    lane_path = _lane_claim_path(lane)
    with locked_file(state_root() / "lane-claims" / ".lock"):
        current = read_json(lane_path, required=False)
        if current and current.get("task_id") != task_id:
            raise FactoryError(f"lane {lane} is claimed by another task")
        if current:
            lane_path.unlink(missing_ok=True)
    return {"task_id": task_id, "lane": lane, "released": bool(current)}


def active_lane_claims() -> list[dict[str, Any]]:
    directory = state_root() / "lane-claims"
    if not directory.exists():
        return []
    with locked_file(directory / ".lock"):
        claims = []
        for path in sorted(directory.glob("*.json")):
            claim = read_json(path, required=False)
            if isinstance(claim, dict):
                claims.append(claim)
        return claims


def k1_policy() -> dict[str, int]:
    policy = load_policy("concurrency.json", default={})
    return {
        "k1_total": int(policy.get("k1_total", 1)),
        "k1_per_lane": int(policy.get("k1_per_lane", 1)),
    }


def k1_lease_dir() -> Path:
    configured = os.environ.get("FACTORY_K1_STATE_ROOT")
    base = Path(configured).expanduser() if configured else state_root() / "k1-slots"
    return base.resolve()


def k1_project(lane: str) -> str:
    cleaned = "".join(character.lower() if character.isalnum() else "-" for character in lane)
    return f"factory-k1-{cleaned.strip('-') or 'lane'}"


def acquire_k1_slot(lane: str, run_id: str) -> dict[str, Any]:
    directory = k1_lease_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with locked_file(directory / ".lock"):
        leases = []
        for path in directory.glob("*.json"):
            lease = read_json(path, required=False)
            if lease and lease.get("status") == "up":
                leases.append((path, lease))
        lane_matches = [(path, lease) for path, lease in leases if lease.get("lane") == lane]
        limits = k1_policy()
        if lane_matches:
            raise FactoryError(
                f"K1_SLOT_BUSY: lane {lane} already has run {lane_matches[0][1].get('run_id')}"
            )
        if len(leases) >= limits["k1_total"]:
            holders = ", ".join(str(lease.get("run_id")) for _, lease in leases)
            raise FactoryError(f"K1_SLOT_BUSY: host limit {limits['k1_total']} reached ({holders})")
        lease = {
            "run_id": run_id,
            "lane": lane,
            "project": k1_project(lane),
            "status": "up",
            "started_at": int(time.time()),
        }
        write_json(directory / f"{hashlib.sha256(lane.encode()).hexdigest()[:24]}.json", lease)
        return lease


def get_k1_lease(lane: str) -> dict[str, Any] | None:
    directory = k1_lease_dir()
    path = directory / f"{hashlib.sha256(lane.encode()).hexdigest()[:24]}.json"
    return read_json(path, required=False)


def release_k1_slot(lane: str) -> dict[str, Any] | None:
    directory = k1_lease_dir()
    path = directory / f"{hashlib.sha256(lane.encode()).hexdigest()[:24]}.json"
    with locked_file(directory / ".lock"):
        lease = read_json(path, required=False)
        if lease:
            path.unlink(missing_ok=True)
        return lease


def validate_lane(lane: str) -> None:
    lane_map = load_policy("worktree-map.json", default={})
    phases = load_policy("phases.json", default={})
    valid = set(lane_map)
    valid.update(phases.get("permanent_lanes", []))
    valid.add(phases.get("integration_lane", "spec-comp"))
    if lane not in valid:
        raise FactoryError(f"unknown lane {lane!r}; expected one of {', '.join(sorted(valid))}")
