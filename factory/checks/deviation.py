"""Declared deviations, owned debt, and the auditor's necessity cross-check.

The gate hierarchy in ``policies/gate-hierarchy.json`` gives every check a
level. Levels 0 and 1 are mechanical and never deviable: an invariant must
hold, and a hygiene check fails only on a regression the diff introduced. The
two lower levels turn state the author cannot fix in-scope into an explicit,
reviewable decision:

* ``debt`` - a pre-existing rank-C+ block the diff modifies. The author either
  improves it below the ceiling or declares ``accepted_debt`` with an owner and
  an expiry. Silence is not an option.
* ``exception`` - work outside the task scope, or a refactor a gate forces. It
  is allowed only by ``forced_refactor``, and only after an independent
  falsifier confirms the forcing condition already existed at ``base``.

Declarations live in the task evidence directory (``deviations.json``), never
in the product diff, so the merged branch carries only product changes. The
auditor's verdict is a second file (``deviation-verdict.json``): the check
recomputes the necessity digest and refuses a verdict that does not match the
base tree.
"""

from __future__ import annotations

import datetime as _datetime
import json
import re

from ..util import FactoryError, evidence_dir
from . import (
    canonical_json,
    changed_paths,
    complexity,
    glob_match,
    make_proof,
    policy_path,
    read_json,
    sha256,
)

DECLARATION_SCHEMA_VERSION = 1
DECLARATION_FILE = "deviations.json"
VERDICT_FILE = "deviation-verdict.json"


def _hierarchy(ctx: dict) -> dict:
    doc, error = read_json(policy_path(ctx, "gate-hierarchy.json"))
    if error or not isinstance(doc, dict) or not isinstance(doc.get("levels"), dict):
        raise FactoryError(f"gate-hierarchy.json is missing or malformed: {error or ''}".rstrip(": "))
    return doc


def _evidence(ctx: dict, name: str):
    task_id = str(ctx.get("task_id") or "")
    if not task_id:
        return None
    return evidence_dir(task_id) / name


def _load_optional(ctx: dict, name: str) -> tuple[dict | None, str]:
    path = _evidence(ctx, name)
    if path is None or not path.is_file():
        return None, ""
    try:
        text = path.read_text(encoding="utf-8")
        doc = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise FactoryError(f"cannot read {path}: {exc}") from None
    return doc, sha256(text)


def _one_sentence(text: str, minimum: int) -> bool:
    if len(text.strip()) < minimum:
        return False
    return len(re.findall(r"[.!?](?:\s|$)", text.strip())) == 1


def _valid_date(value: str) -> _datetime.date | None:
    try:
        return _datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def scope_exceptions(ctx: dict) -> tuple[set[str], list[dict]]:
    """Paths a well-formed ``forced_refactor`` declaration already excuses.

    Validation is deliberately shallow here; ``check_deviation`` re-validates
    the same declaration with the base tree and fails the gate if it is not
    sound. SCOPE only needs the path list so the two checks never disagree
    about which files the refactor covers.
    """
    try:
        hierarchy = _hierarchy(ctx)
        doc, _ = _load_optional(ctx, DECLARATION_FILE)
    except FactoryError:
        return set(), []
    if not isinstance(doc, dict):
        return set(), []
    blocked = [str(item) for item in hierarchy.get("gate_paths_never_deviable") or []]
    excused: set[str] = set()
    used: list[dict] = []
    for item in doc.get("deviations") or []:
        if not isinstance(item, dict) or item.get("kind") != "forced_refactor":
            continue
        paths = [str(path) for path in item.get("paths") or []]
        if any(any(glob_match(path, pattern) for pattern in blocked) for path in paths):
            continue
        excused.update(paths)
        used.append(item)
    return excused, used


def _block_ref(block: dict) -> dict:
    return {
        "type": block.get("type"),
        "name": block.get("name"),
        "col_offset": int(block.get("col_offset") or 0),
    }


def _block_identity(value) -> tuple[str, str, int] | None:
    if not isinstance(value, dict):
        return None
    name = value.get("name")
    if not isinstance(name, str):
        return None
    return (
        str(value.get("type") or ""),
        name,
        int(value.get("col_offset") or 0),
    )


