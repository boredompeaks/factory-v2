"""Validate and build the release-train hacker dispatch request.

The campaign is dispatched by a role, not by the factory, so a malformed
envelope or a drifted model pin used to fail as an opaque provider ``400``
before any probe ran. This module makes that failure local: it validates the
envelope shape, the model pin from ``policies/models.json``, the agent
frontmatter, and the prompt before the caller sends anything.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .util import REPO_ROOT, FactoryError, load_policy, read_json

MAX_PROMPT_CHARS = 200_000
SIG_STATUSES = {"unsigned_local", "INVALID", "ABSENT"}
PLAN_ITEM_KEYS = {"item_id", "class", "catalog_ref"}
CATALOG_REF = re.compile(r"^policies/attack-catalog\.json#/items/[A-Za-z0-9_-]+$")
RECORD_HASH = re.compile(r"^[a-f0-9]{64}$")
MODEL_PIN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:#[A-Za-z0-9_.-]+)?$")


def _non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_envelope(envelope: Any) -> list[str]:
    """Return every shape error in the campaign envelope, not just the first."""
    errors: list[str] = []
    if not isinstance(envelope, dict):
        return ["envelope must be a JSON object"]
    for key in ("capability", "attack_plan", "closure_ref", "nonce", "sig_status", "record_hash"):
        if key not in envelope:
            errors.append(f"missing required key: {key}")
    capability = envelope.get("capability")
    if not isinstance(capability, dict):
        errors.append("capability must be an object")
    else:
        for key in ("schema", "capability_id", "lineage_id", "base", "head", "tree", "scope", "tier"):
            if key not in capability:
                errors.append(f"capability is missing {key}")
        if capability.get("role") not in (None, "hacker"):
            errors.append("capability.role must be hacker")
    plan = envelope.get("attack_plan")
    if not isinstance(plan, list) or not plan:
        errors.append("attack_plan must be a non-empty array")
    else:
        for index, item in enumerate(plan):
            if not isinstance(item, dict):
                errors.append(f"attack_plan[{index}] must be an object")
                continue
            for key in PLAN_ITEM_KEYS:
                if not _non_empty(item.get(key)):
                    errors.append(f"attack_plan[{index}] is missing {key}")
            catalog_ref = item.get("catalog_ref")
            if _non_empty(catalog_ref) and not CATALOG_REF.match(catalog_ref):
                errors.append(f"attack_plan[{index}].catalog_ref is not a catalog item ref")
            if _non_empty(item.get("class")) and not re.match(r"^[A-Z][A-Z0-9_]+$", item["class"]):
                errors.append(f"attack_plan[{index}].class must be an upper-case token")
    if not _non_empty(envelope.get("closure_ref")):
        errors.append("closure_ref must be a non-empty string")
    nonce = envelope.get("nonce")
    if not isinstance(nonce, str) or len(nonce) < 16:
        errors.append("nonce must be at least 16 characters")
    if envelope.get("sig_status") not in SIG_STATUSES:
        errors.append("sig_status must be unsigned_local, INVALID, or ABSENT")
    if not isinstance(envelope.get("record_hash"), str) or not RECORD_HASH.match(envelope.get("record_hash") or ""):
        errors.append("record_hash must be 64 lower-case hex characters")
    return errors


def _policy_model(variant: str) -> str:
    roles = load_policy("models.json").get("roles", {})
    role = roles.get(f"hacker-{variant}")
    if not isinstance(role, dict) or not role.get("provider") or not role.get("model"):
        raise FactoryError(f"policies/models.json has no hacker-{variant} role")
    model = f"{role['provider']}/{role['model']}"
    if role.get("variant"):
        model = f"{model}#{role['variant']}"
    if variant == "max" and role.get("variant") != "max":
        raise FactoryError(f"hacker-max must pin the max variant, got {model!r}")
    return model


def _agent_model() -> str:
    path = REPO_ROOT / "agents" / "hacker.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FactoryError(f"cannot read hacker persona: {exc}") from exc
    match = re.search(r"^model:\s*(\S+)\s*$", text, re.MULTILINE)
    if not match:
        raise FactoryError("agents/hacker.md has no model pin")
    return match.group(1)


def campaign_request(envelope_path: str | Path, prompt_path: str | Path, variant: str) -> dict[str, Any]:
    """Validate the envelope and return the exact ``subagent`` request fields."""
    if variant not in {"high", "max"}:
        raise FactoryError("hacker variant must be high or max")
    envelope_file = Path(envelope_path).expanduser()
    try:
        envelope = read_json(envelope_file)
    except FactoryError as exc:
        raise FactoryError(f"cannot read campaign envelope: {exc}") from exc
    errors = validate_envelope(envelope)
    if errors:
        raise FactoryError("invalid campaign envelope: " + "; ".join(errors))

    model = _policy_model(variant)
    agent_model = _agent_model()
    # The persona default is the max pin; a high campaign overrides it in the
    # dispatch. When max is requested the default must not have drifted.
    if variant == "max" and agent_model != model:
        raise FactoryError(
            f"agents/hacker.md pins {agent_model!r} but policies/models.json role "
            f"hacker-{variant} is {model!r}; run tools/sync_agent_models.py"
        )
    if not MODEL_PIN.match(model):
        raise FactoryError(f"model pin is not provider/model[#variant]: {model!r}")

    prompt_file = Path(prompt_path).expanduser()
    try:
        prompt = prompt_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise FactoryError(f"cannot read campaign prompt: {exc}") from exc
    if not prompt.strip():
        raise FactoryError("campaign prompt is empty")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise FactoryError(f"campaign prompt is {len(prompt)} characters; cap is {MAX_PROMPT_CHARS}")
    if "\x00" in prompt:
        raise FactoryError("campaign prompt contains a NUL byte")

    capability = envelope["capability"] if isinstance(envelope, dict) else {}
    pinned = capability.get("hacker_variant")
    if pinned not in (None, variant):
        raise FactoryError(f"envelope pins hacker_variant {pinned!r}, dispatch variant is {variant!r}")

    # Exactly the native subagent fields; no envelope key is forwarded, because
    # an unknown parameter is itself a dispatch-time rejection.
    return {
        "agent": "hacker",
        "description": "release-train hacker campaign",
        "model": model,
        "prompt": prompt,
    }
