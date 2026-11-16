"""Proof-gated, no-ff release merge."""

from __future__ import annotations

import fnmatch
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
    load_policy,
    policy_hash,
    read_json,
    run,
    utc_now,
    write_json,
)

_DEFAULT_MAX_DELETED_LINES = 200
_DEFAULT_TEST_GLOBS = (
    "**/test_*.py",
    "**/*_test.py",
    "**/tests/**",
    "**/test/**",
    "**/*.test.ts",
    "**/*.test.tsx",
    "**/*.spec.ts",
    "**/*.spec.tsx",
)


def _git(args: list[str], repo: Path) -> Any:
    return run(["git", *args], cwd=repo)


def _matches_glob(path: str, pattern: str) -> bool:
    """Match a repo-relative path against a policy glob.

    Plain fnmatch lets ``**/`` require a slash, so a root-level ``test_x.py``
    would miss ``**/test_*.py``. Also try the pattern with the leading ``**/``
    removed so root-level files match.
    """
    if fnmatch.fnmatchcase(path, pattern):
        return True
    return pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:])


def _deletion_risk(
    repo: Path,
    target: str,
    branch: str,
    policy: dict[str, Any] | None,
) -> dict[str, Any]:
    """Detect a divergent branch that deletes tests or too many lines.

    Read-only: computes the merge base and the branch-side diff. It never
    merges, resets, or checks anything out.
    """
    guard = policy if isinstance(policy, dict) else {}
    try:
        max_deleted_lines = int(guard.get("max_deleted_lines", _DEFAULT_MAX_DELETED_LINES))
    except (TypeError, ValueError):
        max_deleted_lines = _DEFAULT_MAX_DELETED_LINES
    test_globs = guard.get("test_file_globs") or list(_DEFAULT_TEST_GLOBS)
    if not isinstance(test_globs, list):
        test_globs = list(_DEFAULT_TEST_GLOBS)

    result: dict[str, Any] = {
        "target": target,
        "branch": branch,
        "merge_base": None,
        "divergent": False,
        "deleted_paths": [],
        "deleted_test_files": [],
        "deleted_lines": 0,
        "max_deleted_lines": max_deleted_lines,
        "test_file_globs": test_globs,
        "risk": False,
    }

    merge_base = git_output(["merge-base", target, branch], cwd=repo)
    if not merge_base:
        return result
    result["merge_base"] = merge_base
    target_sha = git_output(["rev-parse", target], cwd=repo)
    branch_sha = git_output(["rev-parse", branch], cwd=repo)
    # A branch already contained in the target (merge base == branch tip) has no
    # branch-side commits to review. Anything else contributes a diff that a
    # merge would apply, including the strictly-ahead case where only the
    # branch moved; both can delete tests, so both are reviewed.
    if not target_sha or not branch_sha or merge_base == branch_sha:
        return result
    result["divergent"] = True

    name_status = _git(
        [
            "-c",
            "core.quotePath=false",
            "diff",
            "--name-status",
            "--no-renames",
            "-z",
            merge_base,
            branch,
        ],
        repo,
    )
    numstat = _git(
        [
            "-c",
            "core.quotePath=false",
            "diff",
            "--numstat",
            "--no-renames",
            "-z",
            merge_base,
            branch,
        ],
        repo,
    )
    if name_status.returncode or numstat.returncode:
        detail = (
            name_status.stderr.strip()
            or numstat.stderr.strip()
            or "git diff against the merge base failed"
        )
        result["error"] = detail
        result["risk"] = True
        return result

    # -z gives an unambiguous NUL-delimited stream, so filenames containing
    # tabs, quotes, backslashes, or newlines are never C-quoted or split.
    # --name-status -z emits STATUS\0path\0 pairs; --numstat -z emits
    # add\tdel\tpath\0 records (the path may itself contain tabs).
    deleted_paths: list[str] = []
    status_tokens = name_status.stdout.split("\0")
    index = 0
    while index < len(status_tokens):
        token = status_tokens[index]
        if not token:
            index += 1
            continue
        if "\t" in token:
            status, _, path = token.partition("\t")
            index += 1
        else:
            status = token
            path = status_tokens[index + 1] if index + 1 < len(status_tokens) else ""
            index += 2
        if path and status.startswith("D"):
            deleted_paths.append(path)

    deleted_lines = 0
    for record in numstat.stdout.split("\0"):
        if not record:
            continue
        fields = record.split("\t", 2)
        if len(fields) == 3 and fields[1].isdigit():
            deleted_lines += int(fields[1])

    deleted_test_files = [
        path
        for path in deleted_paths
        if any(_matches_glob(path, pattern) for pattern in test_globs)
    ]
    result["deleted_paths"] = deleted_paths
    result["deleted_test_files"] = deleted_test_files
    result["deleted_lines"] = deleted_lines
    result["risk"] = bool(deleted_test_files) or deleted_lines > max_deleted_lines
    return result


def release(
    task_id: str,
    *,
    repo: str | None = None,
    dry_run: bool = False,
    allow_deletions: bool = False,
    owner_approval: str | None = None,
    deletion_scope: list[str] | None = None,
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

    release_policy = load_policy("release-predicates.json")
    deletion_policy = (
        release_policy.get("deletion_guard")
        if isinstance(release_policy, dict)
        else None
    )
    deletion_risk = _deletion_risk(repo_root, current_branch, branch, deletion_policy)
    if deletion_risk["risk"]:
        if deletion_risk.get("error"):
            refuse(f"SILENT_DELETION_RISK: cannot evaluate branch deletions: {deletion_risk['error']}")
        risk_detail = (
            f"branch {branch!r} deletes {deletion_risk['deleted_lines']} lines "
            f"(limit {deletion_risk['max_deleted_lines']}) and test files "
            f"{deletion_risk['deleted_test_files']}"
        )
        if not allow_deletions:
            refuse(
                f"SILENT_DELETION_RISK: {risk_detail}; "
                "owner approval and explicit deletion scope are required"
            )
        if not (isinstance(owner_approval, str) and owner_approval.strip()):
            refuse(
                f"SILENT_DELETION_RISK: {risk_detail}; "
                "--allow-deletions requires a non-empty owner approval"
            )
        scopes = [
            scope
            for scope in (deletion_scope or [])
            if isinstance(scope, str) and scope.strip()
        ]
        if not scopes:
            refuse(
                f"SILENT_DELETION_RISK: {risk_detail}; "
                "--allow-deletions requires at least one deletion scope glob"
            )
        unscoped = [
            path
            for path in deletion_risk["deleted_paths"]
            if not any(_matches_glob(path, scope) for scope in scopes)
        ]
        if unscoped:
            refuse(
                f"SILENT_DELETION_RISK: {risk_detail}; deleted paths outside the "
                f"approved deletion scope: {', '.join(unscoped)}"
            )

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
