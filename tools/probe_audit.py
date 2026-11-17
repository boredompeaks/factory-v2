#!/usr/bin/env python3
"""Audit probe-record/1 executions against policies/attack-catalog.json.

A RACE-ORDERS max campaign reported 9 of 10 required probes because four
concurrent-writer classes (double_spend, stock_oversell, concurrent_cancel,
watermark_regression) were executed against SQLite and died with
``sqlite3.OperationalError: database is locked`` debug-500s. SQLite serializes
writers, so those failures are ENGINE faults, not application gates, and never
coverage: the classes must be rerun on PostgreSQL in the same campaign.

Coverage is recomputed here from the catalog (engine_policy plus each item's
engine_eligibility) instead of trusting a caller-authored count. A counted
execution is a distinct (item_id, probe_class, endpoint, tenant_pair, engine)
record with outcome blocked|reproduced whose engine is eligible for its class;
``error`` never counts and ineligible-engine executions are reported as
engine_fault/engine_ineligible. The output includes a next_probe_plan naming
the class and engine the next campaign must run.

Exit codes: 0 = every selected item complete, 1 = shortfall, 2 = tool/input
error.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from pathlib import Path

TOOL = "probe-audit"
VERSION = "1.0.0"
SCHEMA = "probe-audit/1"
DEFAULT_CATALOG = "policies/attack-catalog.json"
COUNTED_OUTCOMES = ("blocked", "reproduced")
ENGINES = ("none", "postgres", "sqlite")
OUTCOMES = ("blocked", "reproduced", "error")
REQUIRED_KEYS = (
    "schema",
    "item_id",
    "probe_class",
    "endpoint",
    "tenant_pair",
    "engine",
    "outcome",
    "evidence_ref",
)
KNOWN_KEYS = set(REQUIRED_KEYS) | {"engine_note"}
STATUS_ORDER = ("engine_fault", "engine_ineligible", "off_catalog_class", "error")


class ToolError(RuntimeError):
    """Bad catalog, bad CLI, or malformed record input (exit 2)."""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ToolError(message)


def load_catalog(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ToolError(f"cannot read catalog {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ToolError(f"catalog {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ToolError(f"catalog {path} must be a JSON object")
    items = data.get("items")
    if not isinstance(items, dict) or not items:
        raise ToolError(f"catalog {path} has no items object")
    for item_id, item in items.items():
        if not isinstance(item, dict):
            raise ToolError(f"catalog item {item_id} must be an object")
        if not isinstance(item.get("probe_classes"), list) or not item["probe_classes"]:
            raise ToolError(f"catalog item {item_id} has no probe_classes")
        if not isinstance(item.get("min_probes"), dict):
            raise ToolError(f"catalog item {item_id} has no min_probes")
        if item.get("engines") is not None and not isinstance(item["engines"], list):
            raise ToolError(f"catalog item {item_id} engines must be a list")
        for probe_class in item["probe_classes"]:
            if not isinstance(probe_class, str):
                raise ToolError(f"catalog item {item_id} has a non-string probe_class")
    policy = data.get("engine_policy")
    if policy is not None and not isinstance(policy, dict):
        raise ToolError(f"catalog {path} engine_policy must be an object")
    return data


def catalog_classes(catalog: dict) -> set[str]:
    classes: set[str] = set()
    for item in catalog["items"].values():
        classes.update(item["probe_classes"])
    return classes


def required_engine_for(catalog: dict, probe_class: str) -> str | None:
    policy = catalog.get("engine_policy") or {}
    required = policy.get("required_engine_by_class") or {}
    engine = required.get(probe_class)
    return engine if isinstance(engine, str) else None


def eligible_engines(catalog: dict, item_id: str, probe_class: str) -> list[str]:
    """Engines on which ``probe_class`` may produce a counted execution."""
    item = catalog["items"][item_id]
    declared = item.get("engines") or []
    # Static-scan items declare engines ["none"]; engine_eligibility (added for
    # runtime classes) must not make their own engine ineligible.
    if "none" in declared and not {"sqlite", "postgres"} & set(declared):
        return ["none"]
    eligibility = item.get("engine_eligibility") or {}
    if probe_class in eligibility:
        engines = eligibility[probe_class]
        if isinstance(engines, list) and engines:
            return [str(engine) for engine in engines]
    required = required_engine_for(catalog, probe_class)
    if required:
        return [required]
    return [str(engine) for engine in declared]


def iter_record_files(paths: list[str]) -> tuple[list[Path], set[str]]:
    files: list[Path] = []
    explicit: set[str] = set()
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(path.rglob("*.json")))
        elif path.is_file():
            files.append(path)
            explicit.add(str(path.resolve()))
        else:
            raise ToolError(f"records path not found: {raw}")
    return files, explicit


def validate_record(record: dict, catalog: dict, classes: set[str], where: str) -> None:
    missing = [key for key in REQUIRED_KEYS if key not in record]
    if missing:
        raise ToolError(f"{where}: missing required keys: {', '.join(missing)}")
    if record["schema"] != "probe-record/1":
        raise ToolError(f"{where}: unsupported schema {record['schema']!r}")
    if not isinstance(record["item_id"], str) or record["item_id"] not in catalog["items"]:
        raise ToolError(f"{where}: unknown item_id {record['item_id']!r}")
    if not isinstance(record["probe_class"], str) or record["probe_class"] not in classes:
        raise ToolError(f"{where}: unknown probe_class {record['probe_class']!r}")
    endpoint = record["endpoint"]
    if not isinstance(endpoint, str) or not endpoint.startswith("/") or any(
        char.isspace() for char in endpoint
    ):
        raise ToolError(f"{where}: endpoint must be a /-prefixed string without whitespace")
    tenant_pair = record["tenant_pair"]
    if (
        not isinstance(tenant_pair, list)
        or len(tenant_pair) != 2
        or not all(isinstance(pair, str) and pair for pair in tenant_pair)
    ):
        raise ToolError(f"{where}: tenant_pair must be two non-empty strings")
    if record["engine"] not in ENGINES:
        raise ToolError(f"{where}: engine must be one of {', '.join(ENGINES)}")
    if record["outcome"] not in OUTCOMES:
        raise ToolError(f"{where}: outcome must be one of {', '.join(OUTCOMES)}")
    evidence_ref = record["evidence_ref"]
    if not isinstance(evidence_ref, str) or not evidence_ref:
        raise ToolError(f"{where}: evidence_ref must be a non-empty string")
    note = record.get("engine_note")
    if note is not None and (not isinstance(note, str) or len(note) > 200):
        raise ToolError(f"{where}: engine_note must be a string of at most 200 characters")


def load_records(paths: list[str], catalog: dict) -> dict:
    files, explicit = iter_record_files(paths)
    classes = catalog_classes(catalog)
    executions: list[dict] = []
    non_record = 0
    ignored_keys: set[str] = set()
    for path in files:
        where = str(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            if where in explicit or str(path.resolve()) in explicit:
                raise ToolError(f"{where}: cannot parse JSON: {exc}") from exc
            non_record += 1
            continue
        if not isinstance(data, dict) or data.get("schema") != "probe-record/1":
            non_record += 1
            continue
        validate_record(data, catalog, classes, where)
        ignored_keys.update(set(data) - KNOWN_KEYS)
        record = dict(data)
        record["_file"] = where
        executions.append(record)
    return {
        "files_scanned": len(files),
        "executions": executions,
        "non_record_files": non_record,
        "ignored_keys": sorted(ignored_keys),
    }


def dedupe(executions: list[dict]) -> tuple[list[dict], list[dict]]:
    seen: dict[tuple, dict] = {}
    duplicates: list[dict] = []
    for record in executions:
        key = (
            record["item_id"],
            record["probe_class"],
            record["endpoint"],
            tuple(record["tenant_pair"]),
            record["engine"],
        )
        if key in seen:
            duplicates.append(
                {
                    "item_id": key[0],
                    "probe_class": key[1],
                    "endpoint": key[2],
                    "tenant_pair": list(key[3]),
                    "engine": key[4],
                    "kept": seen[key]["_file"],
                    "merged": record["_file"],
                }
            )
            continue
        seen[key] = record
    return list(seen.values()), duplicates


def classify(catalog: dict, record: dict) -> dict:
    declared = set(catalog["items"][record["item_id"]].get("probe_classes") or [])
    eligible = eligible_engines(catalog, record["item_id"], record["probe_class"])
    if record["probe_class"] not in declared:
        # A class the item does not declare cannot pad its total. It is a
        # bookkeeping error in the campaign, not coverage.
        status = "off_catalog_class"
        reason = (
            f"probe class {record['probe_class']} is not declared for "
            f"{record['item_id']}; it never counts toward min_probes"
        )
    elif record["engine"] not in eligible:
        status = "engine_fault" if record["outcome"] == "error" else "engine_ineligible"
        reason = (
            f"engine {record['engine']} is not eligible for {record['probe_class']} "
            f"(eligible: {', '.join(eligible)}); outcome {record['outcome']} never counts"
        )
    elif record["outcome"] == "error":
        status = "error"
        reason = f"outcome error never counts on any engine (engine: {record['engine']})"
    else:
        status = "counted"
        reason = f"outcome {record['outcome']} on eligible engine {record['engine']}"
    return {
        "item_id": record["item_id"],
        "probe_class": record["probe_class"],
        "endpoint": record["endpoint"],
        "tenant_pair": list(record["tenant_pair"]),
        "engine": record["engine"],
        "outcome": record["outcome"],
        "evidence_ref": record["evidence_ref"],
        "status": status,
        "eligible_engines": eligible,
        "reason": reason,
        "file": record["_file"],
    }


def build_item_report(
    catalog: dict, item_id: str, evaluations: list[dict], variant: str
) -> dict:
    item = catalog["items"][item_id]
    declared = list(item["probe_classes"])
    min_probes = item["min_probes"].get(variant)
    if not isinstance(min_probes, int):
        raise ToolError(f"item {item_id} has no min_probes[{variant}]")
    mine = [entry for entry in evaluations if entry["item_id"] == item_id]
    counted = [entry for entry in mine if entry["status"] == "counted"]
    counted_by_class: dict[str, int] = {probe_class: 0 for probe_class in declared}
    for entry in counted:
        counted_by_class[entry["probe_class"]] = counted_by_class.get(entry["probe_class"], 0) + 1
    missing = [probe_class for probe_class in declared if counted_by_class[probe_class] == 0]
    excluded = [entry for entry in mine if entry["status"] != "counted"]
    faults = [entry for entry in excluded if entry["status"] in STATUS_ORDER]

    status_counts: dict[str, int] = {}
    for entry in mine:
        status_counts[entry["status"]] = status_counts.get(entry["status"], 0) + 1

    reasons: list[str] = []
    if len(counted) < min_probes:
        reasons.append(f"below_min_probes: {len(counted)} < {min_probes}")
    for probe_class in missing:
        reasons.append(f"missing_probe_class: {probe_class}")

    off_catalog = sorted({entry["probe_class"] for entry in mine} - set(declared))
    return {
        "item_id": item_id,
        "variant": variant,
        "min_probes": min_probes,
        "counted": len(counted),
        "shortfall": max(0, min_probes - len(counted)),
        "complete": not missing and len(counted) >= min_probes,
        "probe_classes": OrderedDict(
            (probe_class, {
                "counted": counted_by_class[probe_class],
                "present": counted_by_class[probe_class] > 0,
                "eligible_engines": eligible_engines(catalog, item_id, probe_class),
            })
            for probe_class in declared
        ),
        "missing_probe_classes": missing,
        "off_catalog_probe_classes": off_catalog,
        "status_counts": OrderedDict(
            (status, status_counts[status])
            for status in ("counted",) + STATUS_ORDER
            if status in status_counts
        ),
        "engine_faults": [
            {
                "probe_class": entry["probe_class"],
                "engine": entry["engine"],
                "outcome": entry["outcome"],
                "endpoint": entry["endpoint"],
                "reason": entry["reason"],
            }
            for entry in faults
        ],
        "excluded": [
            {
                "probe_class": entry["probe_class"],
                "engine": entry["engine"],
                "outcome": entry["outcome"],
                "endpoint": entry["endpoint"],
                "tenant_pair": entry["tenant_pair"],
                "status": entry["status"],
                "reason": entry["reason"],
            }
            for entry in excluded
        ],
        "counted_executions": [
            {
                "probe_class": entry["probe_class"],
                "endpoint": entry["endpoint"],
                "tenant_pair": entry["tenant_pair"],
                "engine": entry["engine"],
                "outcome": entry["outcome"],
                "evidence_ref": entry["evidence_ref"],
            }
            for entry in counted
        ],
        "reasons": reasons,
    }


def plan_engine(catalog: dict, item_id: str, probe_class: str) -> str:
    eligible = eligible_engines(catalog, item_id, probe_class)
    return eligible[0] if len(eligible) == 1 else "any"


def build_plan(catalog: dict, reports: list[dict], evaluations: list[dict]) -> list[dict]:
    plan: list[dict] = []
    for report in reports:
        item_id = report["item_id"]
        if report["complete"]:
            continue
        mine = [entry for entry in evaluations if entry["item_id"] == item_id]
        for probe_class in report["missing_probe_classes"]:
            faults = [
                entry
                for entry in mine
                if entry["probe_class"] == probe_class and entry["status"] != "counted"
            ]
            if faults:
                engines = sorted({entry["engine"] for entry in faults})
                fault_statuses = sorted({entry["status"] for entry in faults})
                reason = (
                    f"no counted execution ({len(faults)} {'/'.join(fault_statuses)} "
                    f"on {', '.join(engines)}); rerun on the required engine"
                )
            else:
                reason = "no execution recorded for this probe_class"
            plan.append(
                {
                    "item_id": item_id,
                    "probe_class": probe_class,
                    "required_engine": plan_engine(catalog, item_id, probe_class),
                    "reason": reason,
                }
            )
        projected = report["counted"] + len(report["missing_probe_classes"])
        need = report["min_probes"] - projected
        present = [
            probe_class
            for probe_class, info in report["probe_classes"].items()
            if info["present"]
        ]
        present.sort(key=lambda probe_class: report["probe_classes"][probe_class]["counted"])
        for probe_class in present:
            if need <= 0:
                break
            plan.append(
                {
                    "item_id": item_id,
                    "probe_class": probe_class,
                    "required_engine": plan_engine(catalog, item_id, probe_class),
                    "reason": (
                        f"additional_executions_needed: projected counted {projected} "
                        f"< min_probes {report['min_probes']}"
                    ),
                }
            )
            need -= 1
    return plan


def audit(args) -> dict:
    catalog_path = Path(args.catalog)
    catalog = load_catalog(catalog_path)
    loaded = load_records(args.records, catalog)
    executions, duplicates = dedupe(loaded["executions"])
    evaluations = [classify(catalog, record) for record in executions]

    if args.item:
        unknown = [item for item in args.item if item not in catalog["items"]]
        if unknown:
            raise ToolError(f"unknown --item value(s): {', '.join(unknown)}")
        item_ids = list(OrderedDict.fromkeys(args.item))
    else:
        item_ids = list(catalog["items"])

    reports = [
        build_item_report(catalog, item_id, evaluations, args.variant)
        for item_id in item_ids
    ]
    plan = build_plan(catalog, reports, evaluations)
    complete = all(report["complete"] for report in reports)
    return {
        "tool": TOOL,
        "version": VERSION,
        "schema": SCHEMA,
        "catalog": str(catalog_path),
        "variant": args.variant,
        "records": {
            "files_scanned": loaded["files_scanned"],
            "non_record_files": loaded["non_record_files"],
            "executions": len(loaded["executions"]),
            "unique_executions": len(executions),
            "duplicates_merged": len(duplicates),
            "ignored_fields": loaded["ignored_keys"],
        },
        "items": reports,
        "next_probe_plan": plan,
        "summary": {
            "items": len(reports),
            "complete": sum(1 for report in reports if report["complete"]),
            "shortfall": sum(1 for report in reports if not report["complete"]),
        },
        "complete": complete,
    }


def human(report: dict) -> str:
    lines = [
        f"{TOOL} {VERSION} (variant={report['variant']}, catalog={report['catalog']})",
        (
            "records: {unique_executions} unique of {executions} execution(s) from "
            "{files_scanned} file(s); {duplicates_merged} duplicate(s) merged, "
            "{non_record_files} non-record file(s) ignored"
        ).format(**report["records"]),
    ]
    if report["records"]["ignored_fields"]:
        lines.append(
            "warning: ignored caller-authored field(s): "
            + ", ".join(report["records"]["ignored_fields"])
        )
    for item in report["items"]:
        verdict = "COMPLETE" if item["complete"] else "SHORTFALL"
        line = (
            f"{item['item_id']}  counted={item['counted']}/{item['min_probes']}  "
            f"{verdict}"
        )
        if item["missing_probe_classes"]:
            line += "  missing_classes=" + ",".join(item["missing_probe_classes"])
        faults = item["status_counts"].get("engine_fault", 0)
        if faults:
            line += f"  engine_fault={faults}"
        if item["off_catalog_probe_classes"]:
            line += "  off_catalog_classes=" + ",".join(item["off_catalog_probe_classes"])
        lines.append(line)
    lines.append("next probe plan:")
    if not report["next_probe_plan"]:
        lines.append("  (none: every selected item is complete)")
    for entry in report["next_probe_plan"]:
        lines.append(
            f"  {entry['item_id']}/{entry['probe_class']} -> {entry['required_engine']}: "
            f"{entry['reason']}"
        )
    lines.append(
        "result: {complete}/{items} item(s) complete -> {verdict}".format(
            verdict="PASS" if report["complete"] else "SHORTFALL",
            **report["summary"],
        )
    )
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = Parser(
        prog=TOOL,
        description=(
            "Audit probe-record/1 executions against attack-catalog.json and emit "
            "the next probe plan. Counted probes exclude outcome=error and any "
            "execution whose engine is ineligible for its probe class."
        ),
    )
    parser.add_argument(
        "--records",
        nargs="+",
        required=True,
        metavar="PATH",
        help="probe-record/1 JSON files or directories (searched recursively)",
    )
    parser.add_argument(
        "--catalog",
        default=DEFAULT_CATALOG,
        metavar="PATH",
        help=f"attack catalog path (default: {DEFAULT_CATALOG})",
    )
    parser.add_argument(
        "--variant",
        choices=("high", "max"),
        default="max",
        help="min_probes variant to audit (default: max)",
    )
    parser.add_argument(
        "--item",
        action="append",
        metavar="ITEM_ID",
        help="catalog item to audit; repeatable (default: every item)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit the full audit structure as JSON",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
        report = audit(args)
    except ToolError as exc:
        print(f"{TOOL}: error: {exc}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        print(human(report))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
