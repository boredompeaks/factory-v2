"""Argparse CLI for the stripped agent factory."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import secrets
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .gate import evidence as get_evidence
from .gate import execute_check, run_gate, verify
from .proof import make_proof, save_proof
from .release import release
from .scope import check_scope
from .state import (
    acquire_k1_slot,
    get_k1_lease,
    k1_lease_dir,
    k1_project,
    load_task,
    release_k1_slot,
    validate_lane,
)
from .task import claim, create_task, set_mode, set_phase, show_task, task_for_worktree, unclaim
from .util import (
    FactoryError,
    REPO_ROOT,
    evidence_dir,
    run,
)


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")


def _task_id(value: str | None) -> str:
    candidate = value or os.environ.get("FACTORY_TASK_ID") or task_for_worktree()
    if not candidate:
        raise FactoryError("task id required (pass an ID, set FACTORY_TASK_ID, or run inside a task worktree)")
    return candidate


def _json_task(value: dict[str, Any]) -> dict[str, Any]:
    return value


def _task_new(args: argparse.Namespace) -> dict[str, Any]:
    return create_task(
        kind=args.kind,
        lane=args.lane,
        scopes=args.scope,
        worktree=args.worktree,
        branch=args.branch,
        mode=args.mode,
        tier=args.tier,
        task_id=args.task_id,
    )


def _task_show(args: argparse.Namespace) -> dict[str, Any]:
    return show_task(_task_id(args.task_id))


def _task_phase(args: argparse.Namespace) -> dict[str, Any]:
    return set_phase(_task_id(args.task_id), args.phase, args.evidence)


def _task_mode(args: argparse.Namespace) -> dict[str, Any]:
    return set_mode(_task_id(args.task_id), args.mode)


def _task_claim(args: argparse.Namespace) -> dict[str, Any]:
    task_id = _task_id(args.task_id)
    return unclaim(task_id) if args.release else claim(task_id, args.by)


def _scope_check(args: argparse.Namespace) -> dict[str, Any]:
    return check_scope(
        _task_id(args.task_id),
        paths=args.path if args.path else None,
        base=args.base,
        head=args.head,
        staged=args.staged,
    )


def _check(args: argparse.Namespace) -> dict[str, Any]:
    proof = execute_check(args.check_id.upper(), _task_id(args.task_id), seed=args.seed)
    proof["result_code"] = 0 if proof.get("result") == "PASS" else 1
    return proof


def _gate(args: argparse.Namespace) -> dict[str, Any]:
    return run_gate(
        _task_id(args.task_id),
        release=args.release,
        strict_shadow=args.strict_shadow,
        seed=args.seed,
        checks=args.checks,
    )


def _evidence(args: argparse.Namespace) -> dict[str, Any]:
    return get_evidence(_task_id(args.task_id))


def _verify(args: argparse.Namespace) -> dict[str, Any]:
    return verify(_task_id(args.task_id))


def _runtime_task(task_id: str | None) -> dict[str, Any]:
    if task_id:
        return load_task(task_id)
    return {
        "task_id": "factory-runtime",
        "kind": "DOC",
        "lane": "spec-comp",
        "worktree": str(Path.cwd().resolve()),
        "branch": "",
        "base_sha": "0" * 40,
        "scope": [],
        "phase": "BUILD",
        "mode": "DOCS",
        "tier": "none",
        "created_at": "",
    }


def _runtime_proof(
    *,
    check_id: str,
    task_id: str | None,
    result: str,
    detail: str,
    command: list[str],
    stdout: str = "",
    exit_code: int | None = None,
) -> dict[str, Any]:
    task = _runtime_task(task_id)
    proof = make_proof(
        check_id=check_id,
        task=task,
        result=result,
        detail=detail,
        command=command,
        tool={"name": command[0] if command else "factory", "version": "runtime", "image": None},
        stdout=stdout,
        exit_code=exit_code,
    )
    if task_id:
        path = save_proof(task_id, proof)
        proof["proof_path"] = str(path)
    return proof


def _docker_skip(check_id: str, task_id: str | None) -> dict[str, Any]:
    detail = "SKIP: docker executable is not available on PATH"
    proof = _runtime_proof(
        check_id=check_id,
        task_id=task_id,
        result="SKIP",
        detail=detail,
        command=["docker"],
        exit_code=127,
    )
    return {"result": "SKIP", "detail": detail, "proof": proof}


def _resolve_lane(lane: str | None, task_id: str | None) -> tuple[str, str | None]:
    if task_id:
        task = load_task(task_id)
        task_lane = task.get("lane")
        if lane and lane != task_lane:
            raise FactoryError(f"K1 lane {lane!r} does not match task lane {task_lane!r}")
        lane = task_lane
    if not lane:
        raise FactoryError("K1 lane required (use --lane or pass --task-id)")
    validate_lane(lane)
    return lane, task_id


def _k1_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault(
        "POSTGRES_PASSWORD",
        os.environ.get("FACTORY_K1_POSTGRES_PASSWORD", "k1_local_test_only"),
    )
    return env


def _k1_lease_public(lease: dict[str, Any] | None) -> dict[str, Any] | None:
    """Lease data safe to print: the postgres password never leaves the state dir."""
    if not lease:
        return lease
    return {key: value for key, value in lease.items() if key != "postgres_password"}


def _k1_redact(value: str, password: str | None) -> str:
    return value.replace(password, "[REDACTED]") if password else value


def _k1_compose_env() -> dict[str, str]:
    """Compose interpolation env for K1 commands that never use the DB password.

    Compose still validates the required ${POSTGRES_PASSWORD} variable for
    status/down, so supply a placeholder when the caller has not set one. The
    real password stays in the lease and is only used by up/exec.
    """
    environment = os.environ.copy()
    if not environment.get("POSTGRES_PASSWORD"):
        environment["POSTGRES_PASSWORD"] = "k1-local-unused"
    return environment


def _k1(args: argparse.Namespace) -> dict[str, Any]:
    task_id = args.task_id or os.environ.get("FACTORY_TASK_ID")
    if args.action == "status" and args.all_lanes and not args.lane and not task_id:
        lane = None
    else:
        lane, task_id = _resolve_lane(args.lane, task_id)
        if not lane:
            raise FactoryError("task has no lane")
    if shutil.which("docker") is None:
        return _docker_skip(f"K1_{args.action.upper()}", task_id)

    project = k1_project(lane) if lane else ""
    compose = REPO_ROOT / "docker" / "k1" / "compose.yaml"
    if args.action == "status":
        if args.all_lanes:
            leases = []
            for path in sorted(k1_lease_dir().glob("*.json")):
                try:
                    leases.append(_k1_lease_public(json.loads(path.read_text(encoding="utf-8"))))
                except (OSError, ValueError):
                    continue
            return {"result": "PASS", "leases": leases}
        lease = get_k1_lease(lane)
        cmd = ["docker", "compose", "-p", project, "-f", str(compose), "ps", "--format", "json"]
        result = run(cmd, cwd=REPO_ROOT, env=_k1_compose_env())
        return {
            "result": "PASS" if result.returncode == 0 else "FAIL",
            "lane": lane,
            "lease": _k1_lease_public(lease),
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit": result.returncode,
        }
    if args.action == "up":
        lease = get_k1_lease(lane)
        if lease and lease.get("status") == "up":
            raise FactoryError(f"K1_SLOT_BUSY: lane {lane} already has run {lease.get('run_id')}")
        run_id = args.run_id or f"{task_id or 'manual'}-{secrets.token_hex(4)}"
        password = (
            os.environ.get("FACTORY_K1_POSTGRES_PASSWORD")
            or os.environ.get("POSTGRES_PASSWORD")
            or secrets.token_urlsafe(24)
        )
        lease = acquire_k1_slot(lane, run_id, password=password)
        task_worktree = load_task(task_id).get("worktree") if task_id else str(Path.cwd())
        environment = os.environ.copy()
        environment.update(
            {
                "K1_BASE": str(Path(args.base or task_worktree).resolve()),
                "K1_HEAD": str(Path(args.head or task_worktree).resolve()),
                "K1_ARTIFACTS": str((evidence_dir(task_id) if task_id else Path.cwd() / ".factory-evidence") / "artifacts"),
                "POSTGRES_PASSWORD": password,
            }
        )
        Path(environment["K1_ARTIFACTS"]).mkdir(parents=True, exist_ok=True)
        command = ["docker", "compose", "-p", project, "-f", str(compose), "up", "-d", "--pull", "never"]
        result = run(command, cwd=REPO_ROOT, env=environment)
        if result.returncode:
            release_k1_slot(lane)
        stdout = _k1_redact(result.stdout, password)
        stderr = _k1_redact(result.stderr, password)
        proof = _runtime_proof(
            check_id="K1_UP",
            task_id=task_id,
            result="PASS" if result.returncode == 0 else "FAIL",
            detail=stderr.strip() or ("K1 started" if result.returncode == 0 else "K1 start failed"),
            command=command,
            stdout=stdout,
            exit_code=result.returncode,
        )
        return {
            "result": "PASS" if result.returncode == 0 else "FAIL",
            "lane": lane,
            "lease": _k1_lease_public(lease) if result.returncode == 0 else None,
            "stdout": stdout,
            "stderr": stderr,
            "proof": proof,
        }
    if args.action == "down":
        command = ["docker", "compose", "-p", project, "-f", str(compose), "down", "--remove-orphans"]
        result = run(command, cwd=REPO_ROOT, env=_k1_compose_env())
        released = release_k1_slot(lane) if result.returncode == 0 else None
        proof = _runtime_proof(
            check_id="K1_DOWN",
            task_id=task_id,
            result="PASS" if result.returncode == 0 else "FAIL",
            detail=result.stderr.strip() or ("K1 stopped" if result.returncode == 0 else "K1 stop failed"),
            command=command,
            stdout=result.stdout,
            exit_code=result.returncode,
        )
        return {
            "result": "PASS" if result.returncode == 0 else "FAIL",
            "lane": lane,
            "released_lease": _k1_lease_public(released),
            "stdout": result.stdout,
            "stderr": result.stderr,
            "proof": proof,
        }
    lease = get_k1_lease(lane)
    if not lease or lease.get("status") != "up":
        raise FactoryError(f"K1 is not running for lane {lane}; run `factory k1 up --lane {lane}` first")
    password = lease.get("postgres_password")
    if not isinstance(password, str):
        password = None
    command = ["docker", "compose", "-p", project, "-f", str(compose), "exec", "-T"]
    if password:
        command.extend(["-e", f"POSTGRES_PASSWORD={password}"])
    command.append("app")
    if not args.command:
        raise FactoryError("k1 exec requires a command after --")
    command.extend(args.command)
    environment = _k1_compose_env()
    if password:
        environment["POSTGRES_PASSWORD"] = password
    result = run(command, cwd=REPO_ROOT, env=environment)
    stdout = _k1_redact(result.stdout, password)
    stderr = _k1_redact(result.stderr, password)
    display_command = [_k1_redact(part, password) for part in command]
    proof = _runtime_proof(
        check_id="K1_EXEC",
        task_id=task_id,
        result="PASS" if result.returncode == 0 else "FAIL",
        detail=stderr.strip() or f"container command exited {result.returncode}",
        command=display_command,
        stdout=stdout,
        exit_code=result.returncode,
    )
    return {
        "result": "PASS" if result.returncode == 0 else "FAIL",
        "stdout": stdout,
        "stderr": stderr,
        "exit": result.returncode,
        "proof": proof,
    }


def _services(args: argparse.Namespace) -> dict[str, Any]:
    action = args.action
    services = args.services or ["pg2a", "pg2b", "redis"]
    invalid = sorted(set(services) - {"pg2a", "pg2b", "redis"})
    if invalid:
        raise FactoryError(f"unknown services: {', '.join(invalid)}")
    if shutil.which("docker") is None:
        return _docker_skip(f"SERVICES_{action.upper()}", args.task_id)
    compose = REPO_ROOT / "docker" / "services" / "compose.yaml"
    if action == "up":
        command = [
            "docker",
            "compose",
            "-p",
            "services",
            "-f",
            str(compose),
            "up",
            "-d",
            "--pull",
            "never",
            *services,
        ]
    elif action == "down":
        command = [
            "docker",
            "compose",
            "-p",
            "services",
            "-f",
            str(compose),
            "stop",
            *services,
        ]
        stopped = run(command, cwd=REPO_ROOT)
        if stopped.returncode == 0:
            removed = run(
                [
                    "docker",
                    "compose",
                    "-p",
                    "services",
                    "-f",
                    str(compose),
                    "rm",
                    "-f",
                    *services,
                ],
                cwd=REPO_ROOT,
            )
            command = [
                "docker",
                "compose",
                "-p",
                "services",
                "-f",
                str(compose),
                "stop",
                *services,
            ]
            result = removed
            output = stopped.stdout + removed.stdout
            errors = stopped.stderr + removed.stderr
        else:
            result = stopped
            output = stopped.stdout
            errors = stopped.stderr
    else:
        script = REPO_ROOT / "scripts" / "services_health.sh"
        command = ["sh", str(script), *services]
        result = run(command, cwd=REPO_ROOT)
        output, errors = result.stdout, result.stderr
    if action == "up":
        result = run(command, cwd=REPO_ROOT)
        output, errors = result.stdout, result.stderr
    proof = _runtime_proof(
        check_id=f"SERVICES_{action.upper()}",
        task_id=args.task_id,
        result="PASS" if result.returncode == 0 else "FAIL",
        detail=errors.strip() or f"services {action} exited {result.returncode}",
        command=command,
        stdout=output,
        exit_code=result.returncode,
    )
    return {
        "result": "PASS" if result.returncode == 0 else "FAIL",
        "services": services,
        "stdout": output,
        "stderr": errors,
        "exit": result.returncode,
        "proof": proof,
    }


def _policy_card(args: argparse.Namespace) -> dict[str, Any]:
    try:
        module = importlib.import_module("factory.policy_cards")
    except ImportError as exc:
        return {
            "result": "SKIP",
            "role": args.role,
            "detail": f"policy cards unavailable: {exc}",
        }
    for name in ("policy_card", "get_policy_card", "render_card", "card_for_role", "card_for"):
        function = getattr(module, name, None)
        if callable(function):
            try:
                return {"result": "PASS", "role": args.role, "card": function(args.role)}
            except Exception as exc:
                return {
                    "result": "SKIP",
                    "role": args.role,
                    "detail": f"policy card unavailable for role {args.role!r}: {exc}",
                }
    cards = getattr(module, "CARDS", getattr(module, "cards", None))
    if isinstance(cards, dict) and args.role in cards:
        return {"result": "PASS", "role": args.role, "card": cards[args.role]}
    return {
        "result": "SKIP",
        "role": args.role,
        "detail": "factory.policy_cards loaded but exposes no recognized card lookup",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factory",
        description="Stripped agent factory CLI",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    task_parser = commands.add_parser("task", help="create and manage tasks")
    task_sub = task_parser.add_subparsers(dest="task_action", required=True)
    new = task_sub.add_parser("new", help="create a task")
    new.add_argument("--kind", required=True, choices=sorted({"DOC", "FIX", "FEATURE", "SECURITY"}))
    new.add_argument("--lane", required=True)
    new.add_argument("--scope", action="append", required=True, help="scope glob; may be repeated")
    new.add_argument("--worktree")
    new.add_argument("--branch")
    new.add_argument("--mode")
    new.add_argument("--tier", choices=("none", "high", "max"), default="none")
    new.add_argument("--task-id")
    _add_json(new)
    new.set_defaults(handler=_task_new)
    show = task_sub.add_parser("show", help="show a task")
    show.add_argument("task_id", nargs="?")
    _add_json(show)
    show.set_defaults(handler=_task_show)
    phase = task_sub.add_parser("phase", help="transition task phase")
    phase.add_argument("task_id")
    phase.add_argument("phase", choices=("BUILD", "GATE"))
    phase.add_argument("--evidence", action="append", default=[])
    _add_json(phase)
    phase.set_defaults(handler=_task_phase)
    mode = task_sub.add_parser("mode", help="set task mode")
    mode.add_argument("task_id")
    mode.add_argument("mode")
    _add_json(mode)
    mode.set_defaults(handler=_task_mode)
    claim_parser = task_sub.add_parser("claim", help="claim or release a task lane")
    claim_parser.add_argument("task_id")
    claim_parser.add_argument("--by", default=os.environ.get("USER", "unknown"))
    claim_parser.add_argument("--release", action="store_true")
    _add_json(claim_parser)
    claim_parser.set_defaults(handler=_task_claim)

    scope_parser = commands.add_parser("scope", help="scope operations")
    scope_sub = scope_parser.add_subparsers(dest="scope_action", required=True)
    scope_check = scope_sub.add_parser("check", help="check changed paths against task scope")
    scope_check.add_argument("task_id", nargs="?")
    scope_check.add_argument("--task-id", dest="task_id_option")
    scope_check.add_argument("--path", action="append", default=[])
    scope_check.add_argument("--base")
    scope_check.add_argument("--head", default="HEAD")
    scope_check.add_argument("--staged", action="store_true", help="check only staged paths")
    _add_json(scope_check)
    scope_check.set_defaults(handler=_scope_check)

    check = commands.add_parser("check", help="run one check and persist its proof")
    check.add_argument("check_id")
    check.add_argument("--task-id")
    check.add_argument("--seed", type=int)
    _add_json(check)
    check.set_defaults(handler=_check)

    gate = commands.add_parser("gate", help="run the policy-selected check set")
    gate.add_argument("--task-id")
    gate.add_argument("--release", action="store_true", help="include release checks")
    gate.add_argument("--strict-shadow", action="store_true")
    gate.add_argument("--seed", type=int, help="replay a fuzz seed")
    gate.add_argument(
        "--checks",
        type=lambda value: [item.strip() for item in value.split(",") if item.strip()],
        help="comma-separated subset of the policy gate (may narrow, never widen)",
    )
    _add_json(gate)
    gate.set_defaults(handler=_gate)

    evidence_parser = commands.add_parser("evidence", help="list task proofs and their root")
    evidence_parser.add_argument("task_id", nargs="?")
    evidence_parser.add_argument("--task-id", dest="task_id_option")
    _add_json(evidence_parser)
    evidence_parser.set_defaults(handler=_evidence)

    verify_parser = commands.add_parser("verify", help="verify proof root and policy hash")
    verify_parser.add_argument("task_id", nargs="?")
    verify_parser.add_argument("--task-id", dest="task_id_option")
    _add_json(verify_parser)
    verify_parser.set_defaults(handler=_verify)

    release_parser = commands.add_parser("release", help="merge a proof-gated task branch")
    release_parser.add_argument("task_id", nargs="?")
    release_parser.add_argument("--task-id", dest="task_id_option")
    release_parser.add_argument("--repo", help="clean integration worktree (defaults to cwd)")
    release_parser.add_argument("--dry-run", action="store_true")
    release_parser.add_argument(
        "--allow-deletions",
        action="store_true",
        help="approve a divergent branch that deletes test files or many lines",
    )
    release_parser.add_argument(
        "--owner-approval",
        help="recorded owner approval, required with --allow-deletions",
    )
    release_parser.add_argument(
        "--deletion-scope",
        action="append",
        default=None,
        metavar="GLOB",
        help="path glob covering deletions; repeat for multiple scopes (requires --allow-deletions)",
    )
    _add_json(release_parser)
    release_parser.set_defaults(handler=_release)

    k1_parser = commands.add_parser("k1", help="manage the K1 Docker Compose sandbox")
    k1_sub = k1_parser.add_subparsers(dest="action", required=True)
    for action in ("up", "down", "status", "exec"):
        item = k1_sub.add_parser(action, help=f"K1 {action}")
        item.add_argument("--task-id")
        item.add_argument("--lane")
        if action == "up":
            item.add_argument("--run-id")
            item.add_argument("--base")
            item.add_argument("--head")
        if action == "status":
            item.add_argument("--all-lanes", action="store_true")
        if action == "exec":
            item.add_argument("command", nargs=argparse.REMAINDER)
        _add_json(item)
        item.set_defaults(handler=_k1)

    services_parser = commands.add_parser("services", help="manage test service containers")
    services_sub = services_parser.add_subparsers(dest="action", required=True)
    for action in ("up", "down", "health"):
        item = services_sub.add_parser(action, help=f"services {action}")
        item.add_argument("services", nargs="*", choices=("pg2a", "pg2b", "redis"))
        item.add_argument("--task-id")
        _add_json(item)
        item.set_defaults(handler=_services)

    policy_parser = commands.add_parser("policy", help="policy utilities")
    policy_sub = policy_parser.add_subparsers(dest="policy_action", required=True)
    card = policy_sub.add_parser("card", help="show a role policy card")
    card.add_argument("--role", required=True)
    _add_json(card)
    card.set_defaults(handler=_policy_card)

    probe_parser = commands.add_parser("probe", help="audit hacker campaign probe coverage")
    probe_sub = probe_parser.add_subparsers(dest="probe_action", required=True)
    audit = probe_sub.add_parser("audit", help="count eligible probe executions against the catalog")
    audit.add_argument("--records", action="append", default=[], help="probe-record file or directory (repeatable)")
    audit.add_argument("--variant", choices=("high", "max"), default="max")
    audit.add_argument("--item", action="append", default=[], help="catalog item id (repeatable)")
    _add_json(audit)
    audit.set_defaults(handler=_probe_audit)

    return parser


def _release(args: argparse.Namespace) -> dict[str, Any]:
    return release(
        _task_id(args.task_id or args.task_id_option),
        repo=args.repo,
        dry_run=args.dry_run,
        allow_deletions=args.allow_deletions,
        owner_approval=args.owner_approval,
        deletion_scope=args.deletion_scope,
    )


def _normalize_task_arg(args: argparse.Namespace) -> None:
    if hasattr(args, "task_id_option"):
        args.task_id = args.task_id_option or getattr(args, "task_id", None)


def _probe_audit(args: argparse.Namespace) -> dict[str, Any]:
    script = REPO_ROOT / "tools" / "probe_audit.py"
    if not script.is_file():
        raise FactoryError(f"probe audit tool is missing: {script}")
    records = args.records or ["evidence/probe"]
    command = [
        sys.executable, str(script),
        "--records", *[str(item) for item in records],
        "--variant", args.variant,
    ]
    for item in args.item:
        command.extend(["--item", item])
    command.append("--json")
    result = run(command, cwd=REPO_ROOT)
    report: Any = None
    if result.stdout.strip():
        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError:
            report = None
    status = "PASS" if result.returncode == 0 else ("FAIL" if result.returncode == 1 else "TOOL_ABSENT")
    return {
        "result": status,
        "exit": result.returncode,
        "records": [str(item) for item in records],
        "variant": args.variant,
        "report": report,
        "stdout": result.stdout if report is None else "",
        "stderr": result.stderr,
    }


def _exit_code(result: dict[str, Any]) -> int:
    status = result.get("result")
    if status in {"FAIL", "FLAKY", "TOOL_ABSENT"}:
        return 1
    if result.get("result_code") is not None:
        return int(result["result_code"])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        _normalize_task_arg(args)
        handler: Callable[[argparse.Namespace], dict[str, Any]] = args.handler
        result = handler(args)
    except FactoryError as exc:
        result = {"result": "FAIL", "error": str(exc)}
        if "args" in locals() and getattr(args, "json", False):
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"factory: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("factory: interrupted", file=sys.stderr)
        return 130

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        if result.get("result") in {"FAIL", "FLAKY", "TOOL_ABSENT"}:
            print(f"FAIL: {result.get('detail') or result.get('error') or result.get('result')}")
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return _exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
