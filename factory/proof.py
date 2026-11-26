"""Proof creation, canonical hashing, persistence, and verification."""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import inspect
import os
import re
import secrets
import shutil
import sys
import subprocess
import time
from pathlib import Path
from typing import Any

from .util import (
    FactoryError,
    REPO_ROOT,
    canonical_json,
    evidence_dir,
    git_metadata,
    policy_hash,
    proofs_dir,
    resolve_worktree,
    sha256_bytes,
    write_json,
)


TIMING_FIELDS = {
    "duration",
    "duration_ms",
    "elapsed",
    "elapsed_ms",
    "started_at",
    "finished_at",
    "completed_at",
    "timings",
}
RESULTS = {"PASS", "FAIL", "SKIP", "TOOL_ABSENT", "FLAKY"}
NETWORK_ISOLATED_CHECKS = {
    "SCOPE",
    "TEST_SET",
    "TEST_INFRA",
    "CHANGELOG",
    "SECRETS",
    "CONVENTION",
    "SMELL",
    "GATE_CONTROLS",
    "POLICY_DIGEST",
    "POLICY_HASH",
    "ARCH",
    "ORACLE_PROVENANCE",
    "GUARD_DELTA",
    "AUTHZ_MATRIX",
    "SEMGREP",
    "ZIZMOR",
    "AGT_PARITY",
    "AGT_AUDIT",
    "RADON",
    "BANDIT",
}
_BWRAP_READY: bool | None = None


