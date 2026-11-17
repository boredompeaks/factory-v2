"""Counting rules for the hacker probe audit.

The bug this locks down: a campaign on SQLite reported 9 of 10 RACE-ORDERS
probes because seven concurrent-writer executions died with
``OperationalError``. An engine fault is not coverage, and a class the item
does not declare must never pad the total.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("probe_audit", ROOT / "tools" / "probe_audit.py")
probe_audit = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(probe_audit)

CATALOG = json.loads((ROOT / "policies" / "attack-catalog.json").read_text(encoding="utf-8"))
CONCURRENT = {"double_spend", "stock_oversell", "concurrent_cancel", "watermark_regression"}


def record(probe_class, engine, outcome, endpoint="/api/orders", tenants=("a", "b")):
    return {
        "schema": "probe-record/1",
        "item_id": "RACE-ORDERS",
        "probe_class": probe_class,
        "endpoint": endpoint,
        "tenant_pair": list(tenants),
        "engine": engine,
        "outcome": outcome,
        "evidence_ref": f"evidence/probe/{probe_class}-{engine}.json",
        "_file": "<memory>",
    }


def evaluate(records):
    return [probe_audit.classify(CATALOG, item) for item in records]


def test_sqlite_concurrent_writer_errors_are_engine_faults_not_coverage():
    records = [record(cls, "sqlite", "error") for cls in sorted(CONCURRENT)]
    report = probe_audit.build_item_report(CATALOG, "RACE-ORDERS", evaluate(records), "max")
    assert report["counted"] == 0
    assert report["status_counts"]["engine_fault"] == 4
    assert report["complete"] is False
    assert set(report["missing_probe_classes"]) == CONCURRENT


def test_off_catalog_classes_never_pad_the_total():
    # Nine blocked sqlite probes whose classes this item does not declare.
    off_catalog = ["cross_tenant", "id_swap", "wrong_role", "method_override",
                   "step_skip", "negative_amount", "over_refund", "client_price_override"]
    records = [record(cls, "sqlite", "blocked", endpoint=f"/api/x/{i}")
               for i, cls in enumerate(off_catalog + off_catalog[:1])]
    report = probe_audit.build_item_report(CATALOG, "RACE-ORDERS", evaluate(records), "max")
    assert report["counted"] == 0
    assert report["status_counts"]["off_catalog_class"] == 9
    assert report["complete"] is False


def test_postgres_executions_satisfy_the_item():
    records = [record(cls, "postgres", "blocked", endpoint=f"/api/ok/{i}")
               for i, cls in enumerate(sorted(CONCURRENT))]
    records += [record(cls, "postgres", "blocked", endpoint=f"/api/extra/{i}")
                for i, cls in enumerate(sorted(CONCURRENT) * 2)]
    report = probe_audit.build_item_report(CATALOG, "RACE-ORDERS", evaluate(records), "max")
    assert report["counted"] == 12
    assert report["complete"] is True
    assert report["shortfall"] == 0


def test_next_probe_plan_names_postgres_for_each_missing_class():
    records = [record(cls, "sqlite", "error") for cls in sorted(CONCURRENT)]
    evaluations = evaluate(records)
    report = probe_audit.build_item_report(CATALOG, "RACE-ORDERS", evaluations, "max")
    plan = probe_audit.build_plan(CATALOG, [report], evaluations)
    by_class = {item["probe_class"]: item for item in plan if item["item_id"] == "RACE-ORDERS"}
    assert set(by_class) == CONCURRENT
    assert all(item["required_engine"] == "postgres" for item in by_class.values())


def test_static_items_declare_no_runtime_engine():
    for item_id in ("PROMPT-INJECTION", "SECRET-EXPOSURE"):
        eligibility = CATALOG["items"][item_id]["engine_eligibility"]
        assert all(engines == ["none"] for engines in eligibility.values())