def _block_by_identity(blocks: list[dict], identity) -> dict | None:
    for block in blocks:
        if complexity.block_key(block) == identity:
            return block
    return None


def _declared_identities(item: dict) -> list[tuple[str, str, int]]:
    blocks = item.get("blocks")
    if isinstance(blocks, list):
        return [identity for identity in map(_block_identity, blocks) if identity]
    identity = _block_identity(item.get("block"))
    return [identity] if identity else []


def _obligations(ctx: dict) -> tuple[dict, dict | None]:
    """Compute what the hierarchy says must be declared for this diff."""
    paths, _proc, error = changed_paths(ctx)
    if error:
        raise FactoryError(f"cannot determine changed paths: {error}")
    assert paths is not None
    scope = [str(pattern) for pattern in ctx.get("scope") or []]
    outside = [
        path for path in paths
        if scope and not any(glob_match(path, pattern) for pattern in scope)
    ]
    state, problem = complexity.radon_state(ctx)
    debt: list[dict] = []
    if problem:
        raise FactoryError(f"RADON base/head state unavailable: {problem.get('detail')}")
    assert state is not None
    # Key base blocks by the head path so a declaration for a renamed file
    # resolves against the blob it came from.
    renames = state.get("renames") or {}
    base_by_head = {
        path: state["base"].get(renames.get(path, path), [])
        for path in state.get("files") or []
    }
    for path, groups in complexity.per_file(state).items():
        hunks = complexity.changed_hunks(ctx, path, renames)
        for record in [*groups["worsened"], *groups["residual"]]:
            head = record["head"]
            if not complexity.overlaps(head, hunks):
                continue
            debt.append({
                "check": "RADON",
                "path": path,
                "block": _block_ref(head),
                "base": record["base"],
                "head": record["head"],
            })
    return {
        "paths": paths,
        "outside_scope": outside,
        "debt": debt,
        "radon_state": {"base": base_by_head, "head": state["head"]},
    }, None