@contextlib.contextmanager
def _isolate_runner_subprocesses():
    """Wrap check-runner subprocesses in bwrap's network namespace when usable."""
    global _BWRAP_READY
    binary = shutil.which("bwrap")
    if not binary:
        yield "not_isolated"
        return
    original_run = subprocess.run
    if _BWRAP_READY is None:
        try:
            probe = original_run(
                [
                    binary,
                    "--die-with-parent",
                    "--unshare-net",
                    "--ro-bind",
                    "/",
                    "/",
                    "--dev",
                    "/dev",
                    "--proc",
                    "/proc",
                    "--",
                    "true",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            _BWRAP_READY = probe.returncode == 0
        except OSError:
            _BWRAP_READY = False
    if not _BWRAP_READY:
        yield "isolation_unavailable"
        return

    def isolated_run(args, *positional, **kwargs):
        shell = bool(kwargs.pop("shell", False))
        if shell:
            argv = ["/bin/sh", "-c", args] if isinstance(args, str) else list(args)
        elif isinstance(args, (list, tuple)):
            argv = [str(part) for part in args]
        else:
            argv = [str(args)]
        for directory in ("~/.semgrep", "~/.cache"):
            Path(os.path.expanduser(directory)).mkdir(parents=True, exist_ok=True)
        prefix = [
            binary,
            "--die-with-parent",
            "--unshare-net",
            "--ro-bind",
            "/",
            "/",
            "--bind",
            "/tmp",
            "/tmp",
            "--bind",
            os.path.expanduser("~/.semgrep"),
            os.path.expanduser("~/.semgrep"),
            "--bind",
            os.path.expanduser("~/.cache"),
            os.path.expanduser("~/.cache"),
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--",
        ]
        return original_run([*prefix, *argv], *positional, shell=False, **kwargs)

    subprocess.run = isolated_run
    try:
        yield "none"
    finally:
        subprocess.run = original_run


def _without_timing(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_timing(item)
            for key, item in value.items()
            if not _is_timing_field(str(key))
        }
    if isinstance(value, list):
        return [_without_timing(item) for item in value]
    return value


def _is_timing_field(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in TIMING_FIELDS
        or lowered.startswith(("duration_", "elapsed_", "timing_"))
        or lowered.endswith(("_duration", "_duration_ms", "_elapsed_ms"))
    )


def proof_for_hash(proof: dict[str, Any]) -> dict[str, Any]:
    return _without_timing(proof)


def canonical_proof(proof: dict[str, Any]) -> str:
    return canonical_json(proof_for_hash(proof))


def proof_root(proofs: list[dict[str, Any]]) -> str:
    ordered = sorted((proof_for_hash(item) for item in proofs), key=lambda item: (item.get("check", ""), canonical_json(item)))
    return hashlib.sha256(canonical_json(ordered).encode("utf-8")).hexdigest()


def save_proof(task_id: str, proof: dict[str, Any]) -> Path:
    check_id = str(proof.get("check", ""))
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", check_id):
        raise FactoryError(f"invalid proof check id: {check_id!r}")
    directory = proofs_dir(task_id)
    path = directory / f"{check_id}.json"
    write_json(path, proof)
    return path


def read_proofs(task_id: str) -> list[dict[str, Any]]:
    directory = proofs_dir(task_id)
    if not directory.exists():
        return []
    proofs = []
    import json

    for path in sorted(directory.glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as stream:
                value = json.load(stream)
        except (OSError, ValueError) as exc:
            raise FactoryError(f"invalid proof file {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise FactoryError(f"proof file must contain an object: {path}")
        proofs.append(value)
    return proofs


def _result_name(value: Any) -> str:
    if isinstance(value, bool):
        return "PASS" if value else "FAIL"
    candidate = str(value or "").upper()
    return candidate if candidate in RESULTS else "FAIL"


def _k1_image_digest() -> str | None:
    configured = os.environ.get("K1_IMAGE_DIGEST")
    if configured:
        return configured
    docker = shutil.which("docker")
    if not docker:
        return None
    try:
        result = subprocess.run(
            [docker, "image", "inspect", "--format", "{{.Id}}", "k1-app:latest"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _tool_info(raw: dict[str, Any], check_id: str) -> dict[str, Any]:
    value = raw.get("tool")
    if isinstance(value, dict):
        return {
            "name": str(value.get("name", check_id.lower())),
            "version": str(value.get("version", f"python {sys.version_info.major}.{sys.version_info.minor}")),
            "image": value.get("image") or _k1_image_digest(),
        }
    return {
        "name": check_id.lower(),
        "version": f"python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "image": _k1_image_digest(),
    }


def make_proof(
    *,
    check_id: str,
    task: dict[str, Any],
    result: str,
    detail: str,
    command: list[str] | None = None,
    tool: dict[str, Any] | None = None,
    env: dict[str, Any] | None = None,
    seed: int | None = None,
    exit_code: int | None = None,
    stdout: str = "",
    artifacts: list[dict[str, str]] | None = None,
    policy_digest: str | None = None,
    extra: dict[str, Any] | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    root = resolve_worktree(task)
    values = git_metadata(root, task.get("base_sha"))
    proof: dict[str, Any] = {
        "check": check_id,
        "task_id": task["task_id"],
        **values,
        "command": command or [],
        "tool": tool or {
            "name": check_id.lower(),
            "version": f"python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "image": _k1_image_digest(),
        },
        "env": env
        or {
            "TZ": "UTC",
            "PYTHONHASHSEED": "0",
            "LC_ALL": "C",
            "network": "not_isolated",
        },
        "seed": seed,
        "exit": exit_code,
        "stdout_sha256": sha256_bytes(stdout.encode("utf-8")),
        "artifacts": artifacts or [],
        "result": _result_name(result),
        "detail": detail,
        "policy_hash": policy_digest or policy_hash(),
    }
    if duration_ms is not None:
        proof["duration_ms"] = duration_ms
    if extra:
        proof.update(extra)
    return proof


def _call_runner(
    check_id: str,
    task: dict[str, Any],
    seed: int | None,
    network: str,
) -> Any:
    """Import and invoke another owner's factory.checks runner lazily."""
    module = importlib.import_module("factory.checks")
    runner = getattr(module, "run_check")
    root = resolve_worktree(task)
    git = git_metadata(root, task.get("base_sha"))
    context = {
        "check": check_id,
        "check_id": check_id,
        "task": task,
        "task_id": task.get("task_id"),
        "repo_root": str(root),
        "worktree": str(root),
        "root": root,
        "policies_dir": str(REPO_ROOT / "policies"),
        "base": git["base"],
        "head": git["head"],
        "tree": git["tree"],
        "scope": task.get("scope", []),
        "tier": task.get("tier", "none"),
        "mode": task.get("mode"),
        "seed": seed,
        "env": {
            "TZ": "UTC",
            "PYTHONHASHSEED": "0",
            "LC_ALL": "C",
            "network": network,
            # SCHEMA_FUZZ/DAST_BASELINE read their target from the environment
            # (the policy has no spec path). The check runner never sees the
            # caller's ambient environment, so forward only those keys.
            **{
                key: os.environ[key]
                for key in (
                    "FACTORY_OPENAPI_SPEC",
                    "OPENAPI_SPEC",
                    "schema_path",
                    "FACTORY_SCHEMA_BASE_URL",
                    "SCHEMA_BASE_URL",
                    "base_url",
                    "FACTORY_DAST_TARGET_URL",
                )
                if os.environ.get(key)
            },
        },
        "policy_hash": policy_hash(),
    }
    static_module = sys.modules.get("factory.checks.static")
    original_tool_script = getattr(static_module, "_tool_script", None) if static_module else None
    if callable(original_tool_script):
        def tool_script(ctx: dict[str, Any], filename: str) -> Path:
            candidate = original_tool_script(ctx, filename)
            return candidate if candidate.is_file() else REPO_ROOT / "tools" / filename

        static_module._tool_script = tool_script
    signature = inspect.signature(runner)
    params = list(signature.parameters.values())
    try:
        if len(params) == 1 and params[0].name in {"context", "ctx", "request"}:
            return runner(context)
        if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in params):
            return runner(**context)
        accepted = {
            name: context[name]
            for name, parameter in signature.parameters.items()
            if name in context
            and parameter.kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }
        required_unknown = [
            parameter
            for parameter in params
            if parameter.default is inspect.Parameter.empty
            and parameter.kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
            and parameter.name not in accepted
        ]
        if required_unknown:
            positional = [check_id, context]
            return runner(*positional[: len(params)])
        return runner(**accepted)
    finally:
        if callable(original_tool_script):
            static_module._tool_script = original_tool_script


def run_check(
    check_id: str,
    task: dict[str, Any],
    *,
    seed: int | None = None,
    policy_digest: str | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", check_id):
        raise FactoryError(f"invalid check id: {check_id!r}")
    if "FUZZ" in check_id and seed is None:
        seed = secrets.randbits(64)
    if seed is not None and not 0 <= seed < (1 << 64):
        raise FactoryError("fuzz seed must be an unsigned 64-bit integer")
    before = time.monotonic_ns()
    digest = policy_digest or policy_hash()
    old_env = {key: os.environ.get(key) for key in ("TZ", "PYTHONHASHSEED", "LC_ALL")}
    os.environ.update({"TZ": "UTC", "PYTHONHASHSEED": "0", "LC_ALL": "C"})
    network = "not_isolated"
    try:
        if check_id == "POLICY_HASH":
            after_hash = policy_hash()
            network = "none"
            raw: Any = {
                "result": "PASS" if digest == after_hash else "FAIL",
                "detail": "policy hash is stable during gate" if digest == after_hash else "policies changed during gate",
                "command": ["factory", "policy-hash"],
                "exit": 0 if digest == after_hash else 1,
            }
        else:
            if check_id in NETWORK_ISOLATED_CHECKS:
                with _isolate_runner_subprocesses() as network:
                    if network == "isolation_unavailable":
                        raw = {
                            "result": "FAIL",
                            "detail": "bwrap is present but --unshare-net cannot be initialized",
                            "command": ["bwrap", "--unshare-net"],
                            "exit": 1,
                        }
                    else:
                        raw = _call_runner(check_id, task, seed, network)
            else:
                network = "host_required"
                raw = _call_runner(check_id, task, seed, network)
    except ModuleNotFoundError as exc:
        raw = {
            "result": "TOOL_ABSENT",
            "detail": f"check runner unavailable: {exc}",
            "command": ["factory.checks", check_id],
            "exit": 127,
        }
    except (AttributeError, ImportError) as exc:
        raw = {
            "result": "TOOL_ABSENT",
            "detail": f"check runner unavailable: {exc}",
            "command": ["factory.checks", check_id],
            "exit": 127,
        }
    except Exception as exc:  # A check crash is evidence of failure, not CLI failure.
        raw = {
            "result": "FAIL",
            "detail": f"check runner raised {type(exc).__name__}: {exc}",
            "command": ["factory.checks", check_id],
            "exit": 1,
        }
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    duration_ms = int((time.monotonic_ns() - before) / 1_000_000)
    if isinstance(raw, dict) and isinstance(raw.get("proof"), dict):
        raw = raw["proof"]
    if isinstance(raw, str):
        raw = {"result": raw, "detail": ""}
    elif isinstance(raw, bool):
        raw = {"result": "PASS" if raw else "FAIL", "detail": ""}
    elif not isinstance(raw, dict):
        raw = {"result": "FAIL", "detail": f"unsupported check result: {raw!r}"}

    result = _result_name(raw.get("result", raw.get("status", "FAIL")))
    detail = str(raw.get("detail", raw.get("message", "")))
    if len(detail) > 2048:
        detail = detail[:2045] + "..."
    output = raw.get("stdout", "")
    if not isinstance(output, str):
        output = str(output)
    command = raw.get("command", ["factory.checks", check_id])
    if not isinstance(command, list):
        command = [str(command)]
    artifacts = raw.get("artifacts", [])
    if not isinstance(artifacts, list):
        artifacts = []
    proof = make_proof(
        check_id=check_id,
        task=task,
        result=result,
        detail=detail,
        command=[str(part) for part in command],
        tool=_tool_info(raw, check_id),
        env={
            "TZ": "UTC",
            "PYTHONHASHSEED": "0",
            "LC_ALL": "C",
            "network": network,
        },
        seed=seed if seed is not None else raw.get("seed"),
        exit_code=raw.get("exit", raw.get("exit_code", 0 if result == "PASS" else 1)),
        stdout=output,
        artifacts=artifacts,
        policy_digest=digest,
        extra={
            key: value
            for key, value in raw.items()
            if key not in {
                "check", "task_id", "base", "head", "tree", "command", "tool",
                "env", "seed", "exit", "exit_code", "stdout", "stdout_sha256",
                "artifacts", "result", "status", "detail", "message", "policy_hash",
                "duration", "duration_ms", "timings", "proof",
            }
        },
        duration_ms=duration_ms,
    )
    # Preserve a runner-supplied stdout digest when it also supplied no stdout.
    if raw.get("stdout_sha256") and not output:
        proof["stdout_sha256"] = str(raw["stdout_sha256"])
    return proof
