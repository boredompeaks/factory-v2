"""ZAP baseline runner for an explicitly provided live application URL."""

from __future__ import annotations

import json
import re
import os
import tempfile
from pathlib import Path

from . import (
    canonical_json,
    executable,
    make_proof,
    run_process,
    tool_version,
    worktree_path,
)


def _report_findings(report: dict) -> tuple[list[dict], int]:
    sites = report.get("site", [])
    if isinstance(sites, dict):
        sites = [sites]
    findings = []
    count = 0
    for site in sites if isinstance(sites, list) else []:
        if not isinstance(site, dict):
            continue
        alerts = site.get("alerts", [])
        for alert in alerts if isinstance(alerts, list) else []:
            if not isinstance(alert, dict):
                continue
            count += 1
            findings.append({
                "name": alert.get("name"),
                "risk": alert.get("riskdesc") or alert.get("risk"),
                "confidence": alert.get("confidence"),
                "instances": len(alert.get("instances") or []),
            })
    return findings, count


def check_dast_baseline(ctx: dict) -> dict:
    env = ctx.get("env") if isinstance(ctx.get("env"), dict) else {}
    target = (
        env.get("FACTORY_DAST_TARGET_URL")
        or env.get("DAST_TARGET_URL")
        or env.get("base_url")
    )
    if not target:
        docker = executable("docker")
        return make_proof(
            "DAST_BASELINE", ctx, result="TOOL_ABSENT",
            command=["docker", "exec", "k1", "zap-baseline"],
            tool_name="docker-zap-baseline",
            version=tool_version("docker", ctx=ctx) if docker else "absent",
            detail="no live DAST target URL was supplied (FACTORY_DAST_TARGET_URL)",
            stdout="DAST target unavailable", network="host-required",
        )
    target = str(target)
    docker = executable("docker")
    if not docker:
        return make_proof(
            "DAST_BASELINE", ctx, result="TOOL_ABSENT",
            command=["docker", "exec", "k1", "zap-baseline"],
            tool_name="docker", version="absent",
            detail="docker binary is not on PATH; host-only ZAP execution is not accepted",
            stdout="docker unavailable", network="host-required",
        )
    container = str(env.get("FACTORY_K1_CONTAINER") or "k1")
    inspect = run_process(["docker", "inspect", "-f", "{{.State.Running}}", container], ctx=ctx, timeout=15)
    if inspect["exit"] != 0 or inspect["stdout"].strip() != "true":
        return make_proof(
            "DAST_BASELINE", ctx, result="TOOL_ABSENT",
            command=["docker", "exec", container, "zap-baseline"],
            tool_name="docker", version=tool_version("docker", ctx=ctx),
            exit_code=inspect["exit"], stdout=inspect["stdout"] + inspect["stderr"],
            detail=f"K1 container {container!r} is not running; start the pinned K1 image before DAST",
            network="host-required",
        )
    task_fragment = re.sub(r"[^A-Za-z0-9_.-]", "_", str(ctx.get("task_id") or "task"))[:48]
    remote_report = f"/tmp/factory-zap-{task_fragment}-{os.getpid()}.json"
    timeout = int(env.get("FACTORY_DAST_TIMEOUT", 1800))
    command = [
        docker, "exec", container, "zap-baseline",
        "-t", target, "-J", remote_report, "-I", "-T", str(max(timeout - 30, 1)),
    ]
    proc = run_process(command, ctx=ctx, cwd=worktree_path(ctx), timeout=timeout)
    version = tool_version("docker", ctx=ctx)
    if proc["error"]:
        return make_proof(
            "DAST_BASELINE", ctx, result="FLAKY" if proc["timed_out"] else "FAIL",
            command=command, tool_name="docker-zap-baseline", version=version,
            exit_code=proc["exit"], stdout=proc["stdout"] + proc["stderr"],
            detail=f"K1 ZAP command failed: {proc['error']}", network="host-required",
        )
    if proc["exit"] in {126, 127} or re.search(
        r"(?:zap-baseline:\s*not found|executable file not found|command not found)",
        (proc["stdout"] + proc["stderr"]), re.I,
    ):
        return make_proof(
            "DAST_BASELINE", ctx, result="TOOL_ABSENT",
            command=command, tool_name="docker-zap-baseline", version=version,
            exit_code=proc["exit"], stdout=proc["stdout"] + proc["stderr"],
            detail=f"ZAP baseline executable is unavailable inside K1 container {container!r}",
            network="host-required",
        )
    with tempfile.TemporaryDirectory(prefix="factory-zap-report-") as tmp:
        local_report = Path(tmp) / "zap-report.json"
        copy_command = [docker, "cp", f"{container}:{remote_report}", str(local_report)]
        copied = run_process(copy_command, ctx=ctx, timeout=30)
        if copied["exit"] != 0 or not local_report.is_file():
            return make_proof(
                "DAST_BASELINE", ctx, result="FAIL",
                command=copy_command, tool_name="docker", version=version,
                exit_code=copied["exit"], stdout=copied["stdout"] + copied["stderr"],
                detail="ZAP report could not be copied out of K1",
                network="host-required",
            )
        raw = local_report.read_text(encoding="utf-8", errors="replace")
        try:
            report = json.loads(raw)
        except json.JSONDecodeError as exc:
            return make_proof(
                "DAST_BASELINE", ctx, result="FAIL",
                command=command, tool_name="docker-zap-baseline", version=version,
                exit_code=proc["exit"], stdout=raw,
                detail=f"ZAP report is invalid JSON: {exc}", network="host-required",
            )
    findings, count = _report_findings(report if isinstance(report, dict) else {})
    result = "PASS" if proc["exit"] == 0 and count == 0 else "FAIL"
    return make_proof(
        "DAST_BASELINE", ctx, result=result, command=command,
        tool_name="docker-zap-baseline", version=version,
        exit_code=proc["exit"], stdout=canonical_json(findings),
        detail=f"K1 ZAP baseline scanned {target}; {count} alert(s) (all alerts block pending an explicit threshold)",
        network="host-required",
    )
