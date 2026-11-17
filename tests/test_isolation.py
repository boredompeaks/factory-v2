"""Contract tests for shared-test-service isolation.

The failure these lock down is real: two harness invocations on one PostgreSQL
server derived the same ``test_perfume_store`` name, so one run's teardown
dropped the database under the other. Unique per-run names plus a per-server
slot lease make that collision impossible.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factory.checks import resource_lease
from factory.checks import tests as tests_module


def test_unique_database_url_replaces_only_the_name(monkeypatch):
    monkeypatch.setenv("FACTORY_TEST_DB_TOKEN", "fixed_token")
    url = tests_module._unique_test_database_url(
        "postgresql://perfume:secret@127.0.0.1:5433/perfume_store?sslmode=disable",
        {"env": {"FACTORY_TEST_DB_TOKEN": "fixed_token"}},
    )
    assert url.startswith("postgresql://perfume:secret@127.0.0.1:5433/factory_fixed_token")
    assert url.endswith("?sslmode=disable")
    assert "perfume_store" not in url


def test_run_token_is_unique_and_validated():
    first = tests_module._run_db_token({"env": {}})
    second = tests_module._run_db_token({"env": {}})
    assert first != second
    assert first.startswith("t") and first.isalnum()
    assert tests_module._run_db_token({"env": {"FACTORY_TEST_DB_TOKEN": "bad-token"}}).startswith("t")


def test_lane_maps_to_its_own_server(monkeypatch):
    tasks = {"lane/a": {"lane": "lane/a"}, "lane/b": {"lane": "lane/b"}}
    monkeypatch.setattr(tests_module, "load_task", lambda task_id: tasks[task_id])
    assert tests_module._preferred_postgres_port({"task_id": "lane/a", "head": "x"}) == 5432
    assert tests_module._preferred_postgres_port({"task_id": "lane/b", "head": "x"}) == 5433


def test_unknown_lane_is_stable(monkeypatch):
    def missing(_task_id):
        from factory.util import FactoryError

        raise FactoryError("no task")

    monkeypatch.setattr(tests_module, "load_task", missing)
    ctx = {"task_id": "T-1", "head": "abc"}
    assert tests_module._preferred_postgres_port(ctx) == tests_module._preferred_postgres_port(ctx)


def test_postgres_pool_reads_policy(tmp_path, monkeypatch):
    policies = tmp_path / "policies"
    policies.mkdir()
    (policies / "concurrency.json").write_text(
        json.dumps({"postgres": {"test_database": {"slots_per_server": 2, "wait_s": 30}}}),
        encoding="utf-8",
    )
    ctx = {"policies_dir": str(policies)}
    assert tests_module._postgres_pool(ctx) == (2, 30.0)


def test_policy_declares_per_run_databases():
    policy = json.loads((ROOT / "policies" / "concurrency.json").read_text(encoding="utf-8"))
    database = policy["postgres"]["test_database"]
    assert database["mode"] == "per_run_unique"
    assert database["lane_servers"] == {"lane/a": 5432, "lane/b": 5433}
    assert database["slots_per_server"] >= 1


def test_resource_lease_serializes_a_single_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_LOCK_DIR", str(tmp_path / "locks"))
    order: list[float] = []
    started = time.monotonic()

    def hold() -> None:
        with resource_lease("demo", slots=1, wait=5):
            order.append(time.monotonic() - started)
            time.sleep(0.2)

    threads = [threading.Thread(target=hold) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert len(order) == 2
    assert max(order) - min(order) >= 0.15, order


def test_resource_lease_reports_busy_instead_of_colliding(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_LOCK_DIR", str(tmp_path / "locks"))
    with resource_lease("demo", slots=1, wait=1):  # noqa: SIM117 - the hold must outlive the expected refusal
        with pytest.raises(RuntimeError, match="RESOURCE_BUSY"):
            with resource_lease("demo", slots=1, wait=0.2):
                pass
