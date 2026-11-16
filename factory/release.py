"""Proof-gated, no-ff release merge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .gate import verify
from .proof import read_proofs
from .scope import check_scope
from .state import load_task
from .util import (
    FactoryError,
    evidence_dir,
    git_output,
    policy_hash,
    read_json,
    run,
    utc_now,
    write_json,
)


def _git(args: list[str], repo: Path) -> Any:
    return run(["git", *args], cwd=repo)


def release(
    task_id: str,
    *,
    repo: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    task = load_task(task_id)
    if not dry_run and (task.get("phase") != "GATE" or task.get("mode") != "RELEASE"):
        raise FactoryError("release requires task phase GATE and mode RELEASE")
    repo_root = Path(repo or Path.cwd()).expanduser().resolve()
    gate_path = evidence_dir(task_id) / "gate.json"
    gate_record = read_json(gate_path, required=False)
    existing_proofs = read_proofs(task_id)
    existing_by_name = {proof.get("check"): proof for proof in existing_proofs}

    def refuse(reason: str) -> None:
        scope_proof = existing_by_name.get("SCOPE", {})
        record = {
            "task_id": task_id,
            "result": "REFUSED",
            "reason": reason,
            "created_at": utc_now(),
            "proof_root": gate_record.get("proof_root") if isinstance(gate_record, dict) else None,
            "policy_hash": policy_hash(),
            "base": task.get("base_sha"),
            "head": scope_proof.get("head"),
            "tree": scope_proof.get("tree"),
        }
        write_json(evidence_dir(task_id) / "release.json", record)
        raise FactoryError(f"release refused: {reason}")

    if not isinstance(gate_record, dict):
        refuse("a passing gate record is required")
    if not dry_run and gate_record.get("release") is not True:
        refuse("a passing `factory gate --release` record is required")
    if gate_record.get("result") != "PASS":
        refuse("required gate checks did not PASS")

    verification = verify(task_id)
    if verification["result"] != "PASS":
        refuse(
            "proof verification failed "
            f"(missing={verification['missing_proofs']}, "
            f"policy_hash_matches={verification['policy_hash_matches']})"
        )
    current_hash = policy_hash()
    if gate_record.get("policy_hash") != current_hash:
        refuse("policy_hash does not match the gate record")

    scope_result = check_scope(task_id)
    if scope_result.get("result") != "PASS":
        refuse("scope check failed for " + ", ".join(scope_result.get("violations", [])))

    proof_by_name = {proof.get("check"): proof for proof in read_proofs(task_id)}
    outcomes = {
        item.get("check"): item
        for item in gate_record.get("checks", [])
        if isinstance(item, dict)
    }
    required_checks = [
        name
        for name in gate_record.get("selected_checks", [])
        if outcomes.get(name, {}).get("required", True)
    ]
    missing = [name for name in required_checks if name not in proof_by_name]
    failed = [
        name
        for name in required_checks
        if name in proof_by_name and proof_by_name[name].get("result") != "PASS"
    ]
    if "SCOPE" not in proof_by_name or proof_by_name["SCOPE"].get("result") != "PASS":
        failed.append("SCOPE")
    if missing or failed:
        refuse(f"missing required proofs={missing}; non-PASS proofs={sorted(set(failed))}")

    branch = task.get("branch")
    if not isinstance(branch, str) or not branch:
        refuse("task has no branch to merge")
    branch_check = _git(["check-ref-format", "--branch", branch], repo_root)
    if branch_check.returncode:
        refuse(f"task branch is not a valid git branch name: {branch!r}")
    current_branch = git_output(["branch", "--show-current"], cwd=repo_root)
    if not current_branch:
        refuse(f"release target is not a git worktree: {repo_root}")
    if current_branch == branch:
        refuse(
            f"release target is already on task branch {branch}; "
            "run release from the integration/base worktree"
        )
    status = git_output(["status", "--porcelain"], cwd=repo_root)
    if status:
        refuse("release target worktree is not clean; refusing to overwrite user changes")

    proof_root = gate_record["proof_root"]
    metadata = {
        "task_id": task_id,
        "proof_root": proof_root,
        "policy_hash": current_hash,
        "base": task.get("base_sha"),
        "head": proof_by_name["SCOPE"].get("head"),
        "tree": proof_by_name["SCOPE"].get("tree"),
        "branch": branch,
        "target_branch": current_branch,
        "dry_run": dry_run,
    }
    if dry_run:
        merge_tree = _git(["merge-tree", "--write-tree", "HEAD", branch], repo_root)
        if merge_tree.returncode:
            detail = merge_tree.stderr.strip() or merge_tree.stdout.strip()
            refuse(f"release dry-run merge failed: {detail}")
        metadata["merge_tree"] = merge_tree.stdout.strip().splitlines()[0] if merge_tree.stdout.strip() else ""
        metadata["result"] = "DRY_RUN"
        write_json(evidence_dir(task_id) / "release.json", metadata)
        return metadata

    merge = _git(["merge", "--no-ff", "--no-commit", "--no-edit", "--", branch], repo_root)
    if merge.returncode:
        detail = merge.stderr.strip() or merge.stdout.strip()
        raise FactoryError(f"git merge --no-ff failed; merge state may need attention: {detail}")
    message = f"Merge branch '{branch}' into {current_branch}"
    commit = _git(
        ["commit", "-m", message, "-m", f"Proof-Root: {proof_root}"],
        repo_root,
    )
    if commit.returncode:
        detail = commit.stderr.strip() or commit.stdout.strip()
        raise FactoryError(f"merge applied but commit failed; merge state may need attention: {detail}")
    metadata["result"] = "MERGED"
    metadata["merge_commit"] = git_output(["rev-parse", "HEAD"], cwd=repo_root)
    write_json(evidence_dir(task_id) / "release.json", metadata)
    return metadata