def _validate_declaration(
    item: dict,
    ctx: dict,
    hierarchy: dict,
    obligations: dict,
    limits: dict,
    owners: set[str],
    today: _datetime.date,
) -> list[str]:
    errors: list[str] = []
    label = str(item.get("id") or "<unnamed>")
    kind = str(item.get("kind") or "")
    check = str(item.get("check") or "")
    levels = hierarchy.get("levels") or {}
    allowed_kinds: dict[str, list[str]] = {}
    for name, level in levels.items():
        if level.get("deviable") is True:
            for allowed in level.get("kinds") or []:
                allowed_kinds.setdefault(allowed, []).append(name)
    if kind not in allowed_kinds:
        return [f"{label}: kind {kind!r} is not deviable (no hierarchy level allows it)"]
    applies = {
        str(item)
        for name in allowed_kinds[kind]
        for item in (levels[name].get("applies_to") or [])
    }
    if check not in applies:
        return [f"{label}: check {check!r} is not covered by kind {kind!r}"]
    invariant = {
        str(item) for item in (levels.get("invariant", {}).get("checks") or [])
    }
    if check in invariant:
        errors.append(f"{label}: {check} is an invariant and can never be deviated")

    reason = str(item.get("reason") or "")
    if not _one_sentence(reason, int(limits.get("min_reason_chars") or 20)):
        errors.append(f"{label}: reason must be one sentence of at least {limits.get('min_reason_chars', 20)} characters")
    owner = str(item.get("owner") or "")
    if owner not in owners:
        errors.append(f"{label}: owner {owner!r} is not a known persona")
    expires = _valid_date(str(item.get("expires") or ""))
    if expires is None:
        errors.append(f"{label}: expires must be an ISO date")
    elif expires < today:
        errors.append(f"{label}: expired on {expires.isoformat()}")
    elif expires > today + _datetime.timedelta(days=int(limits.get("max_days") or 30)):
        errors.append(f"{label}: expiry exceeds the {limits.get('max_days', 30)}-day ceiling")
    if not str(item.get("follow_up") or "").strip():
        errors.append(f"{label}: follow_up must name where the debt is paid down")

    if kind == "accepted_debt":
        path = str(item.get("path") or "")
        if path not in obligations["paths"]:
            errors.append(f"{label}: path {path!r} is not in the diff")
        identities = _declared_identities(item)
        if not identities:
            errors.append(f"{label}: accepted_debt must name the block (name/type/col_offset)")
        base_blocks = obligations["radon_state"]["base"].get(path, [])
        head_blocks = obligations["radon_state"]["head"].get(path, [])
        for identity in identities:
            base = _block_by_identity(base_blocks, identity)
            head = _block_by_identity(head_blocks, identity)
            if base is None:
                errors.append(f"{label}: {path}:{identity[1]} does not exist at base; this is new debt, not accepted debt")
                continue
            if str(base.get("rank", "")).upper() not in complexity.CEILING_RANKS:
                errors.append(f"{label}: {path}:{identity[1]} was rank {base.get('rank')} at base; the diff introduced the finding")
            if head is None:
                errors.append(f"{label}: {path}:{identity[1]} is gone at head; remove the deviation")
                continue
            if int(head.get("complexity") or 0) < int(base.get("complexity") or 0):
                errors.append(f"{label}: {path}:{identity[1]} improved at head; remove the deviation")
    if kind == "forced_refactor":
        paths = [str(path) for path in item.get("paths") or []]
        if not paths:
            errors.append(f"{label}: forced_refactor must list the paths it covers")
        blocked = [str(pattern) for pattern in hierarchy.get("gate_paths_never_deviable") or []]
        for path in paths:
            if path not in obligations["paths"]:
                errors.append(f"{label}: path {path!r} is not in the diff")
            if any(glob_match(path, pattern) for pattern in blocked):
                errors.append(f"{label}: path {path!r} is factory machinery and is never deviable")
        if paths and not set(paths) & set(obligations["outside_scope"]):
            errors.append(f"{label}: none of the paths is outside task scope; declare nothing instead")
        forcing = item.get("forcing") if isinstance(item.get("forcing"), dict) else {}
        forcing_check = str(forcing.get("check") or "")
        hygiene = {
            str(item) for item in (levels.get("hygiene", {}).get("checks") or [])
        }
        allowed_forcing = {
            str(item)
            for item in (levels.get("exception", {}).get("forcing_checks") or [])
        }
        if forcing_check not in hygiene:
            errors.append(f"{label}: forcing.check must be a hygiene check, got {forcing_check!r}")
        elif forcing_check not in allowed_forcing:
            errors.append(
                f"{label}: forcing.check {forcing_check!r} has no base-state replay; "
                f"allowed: {sorted(allowed_forcing)}"
            )
        identity = _block_identity(forcing.get("block"))
        forcing_path = str(forcing.get("path") or "")
        if forcing_path not in obligations["paths"]:
            errors.append(f"{label}: forcing.path {forcing_path!r} is not in the diff")
        elif forcing_check == "RADON":
            if identity is None:
                errors.append(f"{label}: RADON forcing must name the block")
            else:
                base = _block_by_identity(
                    obligations["radon_state"]["base"].get(forcing_path, []), identity
                )
                if base is None:
                    errors.append(f"{label}: forcing block does not exist at base; the refactor was not forced")
                elif str(base.get("rank", "")).upper() not in complexity.CEILING_RANKS:
                    errors.append(
                        f"{label}: forcing block was rank {base.get('rank')} at base; not forced"
                    )
    return errors


