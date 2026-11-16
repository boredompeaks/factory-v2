"""Focused contract tests for the public factory CLI and proof helpers."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factory.proof import proof_root  # noqa: E402
from factory.scope import path_matches  # noqa: E402
from factory.util import canonical_json  # noqa: E402


def test_task_creation_writes_valid_task_json(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Factory Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "factory-test@example.invalid"],
        check=True,
    )
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)

    env = os.environ.copy()
    env["FACTORY_HOME"] = str(repo / ".factory")
    subprocess.run(
        [
            str(ROOT / "bin" / "factory"),
            "task",
            "new",
            "--kind",
            "DOC",
            "--lane",
            "spec-comp",
            "--scope",
            "README.md",
        ],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    task_path = next((repo / ".factory" / "tasks").glob("*/task.json"))
    task = json.loads(task_path.read_text(encoding="utf-8"))
    assert task["task_id"]
    assert task["kind"] == "DOC"
    assert "README.md" in task["scope"]


def test_proof_canonicalization_is_sorted_compact_utf8_json():
    encoded = canonical_json({"z": "é", "a": [2, 1]})
    assert encoded == '{"a":[2,1],"z":"é"}'
    assert isinstance(encoded, str)
    assert "\n" not in encoded


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("backend/orders/**", "backend/orders/service.py", True),
        ("backend/orders/**", "backend/billing/service.py", False),
        ("README.md", "README.md", True),
        ("README.md", "docs/README.md", False),
    ],
)
def test_scope_matching(pattern, path, expected):
    assert path_matches(path, pattern) is expected


def test_proof_root_is_stable_for_order_and_timing_fields():
    left = [
        {"check": "SCOPE", "result": "PASS", "duration_ms": 100},
        {"check": "CONVENTION", "result": "PASS", "timings": {"run": 1}},
    ]
    right = [
        {"timings": {"run": 99}, "result": "PASS", "check": "CONVENTION"},
        {"duration_ms": 12000, "result": "PASS", "check": "SCOPE"},
    ]
    assert proof_root(left) == proof_root(right)
    assert proof_root(left) == proof_root(list(reversed(left)))
