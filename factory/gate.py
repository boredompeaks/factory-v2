"""Policy-selected gate execution and proof verification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .proof import proof_root, read_proofs, run_check, save_proof
from .state import load_task
from .util import (
    FactoryError,
    evidence_dir,
    load_policy,
    policy_hash,
    proofs_dir,
    read_json,
    utc_now,
    write_json,
)


FIX_FEATURE_CHECKS = [
    "SCOPE",
    "SUITE",
    "TEST_SET",
    "TEST_INFRA",
    "BASE_FAIL_HEAD_PASS",
    "MUTATION",
    "SECRETS",
    "CONVENTION",
    "SMELL",
    "SEMGREP",
    "ARCH",
    "RADON",
    "BANDIT",
    "ZIZMOR",
]
DOC_CHECKS = ["SCOPE", "CHANGELOG", "SECRETS", "CONVENTION"]
SECURITY_CHECKS = [
    "AUTHZ_MATRIX",
    "GUARD_DELTA",
    "ORACLE_PROVENANCE",
    "DAST_BASELINE",
]
RELEASE_CHECKS = ["THREAD_RACE", "SCHEMA_FUZZ", "BOOTTEST", "POLICY_DIGEST"]


def _policy_gate_sets() -> dict[str, list[str]]:
    policy = load_policy("gate.json", default={})
    value = policy.get("gate_sets_by_kind", {})
    if not isinstance(value, dict):
        return {}
    return {
        str(kind): [str(item) for item in checks if isinstance(item, str)]
        for kind, checks in value.items()
        if isinstance(checks, list)
    }


def gate_set(kind: str, *, release: bool = False) -> list[str]:
    """Read the live gate table; fall back to the in-code table only if absent."""
    policy_sets = _policy_gate_sets()
    key = "release" if release else kind
    if key in policy_sets:
        return list(policy_sets[key])
    if kind == "DOC":
        selected = list(DOC_CHECKS)
    elif kind in {"FIX", "FEATURE"}:
        selected = list(FIX_FEATURE_CHECKS)
    elif kind == "SECURITY":
        selected = [*FIX_FEATURE_CHECKS, *SECURITY_CHECKS]
    else:
        raise FactoryError(f"no gate set for task kind {kind!r}")
    if release:
        selected.extend(RELEASE_CHECKS)
    return selected


def _configured_narrowing(task: dict[str, Any]) -> list[str] | None:
    candidates = [
        Path(task.get("worktree", ".")) / "gate.json",
        Path(task.get("worktree", ".")) / ".factory" / "gate.json",
        Path(task.get("worktree", ".")) / "factory-gate.json",
    ]
    for path in candidates:
        value = read_json(path, required=False)
        if not isinstance(value, dict):
            continue
        checks = value.get("selected_checks", value.get("gate_checks", value.get("checks")))
        if isinstance(checks, list):
            names = []
            for item in checks:
                name = item.get("check") if isinstance(item, dict) else item
                if isinstance(name, str):
                    names.append(name)
            return names
    return None


def _check_levels() -> dict[str, str]:
    """Map each check to its gate-hierarchy level (mechanical levels only)."""
    policy = load_policy("gate-hierarchy.json", default={})
    levels = policy.get("levels", {}) if isinstance(policy, dict) else {}
    mapping: dict[str, str] = {}
    for name, level in levels.items():
        for check in (level.get("checks") or []) if isinstance(level, dict) else []:
            mapping[str(check)] = str(name)
    # Debt and exception are rules over existing checks: they never displace
    # the mechanical level that owns the check.
    for name, level in levels.items():
        if not isinstance(level, dict) or level.get("deviable") is not True:
            continue
        for check in level.get("applies_to") or []:
            mapping.setdefault(str(check), str(name))
    return mapping


def _level_order() -> dict[str, int]:
    policy = load_policy("gate-hierarchy.json", default={})
    levels = policy.get("levels", {}) if isinstance(policy, dict) else {}
    return {
        str(name): int(level.get("order", 99))
        for name, level in levels.items()
        if isinstance(level, dict)
    }


def _shadow_checks() -> set[str]:
    policy = load_policy("check-runners.json", default={})
    return {
        name
        for name, entry in policy.get("checks", {}).items()
        if isinstance(entry, dict) and entry.get("shadow") is True
    }


def execute_check(
    check_id: str,
    task_id: str,
    *,
    seed: int | None = None,
    digest: str | None = None,
) -> dict[str, Any]:
    task = load_task(task_id)
    proof = run_check(check_id, task, seed=seed, policy_digest=digest)
    path = save_proof(task_id, proof)
    proof["proof_path"] = str(path)
    return proof


def run_gate(
    task_id: str,
    *,
    release: bool = False,
    strict_shadow: bool = False,
    seed: int | None = None,
    checks: list[str] | None = None,
) -> dict[str, Any]:
    task = load_task(task_id)
    narrowed_by_cli = checks is not None
    if release and narrowed_by_cli:
        raise FactoryError("a release gate cannot be narrowed with --checks")
    required_checks = gate_set(task["kind"], release=release)
    narrowing = checks if narrowed_by_cli else _configured_narrowing(task)
    if narrowing is not None:
        illegal = sorted(set(narrowing) - set(required_checks))
        if illegal:
            raise FactoryError(
                "gate.json may narrow but not widen the policy gate; "
                f"unexpected checks: {', '.join(illegal)}"
            )
        selected_checks = [name for name in required_checks if name in set(narrowing)]
    else:
        selected_checks = required_checks

    shadow_set = _shadow_checks()
    levels = _check_levels()
    mandatory_checks = [
        name
        for name in required_checks
        if name not in shadow_set or strict_shadow
    ]
    before_hash = policy_hash()
    proofs: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    for check_id in selected_checks:
        proof = run_check(check_id, task, seed=seed, policy_digest=before_hash)
        saved = save_proof(task_id, proof)
        is_shadow = check_id in shadow_set
        required = not is_shadow or strict_shadow
        if proof.get("result") == "SKIP" and not (is_shadow and strict_shadow):
            # A runner's explicit SKIP denotes a non-applicable check (for
            # example, lint with no changed Python files), not a pass claim.
            required = False
        outcomes.append(
            {
                "check": check_id,
                "level": levels.get(check_id, "unclassified"),
                "result": proof.get("result"),
                "required": required,
                "shadow": is_shadow,
                "detail": proof.get("detail", ""),
                "proof_path": str(saved),
            }
        )
        proofs.append(proof)

    order = _level_order()
    position = {name: index for index, name in enumerate(selected_checks)}
    outcomes.sort(key=lambda item: (order.get(item["level"], 99), position.get(item["check"], 0)))

    after_hash = policy_hash()
    hash_stable = before_hash == after_hash
    required_failures = [
        outcome
        for outcome in outcomes
        if outcome["required"] and outcome["result"] != "PASS"
    ]
    selected_set = set(selected_checks)
    if not narrowed_by_cli:
        for check_id in mandatory_checks:
            if check_id not in selected_set:
                required_failures.append(
                    {
                        "check": check_id,
                        "result": "MISSING",
                        "required": True,
                        "shadow": check_id in shadow_set,
                        "detail": "required check was omitted by gate.json",
                    }
                )
    if not hash_stable:
        required_failures.append(
            {
                "check": "POLICY_HASH",
                "result": "FAIL",
                "required": True,
                "shadow": False,
                "detail": "policy_hash changed while checks were running",
            }
        )
    root = proof_root(proofs)
    record = {
        "task_id": task_id,
        "kind": task["kind"],
        "created_at": utc_now(),
        "release": release,
        "narrowed": narrowed_by_cli,
        "strict_shadow": strict_shadow,
        "policy_hash": before_hash,
        "policy_hash_after": after_hash,
        "policy_hash_stable": hash_stable,
        "selected_checks": selected_checks,
        "hierarchy": {name: levels.get(name, "unclassified") for name in selected_checks},
        "checks": outcomes,
        "proof_root": root,
        "result": "PASS" if not required_failures else "FAIL",
        "required_failures": [
            {
                "check": failure["check"],
                "result": failure["result"],
                "detail": failure.get("detail", ""),
            }
            for failure in required_failures
        ],
    }
    path = evidence_dir(task_id) / "gate.json"
    write_json(path, record)
    record["path"] = str(path)
    return record


def verify(task_id: str) -> dict[str, Any]:
    task = load_task(task_id)
    gate_path = evidence_dir(task_id) / "gate.json"
    gate_record = read_json(gate_path)
    if not isinstance(gate_record, dict):
        raise FactoryError(f"gate record must contain an object: {gate_path}")
    expected_names = gate_record.get("selected_checks", [])
    all_proofs = read_proofs(task_id)
    by_name = {proof.get("check"): proof for proof in all_proofs}
    proofs = [by_name[name] for name in expected_names if name in by_name]
    missing = [name for name in expected_names if name not in by_name]
    current_hash = policy_hash()
    actual_root = proof_root(proofs)
    root_matches = actual_root == gate_record.get("proof_root")
    gate_policy_matches = current_hash == gate_record.get("policy_hash")
    proof_policies_match = all(
        proof.get("policy_hash") == current_hash for proof in proofs
    )
    passed = (
        not missing
        and root_matches
        and gate_policy_matches
        and proof_policies_match
        and gate_record.get("policy_hash_stable") is True
    )
    return {
        "task_id": task_id,
        "result": "PASS" if passed else "FAIL",
        "proof_root": actual_root,
        "expected_proof_root": gate_record.get("proof_root"),
        "proof_root_matches": root_matches,
        "policy_hash": current_hash,
        "gate_policy_hash": gate_record.get("policy_hash"),
        "policy_hash_matches": gate_policy_matches and proof_policies_match,
        "missing_proofs": missing,
        "proof_count": len(proofs),
    }


def evidence(task_id: str) -> dict[str, Any]:
    directory = evidence_dir(task_id)
    gate_record = read_json(directory / "gate.json", required=False)
    all_proofs = read_proofs(task_id)
    if isinstance(gate_record, dict) and isinstance(gate_record.get("selected_checks"), list):
        by_name = {proof.get("check"): proof for proof in all_proofs}
        proofs = [
            by_name[name]
            for name in gate_record["selected_checks"]
            if name in by_name
        ]
    else:
        proofs = all_proofs
    return {
        "task_id": task_id,
        "proof_root": proof_root(proofs),
        "proofs": [
            {
                "check": proof.get("check"),
                "result": proof.get("result"),
                "path": str(proofs_dir(task_id) / f"{proof.get('check')}.json"),
            }
            for proof in proofs
        ],
        "gate": gate_record,
    }