def _validate_verdicts(
    declarations: list[dict],
    verdicts: dict | None,
    ctx: dict,
    obligations: dict,
    hierarchy: dict,
    owners: set[str],
) -> tuple[dict[str, dict], list[str]]:
    errors: list[str] = []
    by_id: dict[str, dict] = {}
    if not isinstance(verdicts, dict):
        verdicts = {"verdicts": []}
    for item in verdicts.get("verdicts") or []:
        if isinstance(item, dict) and item.get("deviation"):
            by_id[str(item["deviation"])] = item
    for declaration in declarations:
        if declaration.get("kind") != "forced_refactor":
            continue
        label = str(declaration.get("id") or "<unnamed>")
        verdict = by_id.get(label)
        if verdict is None:
            errors.append(f"{label}: forced_refactor requires an auditor verdict")
            continue
        author = str(hierarchy.get("verdict_author") or "")
        if str(verdict.get("auditor") or "") != author:
            errors.append(
                f"{label}: verdict must be authored by {author!r}, got {verdict.get('auditor')!r}"
            )
        if verdict.get("verdict") != "NECESSARY":
            errors.append(f"{label}: auditor verdict is {verdict.get('verdict')!r}, not NECESSARY")
        falsifier = verdict.get("falsifier") if isinstance(verdict.get("falsifier"), dict) else {}
        if str(falsifier.get("head") or "") != str(ctx.get("head") or ""):
            errors.append(f"{label}: verdict falsifier did not run at the audited head")
        expected = _necessity_digest(declaration, ctx, obligations)
        if str(falsifier.get("digest") or "") != expected:
            errors.append(
                f"{label}: verdict digest does not match the recomputed base state "
                f"(expected {expected[:16]}...)"
            )
        if not str(verdict.get("reason") or "").strip():
            errors.append(f"{label}: verdict must record the falsifier's reasoning")
    return by_id, errors


def _necessity_digest(declaration: dict, ctx: dict, obligations: dict) -> str:
    forcing = declaration.get("forcing") if isinstance(declaration.get("forcing"), dict) else {}
    path = str(forcing.get("path") or "")
    identity = _block_identity(forcing.get("block"))
    base = None
    if identity is not None:
        base = _block_by_identity(obligations["radon_state"]["base"].get(path, []), identity)
    payload = {
        "check": str(forcing.get("check") or ""),
        "base": str(ctx.get("base") or ""),
        "path": path,
        "block": list(identity) if identity else None,
        "base_complexity": (base or {}).get("complexity"),
        "base_rank": (base or {}).get("rank"),
    }
    return sha256(canonical_json(payload))


