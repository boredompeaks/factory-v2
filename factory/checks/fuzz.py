"""Schema-derived API fuzz check using Schemathesis."""

from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import urljoin

from . import (
    canonical_json,
    executable,
    make_proof,
    policy_path,
    read_json,
    run_process,
    tool_version,
    worktree_path,
)


def check_schema_fuzz(ctx: dict) -> dict:
    seed = ctx.get("seed")
    if seed is None:
        seed = secrets.randbits(64)
    try:
        seed = int(seed)
    except (TypeError, ValueError):
        return make_proof(
            "SCHEMA_FUZZ", ctx, result="FAIL", seed=None,
            detail="ctx.seed must be an unsigned 64-bit integer",
        )
    if seed < 0 or seed >= 2**64:
        return make_proof(
            "SCHEMA_FUZZ", ctx, result="FAIL", seed=seed,
            detail="fuzz seed must be in the unsigned 64-bit range",
        )
    policy, error = read_json(policy_path(ctx, "schema-fuzz.json"))
    if error:
        return make_proof("SCHEMA_FUZZ", ctx, result="FAIL", seed=seed, detail=error, stdout=error)
    env = ctx.get("env") if isinstance(ctx.get("env"), dict) else {}
    worktree = worktree_path(ctx)
    base_url = (
        env.get("FACTORY_SCHEMA_BASE_URL")
        or env.get("FACTORY_DAST_TARGET_URL")
        or env.get("SCHEMA_BASE_URL")
        or env.get("base_url")
    )
    explicit_spec = (
        env.get("FACTORY_OPENAPI_SPEC")
        or env.get("OPENAPI_SPEC")
        or env.get("schema_path")
    )
    if explicit_spec:
        candidate = Path(str(explicit_spec)).expanduser()
        if candidate.is_file():
            schema = str(candidate.resolve())
        elif str(explicit_spec).startswith(("http://", "https://")):
            schema = str(explicit_spec)
        elif worktree and (worktree / str(explicit_spec)).is_file():
            schema = str((worktree / str(explicit_spec)).resolve())
        else:
            available = executable("schemathesis") or executable("st")
            return make_proof(
                "SCHEMA_FUZZ", ctx, result="TOOL_ABSENT", seed=seed,
                command=["schemathesis", "run", str(explicit_spec)],
                tool_name="schemathesis",
                version=tool_version(available, ctx=ctx) if available else "absent",
                detail=f"configured OpenAPI spec does not exist or is not reachable: {explicit_spec}",
                stdout=f"missing spec: {explicit_spec}",
            )
    elif base_url:
        source = str(policy.get("schema_source") or "runtime-openapi:/openapi.json")
        suffix = source.split(":", 1)[1] if ":" in source else "/openapi.json"
        schema = urljoin(str(base_url).rstrip("/") + "/", suffix.lstrip("/"))
    else:
        available = executable("schemathesis") or executable("st")
        return make_proof(
            "SCHEMA_FUZZ", ctx, result="TOOL_ABSENT", seed=seed,
            command=["schemathesis", "run"],
            tool_name="schemathesis",
            version=tool_version(available, ctx=ctx) if available else "absent",
            detail="no live base URL or OpenAPI spec was supplied (FACTORY_SCHEMA_BASE_URL / FACTORY_OPENAPI_SPEC)",
            stdout="schema source unavailable",
        )
    binary = executable("schemathesis") or executable("st")
    if not binary:
        return make_proof(
            "SCHEMA_FUZZ", ctx, result="TOOL_ABSENT", seed=seed,
            command=["schemathesis", "run", schema],
            tool_name="schemathesis", version="absent",
            detail="schemathesis binary is not on PATH",
            stdout="schemathesis unavailable",
        )
    max_examples = min(max(int(policy.get("max_examples", 200)), 1), 1000)
    workers = min(max(int(policy.get("workers", 1)), 1), 8)
    help_proc = run_process(
        [binary, "run", "--help"], ctx=ctx, cwd=worktree, timeout=20,
    )
    # Schemathesis releases have used --seed; some wrappers expose
    # --hypothesis-seed. Prefer the latter when supported, otherwise use the
    # current documented --seed option. HYPOTHESIS_SEED is also set so pytest
    # / Hypothesis-based wrappers replay the same generated sequence.
    seed_flag = "--hypothesis-seed" if "--hypothesis-seed" in (help_proc["stdout"] + help_proc["stderr"]) else "--seed"
    command = [
        binary, "run", schema,
        seed_flag, str(seed),
        "--max-examples", str(max_examples),
        "--workers", str(workers),
    ]
    timeout = min(max(int(policy.get("timeout", 300)), 1), 600)
    proc = run_process(
        command, ctx=ctx, cwd=worktree, timeout=timeout,
        env={"HYPOTHESIS_SEED": str(seed)},
    )
    version = tool_version(binary, ctx=ctx, cwd=worktree)
    if proc["error"]:
        result = "FLAKY" if proc["timed_out"] else "FAIL"
        return make_proof(
            "SCHEMA_FUZZ", ctx, result=result, seed=seed,
            command=command, tool_name="schemathesis", version=version,
            exit_code=proc["exit"], stdout=proc["stdout"] + proc["stderr"],
            detail=f"schemathesis invocation error: {proc['error']}",
            network="host-required" if schema.startswith(("http://", "https://")) else "not_isolated",
            env_extra={"HYPOTHESIS_SEED": str(seed)},
        )
    result = "FLAKY" if proc["timed_out"] else "PASS" if proc["exit"] == 0 else "FAIL"
    status = "timed out" if proc["timed_out"] else "exited {}".format(proc["exit"])
    detail = f"schemathesis {status}; schema={schema}; examples<={max_examples}; workers={workers}"
    return make_proof(
        "SCHEMA_FUZZ", ctx, result=result, seed=seed,
        command=command, tool_name="schemathesis", version=version,
        exit_code=proc["exit"], stdout=proc["stdout"] + proc["stderr"],
        detail=detail,
        network="host-required" if schema.startswith(("http://", "https://")) else "not_isolated",
        env_extra={"HYPOTHESIS_SEED": str(seed)},
    )
