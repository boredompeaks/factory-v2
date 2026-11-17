"""Policy contracts for the release-train hacker campaign and agent topology.

The campaign is one invocation per release train (the release-engineer runs at
the BUILD->GATE boundary, roughly every six tasks), not one per task. The only
other hacker edge is the auditor's BUG_FINDING sweep.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str) -> dict:
    return json.loads((ROOT / "policies" / name).read_text(encoding="utf-8"))


def subagent_rules(persona: str) -> list[tuple[str, str]]:
    text = (ROOT / "agents" / f"{persona}.md").read_text(encoding="utf-8")
    return [
        (match.group(1), match.group(2))
        for match in re.finditer(
            r'action:\s*subagent,\s*resource:\s*"([^"]+)",\s*effect:\s*(\w+)', text
        )
    ]


def test_campaign_is_one_invocation_per_release_train():
    campaign = load("attack-catalog.json")["campaign"]
    assert campaign["unit"] == "release_train"
    assert campaign["children_per_train"] == 1
    assert campaign["invocations_per_train"] == 1
    assert campaign["parent"] == "release-engineer"
    assert campaign["sweep_parent"] == "auditor"
    assert campaign["scope"] == "whole_train"
    assert campaign["cost_control"]["retries"] == 0
    assert "not once per task" in campaign["statement"]


def test_release_train_is_the_gate_boundary():
    modes = load("modes.json")
    assert "hacker" in modes["modes"]["RELEASE"]["roles"]
    assert "hacker" in modes["modes"]["BUG_FINDING"]["roles"]
    assert "hacker" not in modes["modes"]["AUDIT"]["roles"]
    assert modes["modes"]["RELEASE"]["phase"] == "GATE"
    assert modes["campaign"]["unit"] == "release_train"


def test_only_release_engineer_and_auditor_may_spawn_hacker():
    release = [rule for rule in subagent_rules("release-engineer") if rule[0] != "*"]
    auditor = [rule for rule in subagent_rules("auditor") if rule[0] != "*"]
    assert release == [("hacker", "allow")]
    assert auditor == [("hacker", "allow")]
    assert ("*", "deny") in subagent_rules("release-engineer")
    assert ("hacker", "deny") in subagent_rules("orchestrator")
    for persona in ("builder", "hacker", "compliance", "crouton-alpha"):
        assert ("*", "deny") in subagent_rules(persona)


def test_concurrent_writer_classes_are_postgres_only():
    catalog = load("attack-catalog.json")
    policy = catalog["engine_policy"]
    race = catalog["items"]["RACE-ORDERS"]["engine_eligibility"]
    for probe_class in policy["concurrent_writer_classes"]:
        assert race[probe_class] == ["postgres"], probe_class
    assert set(race) == set(catalog["items"]["RACE-ORDERS"]["probe_classes"])
    for item_id in ("PROMPT-INJECTION", "SECRET-EXPOSURE"):
        eligibility = catalog["items"][item_id]["engine_eligibility"]
        assert all(engines == ["none"] for engines in eligibility.values())


def test_gate_allows_only_the_single_train_campaign():
    phases = load("phases.json")
    gate = phases["phases"]["GATE"]
    assert gate["allowed_targets"] == ["release-engineer"]
    assert "hacker" not in gate["forbidden_targets"]
    assert "exactly one hacker campaign" in gate["hacker_campaign_rule"]
    assert "no second hacker" in gate["exclusivity_rule"]
    joined = " ".join(phases["invariants"])
    assert "has no child" not in joined
    assert "may not dispatch any role" not in joined
    assert "one hacker campaign per release train" in joined


def test_probe_concurrency_is_engine_aware():
    policy = load("concurrency.json")["probe"]
    assert policy["sqlite_concurrency_cap"] == 1
    assert policy["k1_concurrency_cap"] >= 16
    assert policy["host_postgres_concurrency_cap"] >= 4


def test_error_never_counts():
    policy = load("attack-catalog.json")["engine_policy"]
    assert "error" in policy["error_rule"].lower()
    assert "never counts" in policy["error_rule"].lower()
