"""The gate hierarchy: mechanical invariants, hygiene deltas, owned debt.

The hierarchy is only real if the policy partitions every check exactly once,
the delta checks ignore pre-existing state, and the deviation ledger refuses
silence, expiry, and a verdict that does not match the base tree.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factory.checks import complexity  # noqa: E402
from factory.checks import deviation  # noqa: E402


def policy(name: str) -> dict:
    return json.loads((ROOT / "policies" / name).read_text(encoding="utf-8"))


def hierarchy() -> dict:
    return policy("gate-hierarchy.json")


def block(name: str, complexity_value: int, rank: str, **extra) -> dict:
    value = {
        "type": "function",
        "name": name,
        "col_offset": 0,
        "lineno": 1,
        "endline": 20,
        "complexity": complexity_value,
        "rank": rank,
    }
    value.update(extra)
    return value


# --- policy contract -------------------------------------------------------

def test_every_check_has_exactly_one_level():
    levels = hierarchy()["levels"]
    checks = set(policy("checks.json")["checks"])
    mechanical = [
        str(name)
        for level in ("invariant", "hygiene")
        for name in (levels[level].get("checks") or [])
    ]
    assert len(mechanical) == len(set(mechanical)), "a check appears in two mechanical levels"
    # Debt is a rule over hygiene, not a level that owns checks; the exception
    # level owns the two containment checks it can excuse.
    assert set(levels["debt"]["applies_to"]) <= set(levels["hygiene"]["checks"])
    covered = set(mechanical) | set(levels["exception"]["applies_to"])
    assert covered == checks, {
        "unassigned": sorted(checks - covered),
        "unknown": sorted(covered - checks),
    }


def test_invariants_are_never_deviable():
    levels = hierarchy()["levels"]
    assert levels["invariant"]["deviable"] is False
    assert levels["hygiene"]["deviable"] is False
    assert levels["hygiene"]["mode"] == "delta"
    for name in ("debt", "exception"):
        assert levels[name]["deviable"] is True
    # An invariant may not be listed as an exception's applies_to target.
    exception_targets = set(levels["exception"]["applies_to"])
    assert not exception_targets & set(levels["invariant"]["checks"])


def test_debt_and_exception_are_ordered_after_hygiene():
    levels = hierarchy()["levels"]
    assert levels["invariant"]["order"] < levels["hygiene"]["order"]
    assert levels["hygiene"]["order"] < levels["debt"]["order"]
    assert levels["debt"]["order"] < levels["exception"]["order"]


def test_deviation_is_in_every_gate_set_that_runs_radon():
    gate = policy("gate.json")
    assert "DEVIATION" in gate["gate_checks"]
    for kind, checks in gate["gate_sets_by_kind"].items():
        if "RADON" in checks or "SCOPE" in checks:
            # SCOPE reads the declaration to excuse containment, so the
            # validating check must run in the same set.
            assert "DEVIATION" in checks, kind
    for tier in ("none", "high", "max"):
        assert "DEVIATION" in policy("tiers.json")[tier]["required_checks"]
    assert "DEVIATION" in policy("check-runners.json")["checks"]
    assert policy("check-runners.json")["checks"]["DEVIATION"]["shadow"] is False


def test_delta_checks_are_not_absolute_state():
    checks = policy("checks.json")["checks"]
    assert "delta" in checks["RADON"]["predicate"].lower()
    assert "delta" in checks["BANDIT"]["predicate"].lower()
    # The old absolute wording must be gone: it made pre-existing debt fail.
    assert "reports no function" not in checks["RADON"]["predicate"].lower()


# --- delta classification --------------------------------------------------

def test_classify_separates_new_crossed_worsened_residual():
    base = [block("stable", 12, "C"), block("border", 9, "B"), block("grown", 12, "C")]
    head = [
        block("stable", 12, "C"),
        block("border", 11, "C"),
        block("grown", 13, "C"),
        block("fresh", 14, "C"),
    ]
    result = complexity.classify(base, head)
    assert [item["head"]["name"] for item in result["new"]] == ["fresh"]
    assert [item["head"]["name"] for item in result["crossed"]] == ["border"]
    assert [item["head"]["name"] for item in result["worsened"]] == ["grown"]
    assert [item["head"]["name"] for item in result["residual"]] == ["stable"]


def test_classify_improved_and_removed_are_not_findings():
    result = complexity.classify(
        [block("shrunk", 15, "C")], [block("shrunk", 12, "C")]
    )
    assert result["improved"] and not result["new"] and not result["worsened"]


def test_block_identity_ignores_line_numbers():
    assert complexity.block_key(block("f", 12, "C", lineno=30)) == complexity.block_key(
        block("f", 12, "C", lineno=90)
    )


def test_overlap_uses_the_touched_hunk_only():
    value = block("f", 12, "C", lineno=100, endline=140)
    assert complexity.overlaps(value, [(120, 122)])
    assert not complexity.overlaps(value, [(1, 10)])


# --- declaration validation ------------------------------------------------

def obligations_fixture() -> dict:
    return {
        "paths": ["app.py", "helpers.py"],
        "outside_scope": ["helpers.py"],
        "debt": [
            {
                "check": "RADON",
                "path": "app.py",
                "block": {"type": "function", "name": "handler", "col_offset": 0},
                "base": block("handler", 12, "C"),
                "head": block("handler", 13, "C"),
            }
        ],
        "radon_state": {
            "base": {"app.py": [block("handler", 12, "C")]},
            "head": {"app.py": [block("handler", 13, "C")]},
        },
    }


def validate(item: dict, ctx: dict, obligations: dict | None = None):
    import datetime

    return deviation._validate_declaration(
        item,
        ctx,
        hierarchy(),
        obligations or obligations_fixture(),
        hierarchy()["limits"],
        set(hierarchy()["owners"]),
        datetime.date(2026, 11, 17),
    )


CTX = {"task_id": "T-1", "base": "0" * 40, "head": "1" * 40, "scope": ["app.py"]}


def debt_declaration(**overrides) -> dict:
    value = {
        "id": "D-001",
        "kind": "accepted_debt",
        "check": "RADON",
        "path": "app.py",
        "block": {"type": "function", "name": "handler", "col_offset": 0},
        "owner": "builder",
        "reason": "The new branch belongs to the existing handler and extracting it is a separate refactor.",
        "expires": "2026-12-10",
        "follow_up": "REFACTOR-2",
    }
    value.update(overrides)
    return value


def test_accepted_debt_for_a_pre_existing_block_is_valid():
    assert validate(debt_declaration(), CTX) == []


def test_new_debt_is_refused():
    obligations = obligations_fixture()
    obligations["radon_state"]["base"]["app.py"] = []
    errors = validate(debt_declaration(), CTX, obligations)
    assert any("does not exist at base" in error for error in errors)


def test_debt_that_improved_must_be_removed():
    obligations = obligations_fixture()
    obligations["radon_state"]["head"]["app.py"] = [block("handler", 11, "C")]
    errors = validate(debt_declaration(), CTX, obligations)
    assert any("improved at head" in error for error in errors)


def test_expired_or_overlong_debt_is_refused():
    assert any("expired" in error for error in validate(debt_declaration(expires="2026-11-01"), CTX))
    assert any("ceiling" in error for error in validate(debt_declaration(expires="2027-06-01"), CTX))


def test_debt_needs_a_known_owner_and_a_plan():
    assert any("known persona" in error for error in validate(debt_declaration(owner="nobody"), CTX))
    assert any("follow_up" in error for error in validate(debt_declaration(follow_up=""), CTX))


def test_reason_must_be_one_sentence():
    errors = validate(debt_declaration(reason="too short"), CTX)
    assert any("one sentence" in error for error in errors)


def test_invariant_cannot_be_deviated_even_with_a_valid_shape():
    errors = validate(debt_declaration(check="SUITE"), CTX)
    assert any("not covered by kind" in error or "invariant" in error for error in errors)


def refactor_declaration(**overrides) -> dict:
    value = {
        "id": "D-002",
        "kind": "forced_refactor",
        "check": "SCOPE",
        "paths": ["helpers.py"],
        "forcing": {
            "check": "RADON",
            "path": "app.py",
            "block": {"type": "function", "name": "handler", "col_offset": 0},
        },
        "owner": "builder",
        "reason": "Radon refuses the new branch inside handler, so the logic moves to a helper module.",
        "expires": "2026-12-10",
        "follow_up": "REFACTOR-2",
    }
    value.update(overrides)
    return value


def test_forced_refactor_is_valid_when_the_forcing_block_pre_exists():
    assert validate(refactor_declaration(), CTX) == []


def test_forced_refactor_without_base_finding_is_refused():
    obligations = obligations_fixture()
    obligations["radon_state"]["base"]["app.py"] = []
    errors = validate(refactor_declaration(), CTX, obligations)
    assert any("was not forced" in error for error in errors)


def test_forced_refactor_cannot_cover_factory_machinery():
    obligations = obligations_fixture()
    obligations["outside_scope"].append("policies/gate.json")
    obligations["paths"].append("policies/gate.json")
    declaration = refactor_declaration(paths=["helpers.py", "policies/gate.json"])
    errors = validate(declaration, CTX, obligations)
    assert any("factory machinery" in error for error in errors)


def test_forced_refactor_needs_an_out_of_scope_path():
    obligations = obligations_fixture()
    obligations["outside_scope"] = ["other.py"]
    errors = validate(refactor_declaration(), CTX, obligations)
    assert any("none of the paths is outside task scope" in error for error in errors)


def test_verdict_must_match_the_recomputed_base_state():
    declaration = refactor_declaration()
    obligations = obligations_fixture()
    digest = deviation._necessity_digest(declaration, CTX, obligations)
    verdict = {
        "deviation": "D-002",
        "auditor": "auditor",
        "verdict": "NECESSARY",
        "reason": "Reverting the extraction puts the branch back inside handler.",
        "falsifier": {"head": CTX["head"], "digest": digest},
    }
    _, errors = deviation._validate_verdicts(
        [declaration], {"verdicts": [verdict]}, CTX, obligations, hierarchy(), {"auditor"}
    )
    assert errors == []
    verdict["falsifier"]["digest"] = "0" * 64
    _, errors = deviation._validate_verdicts(
        [declaration], {"verdicts": [verdict]}, CTX, obligations, hierarchy(), {"auditor"}
    )
    assert any("digest does not match" in error for error in errors)


def test_avoidable_verdict_blocks_the_exception():
    declaration = refactor_declaration()
    obligations = obligations_fixture()
    digest = deviation._necessity_digest(declaration, CTX, obligations)
    verdict = {
        "deviation": "D-002",
        "auditor": "auditor",
        "verdict": "AVOIDABLE",
        "reason": "An in-scope helper would have passed.",
        "falsifier": {"head": CTX["head"], "digest": digest},
    }
    _, errors = deviation._validate_verdicts(
        [declaration], {"verdicts": [verdict]}, CTX, obligations, hierarchy(), {"auditor"}
    )
    assert any("not NECESSARY" in error for error in errors)


def test_verdict_author_must_be_the_auditor():
    declaration = refactor_declaration()
    obligations = obligations_fixture()
    digest = deviation._necessity_digest(declaration, CTX, obligations)
    verdict = {
        "deviation": "D-002",
        "auditor": "builder",
        "verdict": "NECESSARY",
        "reason": "trust me",
        "falsifier": {"head": CTX["head"], "digest": digest},
    }
    _, errors = deviation._validate_verdicts(
        [declaration], {"verdicts": [verdict]}, CTX, obligations, hierarchy(), {"auditor"}
    )
    assert any("authored by 'auditor'" in error for error in errors)


# --- end to end on a throwaway repository --------------------------------

def _radon_available() -> bool:
    if shutil.which("radon"):
        return True
    probe = subprocess.run(
        [sys.executable, "-m", "radon", "--version"],
        capture_output=True, text=True, check=False,
    )
    return probe.returncode == 0


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=factory@test", "-c", "user.name=factory", "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


BRANCHES = 11


def _handler(branches: int) -> str:
    lines = ["def handler(value):", "    total = 0"]
    for index in range(branches):
        lines.append(f"    if value == {index}:")
        lines.append(f"        total += {index}")
    lines.append("    return total")
    return "\n".join(lines) + "\n"


@pytest.mark.skipif(not _radon_available(), reason="radon is not available")
def test_radon_is_a_delta_and_deviation_demands_ownership(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "app.py").write_text(_handler(BRANCHES), encoding="utf-8")
    base = _commit(repo, "base")
    (repo / "app.py").write_text(_handler(BRANCHES + 1), encoding="utf-8")
    head = _commit(repo, "add a branch inside the existing function")

    monkeypatch.setenv("FACTORY_EVIDENCE_ROOT", str(tmp_path / "evidence"))
    ctx = {
        "task_id": "T-DELTA-1",
        "repo_root": str(repo),
        "worktree": str(repo),
        "policies_dir": str(ROOT / "policies"),
        "base": base,
        "head": head,
        "scope": ["app.py"],
        "tier": "high",
        "mode": "BUGFIX",
    }

    radon_proof = __import__("factory.checks", fromlist=["run_check"]).run_check("RADON", ctx)
    assert radon_proof["result"] == "PASS", radon_proof["detail"]
    assert "1 pre-existing block(s) worsened" in radon_proof["detail"]

    # Touching the block without owning it is refused.
    deviation_proof = __import__("factory.checks", fromlist=["run_check"]).run_check("DEVIATION", ctx)
    assert deviation_proof["result"] == "FAIL"
    assert "undeclared debt" in deviation_proof["detail"]

    # Declaring the debt with an owner and an expiry satisfies the ledger.
    evidence = tmp_path / "evidence" / "T-DELTA-1"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "deviations.json").write_text(json.dumps({
        "version": 1,
        "task_id": "T-DELTA-1",
        "deviations": [{
            "id": "D-001",
            "kind": "accepted_debt",
            "check": "RADON",
            "path": "app.py",
            "block": {"type": "function", "name": "handler", "col_offset": 0},
            "owner": "builder",
            "reason": "The branch belongs to the existing handler and extracting it is a separate refactor.",
            "expires": "2026-12-10",
            "follow_up": "REFACTOR-2",
        }],
    }), encoding="utf-8")
    deviation_proof = __import__("factory.checks", fromlist=["run_check"]).run_check("DEVIATION", ctx)
    assert deviation_proof["result"] == "PASS", deviation_proof["detail"]

    # A brand new hot spot is hygiene, not debt: no declaration can excuse it.
    (repo / "app.py").write_text(
        _handler(BRANCHES + 1) + "\n\ndef second(value):\n" + "\n".join(
            f"    if value == {index}:\n        value += {index}" for index in range(BRANCHES)
        ) + "\n    return value\n",
        encoding="utf-8",
    )
    head2 = _commit(repo, "add another hot spot")
    ctx["head"] = head2
    radon_proof = __import__("factory.checks", fromlist=["run_check"]).run_check("RADON", ctx)
    assert radon_proof["result"] == "FAIL"
    assert "1 new rank-C+ block(s)" in radon_proof["detail"]


@pytest.mark.skipif(not _radon_available(), reason="radon is not available")
def test_pure_rename_is_not_a_new_hot_spot(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "app.py").write_text(_handler(BRANCHES), encoding="utf-8")
    base = _commit(repo, "base")
    _git(repo, "mv", "app.py", "lib.py")
    head = _commit(repo, "move the module without editing it")
    ctx = {
        "task_id": "T-RENAME-1",
        "repo_root": str(repo),
        "worktree": str(repo),
        "policies_dir": str(ROOT / "policies"),
        "base": base,
        "head": head,
        "scope": ["app.py", "lib.py"],
        "tier": "high",
        "mode": "BUGFIX",
    }
    proof = __import__("factory.checks", fromlist=["run_check"]).run_check("RADON", ctx)
    assert proof["result"] == "PASS", proof["detail"]
    assert "0 new rank-C+ block(s)" in proof["detail"]
    # An exact rename has no touched lines, so it creates no debt obligation.
    ledger, error = deviation._obligations(ctx)
    assert error is None
    assert ledger["debt"] == []


@pytest.mark.skipif(not _radon_available(), reason="radon is not available")
def test_forced_refactor_excuses_scope_only_with_a_matching_verdict(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "app.py").write_text(_handler(BRANCHES), encoding="utf-8")
    base = _commit(repo, "base")
    (repo / "app.py").write_text(
        "from helpers import split\n\n" + _handler(BRANCHES), encoding="utf-8"
    )
    (repo / "helpers.py").write_text("def split(value):\n    return value\n", encoding="utf-8")
    head = _commit(repo, "extract a helper")

    monkeypatch.setenv("FACTORY_EVIDENCE_ROOT", str(tmp_path / "evidence"))
    ctx = {
        "task_id": "T-REFACTOR-1",
        "repo_root": str(repo),
        "worktree": str(repo),
        "policies_dir": str(ROOT / "policies"),
        "base": base,
        "head": head,
        "scope": ["app.py"],
        "tier": "high",
        "mode": "BUGFIX",
    }
    # Without a declaration, both containment and the ledger refuse the extra path.
    assert __import__("factory.checks", fromlist=["run_check"]).run_check("SCOPE", ctx)["result"] == "FAIL"
    assert __import__("factory.checks", fromlist=["run_check"]).run_check("DEVIATION", ctx)["result"] == "FAIL"

    declaration = {
        "id": "D-002",
        "kind": "forced_refactor",
        "check": "SCOPE",
        "paths": ["helpers.py"],
        "forcing": {
            "check": "RADON",
            "path": "app.py",
            "block": {"type": "function", "name": "handler", "col_offset": 0},
        },
        "owner": "builder",
        "reason": "The handler cannot take another branch, so the split moves to a helper module.",
        "expires": "2026-12-10",
        "follow_up": "REFACTOR-2",
    }
    obligations, error = deviation._obligations(ctx)
    assert error is None
    digest = deviation._necessity_digest(declaration, ctx, obligations)
    evidence = tmp_path / "evidence" / "T-REFACTOR-1"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "deviations.json").write_text(json.dumps({
        "version": 1, "task_id": "T-REFACTOR-1", "deviations": [declaration],
    }), encoding="utf-8")
    (evidence / "deviation-verdict.json").write_text(json.dumps({
        "version": 1,
        "task_id": "T-REFACTOR-1",
        "verdicts": [{
            "deviation": "D-002",
            "auditor": "auditor",
            "verdict": "NECESSARY",
            "reason": "Reverting the extraction puts the branch back inside handler.",
            "falsifier": {"head": head, "digest": digest},
        }],
    }), encoding="utf-8")

    assert __import__("factory.checks", fromlist=["run_check"]).run_check("SCOPE", ctx)["result"] == "PASS"
    proof = __import__("factory.checks", fromlist=["run_check"]).run_check("DEVIATION", ctx)
    assert proof["result"] == "PASS", proof["detail"]
