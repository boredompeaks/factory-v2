"""Regression tests for the 2026-11-19 harness ticket.

Each test names one defect from
``orchestrator-scratch/tickets/HARNESS-20261119-FACTORY.md`` and pins the
behaviour that made it silent.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factory import campaign
from factory.checks import (
    is_test_path,
    static,
)
from factory.checks.tests import BOOT_REQUIRED_ENV_KEYS, _boot_env
from factory.proof import _call_runner
from factory.task import validate_dispatch
from factory.util import FactoryError


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    (path / "backend").mkdir()
    (path / "backend" / "app.py").write_text("old = 1\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "-c", "user.email=f@t", "-c", "user.name=f", "commit", "-q", "-m", "base")
    return path


def _head(repo: Path, text: str) -> tuple[str, str]:
    (repo / "backend" / "app.py").write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=f@t", "-c", "user.name=f", "commit", "-q", "-m", "head")
    return _git(repo, "rev-parse", "HEAD~1"), _git(repo, "rev-parse", "HEAD")


def _stub(tmp_path: Path, name: str, body: str) -> Path:
    binary = tmp_path / name
    binary.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    binary.chmod(0o755)
    return binary


# --- 1. BOOTTEST canonical env ---------------------------------------------

def test_boot_env_carries_alerting_keys_without_making_them_required(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "manage.py").write_text("", encoding="utf-8")
    env, _ = _boot_env(worktree, worktree / "manage.py", {"repo_root": str(worktree)})
    assert env["SENTRY_DSN"].startswith("https://factory-boottest@example.invalid/")
    assert env["ALERT_RECIPIENTS"] == "boottest@example.invalid"
    # A missing alerting key must not turn BOOTTEST into a SKIP.
    assert "SENTRY_DSN" not in BOOT_REQUIRED_ENV_KEYS
    assert "ALERT_RECIPIENTS" not in BOOT_REQUIRED_ENV_KEYS


# --- 3. tests_*.py classification ------------------------------------------

def test_tests_prefix_is_a_test_path():
    assert is_test_path("backend/orders/tests_webhooks.py")
    assert is_test_path("backend/orders/tests_refunds.py")
    assert is_test_path("backend/accounts/tests.py")
    assert not is_test_path("backend/orders/testimony.py")


# --- 4. CONVENTION cache ---------------------------------------------------

def test_convention_points_ruff_at_a_writable_cache(tmp_path, repo, monkeypatch):
    base, head = _head(repo, "old = 2\n")
    record = tmp_path / "cache-dir.txt"
    monkeypatch.setenv("FACTORY_TEST_RUFF_RECORD", str(record))
    _stub(
        tmp_path, "ruff",
        'if [ "$1" = "--version" ]; then echo "ruff 0.0.0"; exit 0; fi\n'
        'printf "%s" "$RUFF_CACHE_DIR" > "$FACTORY_TEST_RUFF_RECORD"\n'
        'echo "[]"\n',
    )
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    ctx = {
        "check": "CONVENTION", "repo_root": str(repo), "worktree": str(repo),
        "base": base, "head": head, "policies_dir": str(ROOT / "policies"),
    }
    proof = static.check_convention(ctx)
    assert proof["result"] == "PASS", proof["detail"]
    cache = record.read_text(encoding="utf-8")
    assert cache
    assert not cache.startswith(str(repo))


# --- 5. SEMGREP baseline ---------------------------------------------------

def _semgrep_ctx(repo: Path, base: str, head: str) -> dict:
    return {
        "check": "SEMGREP", "repo_root": str(repo), "worktree": str(repo),
        "base": base, "head": head, "policies_dir": str(ROOT / "policies"),
    }


def test_semgrep_verdict_is_baseline_relative(tmp_path, repo, monkeypatch):
    base, head = _head(repo, "old = 1\nnew = 2\n")
    _stub(
        tmp_path, "semgrep",
        'if [ "$1" = "--version" ]; then echo "1.0.0"; exit 0; fi\n'
        'case " $* " in *" --baseline-commit "*) echo \'{"results":[]}\'; exit 0;; esac\n'
        'echo \'{"results":[{"path":"backend/app.py","start":{"line":1},"end":{"line":1},"check_id":"old"}]}\'\n',
    )
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    proof = static.check_semgrep(_semgrep_ctx(repo, base, head))
    assert proof["result"] == "PASS", proof["detail"]
    assert "raw finding(s), 0 new" in proof["detail"]
    assert "--baseline-commit" in proof["command"]


def test_semgrep_falls_back_to_added_lines_when_baseline_is_unusable(tmp_path, repo, monkeypatch):
    base, head = _head(repo, "old = 1\nnew = 2\n")
    _stub(
        tmp_path, "semgrep",
        'if [ "$1" = "--version" ]; then echo "1.0.0"; exit 0; fi\n'
        'echo \'{"results":[{"path":"backend/app.py","start":{"line":1},"end":{"line":1},"check_id":"old"}]}\'\n',
    )
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    real_run = static.run_process

    def no_baseline(command, **kwargs):
        if command[:2] == ["git", "-C"] and "cat-file" in command:
            return {"command": command, "exit": 1, "stdout": "", "stderr": "", "timed_out": False, "error": None}
        return real_run(command, **kwargs)

    monkeypatch.setattr(static, "run_process", no_baseline)
    proof = static.check_semgrep(_semgrep_ctx(repo, base, head))
    # The finding is on a pre-existing line; only line 2 was added.
    assert proof["result"] == "PASS", proof["detail"]
    assert "baseline=added-lines" in proof["detail"]


# --- 6. SCHEMA_FUZZ env passthrough ---------------------------------------

def test_schema_runner_receives_exported_spec_env(repo, monkeypatch):
    from factory import checks

    captured: dict = {}

    def fake_run_check(ctx):
        captured.update(ctx)
        return {"result": "PASS", "detail": "ok"}

    monkeypatch.setattr(checks, "run_check", fake_run_check)
    monkeypatch.setenv("FACTORY_OPENAPI_SPEC", "/tmp/openapi.json")
    monkeypatch.setenv("FACTORY_SCHEMA_BASE_URL", "http://127.0.0.1:8081")
    _call_runner("SCHEMA_FUZZ", {"worktree": str(repo), "base_sha": None}, None, "host_required")
    assert captured["env"]["FACTORY_OPENAPI_SPEC"] == "/tmp/openapi.json"
    assert captured["env"]["FACTORY_SCHEMA_BASE_URL"] == "http://127.0.0.1:8081"


# --- 2. dispatch rules -----------------------------------------------------

def test_money_rule_covers_orders_and_the_module_file_forms():
    def ids(scopes):
        return [r["rule_id"] for r in validate_dispatch("SECURITY", scopes)]

    assert "DR-MONEY-01" in ids(["backend/orders/views/checkout.py"])
    assert "DR-MONEY-01" in ids(["backend/orders/webhooks.py"])
    assert "DR-SECURITY-01" in ids(["backend/orders/views.py"])
    assert "DR-MONEY-01" in ids(["backend/orders/tests_webhooks.py"])


# --- 7. postgres capacity --------------------------------------------------

def test_postgres_data_tmpfs_is_large_enough_for_repeated_suites():
    text = (ROOT / "docker" / "services" / "compose.yaml").read_text(encoding="utf-8")
    assert text.count("size=512m") == 2
    assert "size=128m" not in text


# --- 9. CHANGELOG re-mint lineage -----------------------------------------

def _changelog_repo(tmp_path: Path, trailer: str | None):
    repo = tmp_path / "changelog-repo"
    (repo / "backend" / "docs").mkdir(parents=True)
    (repo / "backend" / "app.py").write_text("old = 1\n", encoding="utf-8")
    (repo / "backend" / "docs" / "changes.md").write_text(
        "| task_id | date | paths | invariant | attack_class | tier |\n"
        "| --- | --- | --- | --- | --- | --- |\n",
        encoding="utf-8",
    )
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=f@t", "-c", "user.name=f", "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "backend" / "app.py").write_text("old = 2\n", encoding="utf-8")
    with (repo / "backend" / "docs" / "changes.md").open("a", encoding="utf-8") as handle:
        handle.write(
            "| T-OLD | 2026-11-19 | backend/app.py | "
            "The order endpoint refuses a duplicate capture. | AUTHZ | none |\n"
        )
    _git(repo, "add", "-A")
    args = ["-c", "user.email=f@t", "-c", "user.name=f", "commit", "-q", "-m", "re-mint"]
    if trailer:
        args.extend(["-m", f"Task-Id: {trailer}"])
    _git(repo, *args)
    return repo, base, _git(repo, "rev-parse", "HEAD")


def _changelog_ctx(repo: Path, base: str, head: str) -> dict:
    return {
        "check": "CHANGELOG", "repo_root": str(repo), "worktree": str(repo),
        "base": base, "head": head, "task_id": "T-NEW", "tier": "none",
        "policies_dir": str(ROOT / "policies"),
    }


def test_changelog_accepts_the_row_id_from_the_commit_trailer(tmp_path):
    repo, base, head = _changelog_repo(tmp_path, trailer="T-OLD")
    proof = static.check_changelog(_changelog_ctx(repo, base, head))
    assert proof["result"] == "PASS", proof["detail"]


def test_changelog_still_pins_an_untrailered_commit_to_the_live_task_id(tmp_path):
    repo, base, head = _changelog_repo(tmp_path, trailer=None)
    proof = static.check_changelog(_changelog_ctx(repo, base, head))
    assert proof["result"] == "FAIL"
    assert "does not match" in proof["detail"]


def test_changelog_rejects_a_trailer_that_disagrees_with_the_row(tmp_path):
    repo, base, head = _changelog_repo(tmp_path, trailer="T-OTHER")
    proof = static.check_changelog(_changelog_ctx(repo, base, head))
    assert proof["result"] == "FAIL"
    assert "does not match" in proof["detail"]


# --- 8. campaign request preflight ----------------------------------------

def _envelope(**overrides):
    doc = {
        "capability": {
            "schema": "capability/1",
            "capability_id": "cap-1",
            "lineage_id": "lin-1",
            "base": "0" * 40,
            "head": "1" * 40,
            "tree": "2" * 40,
            "scope": ["backend/orders/**"],
            "tier": "max",
            "hacker_variant": "max",
        },
        "attack_plan": [{
            "item_id": "RACE-ORDERS",
            "class": "RACE",
            "catalog_ref": "policies/attack-catalog.json#/items/RACE-ORDERS",
        }],
        "closure_ref": "closure-1",
        "nonce": "0123456789abcdef",
        "sig_status": "unsigned_local",
        "record_hash": "a" * 64,
    }
    doc.update(overrides)
    return doc


def test_campaign_request_returns_the_dispatch_shape(tmp_path):
    envelope = tmp_path / "envelope.json"
    prompt = tmp_path / "prompt.md"
    envelope.write_text(json.dumps(_envelope()), encoding="utf-8")
    prompt.write_text("Attack the train seams.\n", encoding="utf-8")
    request = campaign.campaign_request(envelope, prompt, "max")
    assert set(request) == {"agent", "description", "model", "prompt"}
    assert request["agent"] == "hacker"
    assert request["model"] == "openai/gpt-6-luna#max"
    assert request["prompt"] == "Attack the train seams.\n"


@pytest.mark.parametrize(
    "overrides",
    [
        {"attack_plan": []},
        {"record_hash": "nope"},
        {"nonce": "short"},
        {"sig_status": "SIGNED"},
    ],
)
def test_campaign_request_refuses_a_malformed_envelope(tmp_path, overrides):
    envelope = tmp_path / "envelope.json"
    prompt = tmp_path / "prompt.md"
    envelope.write_text(json.dumps(_envelope(**overrides)), encoding="utf-8")
    prompt.write_text("x\n", encoding="utf-8")
    with pytest.raises(FactoryError):
        campaign.campaign_request(envelope, prompt, "max")


def test_campaign_request_uses_the_high_policy_model(tmp_path):
    envelope = tmp_path / "envelope.json"
    prompt = tmp_path / "prompt.md"
    doc = _envelope()
    doc["capability"]["hacker_variant"] = "high"
    envelope.write_text(json.dumps(doc), encoding="utf-8")
    prompt.write_text("x\n", encoding="utf-8")
    request = campaign.campaign_request(envelope, prompt, "high")
    assert request["model"] == "nous/minimax-m3.1-flash"


def test_campaign_request_refuses_a_variant_mismatch(tmp_path):
    envelope = tmp_path / "envelope.json"
    prompt = tmp_path / "prompt.md"
    doc = _envelope()
    doc["capability"]["hacker_variant"] = "high"
    envelope.write_text(json.dumps(doc), encoding="utf-8")
    prompt.write_text("x\n", encoding="utf-8")
    with pytest.raises(FactoryError):
        campaign.campaign_request(envelope, prompt, "max")