def check_deviation(ctx: dict) -> dict:
    hierarchy = _hierarchy(ctx)
    limits = hierarchy.get("limits") if isinstance(hierarchy.get("limits"), dict) else {}
    owners = {
        str(item)
        for item in (hierarchy.get("owners") or [])
    }
    today = _datetime.date.today()
    doc, declarations_digest = _load_optional(ctx, DECLARATION_FILE)
    verdicts, verdicts_digest = _load_optional(ctx, VERDICT_FILE)
    obligations, _ = _obligations(ctx)

    errors: list[str] = []
    declarations: list[dict] = []
    if isinstance(doc, dict):
        if str(doc.get("task_id") or "") != str(ctx.get("task_id") or ""):
            errors.append(
                f"declarations task_id {doc.get('task_id')!r} does not match {ctx.get('task_id')!r}"
            )
        if int(doc.get("version") or 0) != DECLARATION_SCHEMA_VERSION:
            errors.append(f"declarations version must be {DECLARATION_SCHEMA_VERSION}")
        raw = doc.get("deviations")
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            errors.append("deviations must be a list")
            raw = []
        declarations = [item for item in raw if isinstance(item, dict)]
        if len(declarations) > int(limits.get("max_active") or 5):
            errors.append(
                f"{len(declarations)} active deviations exceeds the {limits.get('max_active', 5)} ceiling"
            )
    elif doc is not None:
        errors.append("declarations file must contain an object")

    seen: set[str] = set()
    for declaration in declarations:
        label = str(declaration.get("id") or "")
        if not label:
            errors.append("every deviation needs an id")
        elif label in seen:
            errors.append(f"duplicate deviation id {label!r}")
        seen.add(label)
        errors.extend(
            _validate_declaration(
                declaration, ctx, hierarchy, obligations, limits, owners, today
            )
        )

    _, verdict_errors = _validate_verdicts(
        declarations, verdicts, ctx, obligations, hierarchy, owners
    )
    errors.extend(verdict_errors)

    debt_declarations = [
        declaration for declaration in declarations
        if declaration.get("kind") == "accepted_debt"
    ]
    refactor_declarations = [
        declaration for declaration in declarations
        if declaration.get("kind") == "forced_refactor"
    ]
    excused_paths = {
        path
        for declaration in refactor_declarations
        for path in (declaration.get("paths") or [])
    }

    covered_debt: set[tuple[str, tuple[str, str, int]]] = set()
    for declaration in debt_declarations:
        path = str(declaration.get("path") or "")
        for identity in _declared_identities(declaration):
            covered_debt.add((path, identity))
    for obligation in obligations["debt"]:
        identity = _block_identity(obligation["block"])
        if identity is not None and (obligation["path"], identity) in covered_debt:
            continue
        head = obligation["head"]
        errors.append(
            f"undeclared debt: {obligation['path']}:{head.get('name')} is rank "
            f"{head.get('rank')} (base {obligation['base'].get('complexity')} -> head "
            f"{head.get('complexity')}) and the diff modifies it; improve it below the "
            "ceiling or declare accepted_debt with owner and expiry"
        )
    for path in obligations["outside_scope"]:
        if path in excused_paths:
            continue
        errors.append(
            f"out-of-scope path {path} has no forced_refactor deviation covering it"
        )
    for declaration in refactor_declarations:
        for path in declaration.get("paths") or []:
            if path not in obligations["outside_scope"] and path not in obligations["paths"]:
                errors.append(f"{declaration.get('id')}: path {path!r} matches no changed path")
    matched_debt = {
        (str(declaration.get("path") or ""), identity)
        for declaration in debt_declarations
        for identity in _declared_identities(declaration)
    }
    observed_debt = {
        (obligation["path"], _block_identity(obligation["block"]))
        for obligation in obligations["debt"]
    }
    for path, identity in sorted(matched_debt - observed_debt):
        errors.append(
            f"deviation for {path}:{identity[1]} matches no debt obligation; delete it"
        )
    for declaration in refactor_declarations:
        if not set(declaration.get("paths") or []) & set(obligations["outside_scope"]):
            errors.append(
                f"{declaration.get('id')}: forced_refactor covers no out-of-scope path"
            )

    necessity = {
        str(declaration.get("id") or ""): _necessity_digest(declaration, ctx, obligations)
        for declaration in refactor_declarations
    }
    data = {
        "hierarchy_version": hierarchy.get("version"),
        "necessity": necessity,
        "declarations": declarations,
        "declarations_sha256": declarations_digest,
        "verdicts_sha256": verdicts_digest,
        "obligations": {
            "outside_scope": obligations["outside_scope"],
            "debt": obligations["debt"],
        },
        "errors": errors,
        "policy": {
            "limits": limits,
            "owners": sorted(owners),
        },
    }
    artifacts: list[dict] = []
    task_id = str(ctx.get("task_id") or "")
    if task_id:
        try:
            artifact_path = evidence_dir(task_id) / "artifacts" / "DEVIATION.json"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            text = canonical_json(data)
            artifact_path.write_text(text, encoding="utf-8")
            artifacts = [{
                "path": str(artifact_path),
                "sha256": sha256(text),
                "kind": "deviation-ledger",
            }]
        except (FactoryError, OSError):
            pass
    if errors:
        detail = f"{len(errors)} deviation error(s): " + "; ".join(errors[:6])
        if len(errors) > 6:
            detail += f" (+{len(errors) - 6} more)"
    elif declarations:
        detail = (
            f"{len(declarations)} deviation(s) declared "
            f"({len(debt_declarations)} debt, {len(refactor_declarations)} forced refactor); "
            f"{len(obligations['debt'])} touched pre-existing block(s), "
            f"{len(obligations['outside_scope'])} out-of-scope path(s)"
        )
    else:
        detail = (
            "no deviations declared and none required "
            f"({len(obligations['debt'])} touched pre-existing block(s), "
            f"{len(obligations['outside_scope'])} out-of-scope path(s))"
        )
    return make_proof(
        "DEVIATION", ctx, result="FAIL" if errors else "PASS",
        command=["factory-deviation-audit", "declarations+verdicts+base-state"],
        tool_name="factory-deviation-audit",
        exit_code=1 if errors else 0,
        stdout=canonical_json(data),
        artifacts=artifacts,
        detail=detail[:1900],
    )
