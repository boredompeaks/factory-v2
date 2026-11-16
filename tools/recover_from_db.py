#!/usr/bin/env python3
"""Reconstruct factory-v2 from OpenCode session tool payloads.

Reads the OpenCode SQLite DB (read-only) and replays, in order:
  - patch  -> apply_patch (*** Begin Patch format)
  - write  -> full file write
  - edit   -> oldString/newString replacement
  - shell  -> only file-mutating commands (whitelisted)

Target: /home/boredompeaks/factory-v2
Source DB: ~/.local/share/opencode/opencode.db
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/boredompeaks/factory-v2")
DB = Path(os.path.expanduser("~/.local/share/opencode/opencode.db"))
OLD = "/tmp/opencode/factory-v2"

SESSIONS = [
    ("cli", "ses_e214e003bffenBUm7R31oKpLb3"),
    ("checks", "ses_e214e0037ffe7TK8Ou7VVzByD2"),
    ("rewrite", "ses_e214e0023ffejjfLu2oYGGUc4d"),
    ("hooks", "ses_e214e001dffedXQy4uRjPD3uxY"),
    ("main", "ses_e25f231a6ffew6tj3XborxECa0"),
]

LOG = []
FAILS = []


def log(msg: str) -> None:
    LOG.append(msg)
    print(msg)


def norm(path: str) -> Path:
    p = path.replace(OLD, str(ROOT))
    q = Path(p)
    if not q.is_absolute():
        q = ROOT / q
    return q.resolve()


def inside(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def write_file(path: Path, content: str) -> None:
    if not inside(path):
        log(f"SKIP outside: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def apply_hunks(path: Path, hunks: list[dict]) -> None:
    if not path.exists():
        FAILS.append(f"update missing file: {path}")
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    pos = 0
    for hunk in hunks:
        before, after = hunk["before"], hunk["after"]
        found = -1
        if not before:
            found = pos
        else:
            for j in range(pos, len(lines) - len(before) + 1):
                if lines[j:j + len(before)] == before:
                    found = j
                    break
            if found < 0:
                # tolerate trailing whitespace differences
                b2 = [x.rstrip() for x in before]
                for j in range(pos, len(lines) - len(before) + 1):
                    if [x.rstrip() for x in lines[j:j + len(before)]] == b2:
                        found = j
                        break
        if found < 0:
            FAILS.append(f"hunk not found in {path}: {before[:3]!r}")
            continue
        lines[found:found + len(before)] = after
        pos = found + len(after)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def apply_patch(text: str) -> None:
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("*** Add File: "):
            path = norm(line[len("*** Add File: "):].strip())
            i += 1
            content = []
            while i < len(lines) and not lines[i].startswith("*** "):
                l = lines[i]
                content.append(l[1:] if l.startswith("+") else l)
                i += 1
            write_file(path, "\n".join(content) + "\n")
            log(f"ADD {path}")
            continue
        if line.startswith("*** Update File: "):
            path = norm(line[len("*** Update File: "):].strip())
            i += 1
            hunks: list[dict] = []
            cur: dict | None = None
            while i < len(lines) and not lines[i].startswith("*** "):
                l = lines[i]
                if l.startswith("@@"):
                    if cur is not None:
                        hunks.append(cur)
                    cur = {"before": [], "after": []}
                elif cur is not None:
                    if l.startswith("+"):
                        cur["after"].append(l[1:])
                    elif l.startswith("-"):
                        cur["before"].append(l[1:])
                    elif l.startswith(" "):
                        cur["before"].append(l[1:])
                        cur["after"].append(l[1:])
                    elif l == "":
                        cur["before"].append("")
                        cur["after"].append("")
                i += 1
            if cur is not None:
                hunks.append(cur)
            apply_hunks(path, hunks)
            log(f"PATCH {path} ({len(hunks)} hunks)")
            continue
        if line.startswith("*** Delete File: "):
            path = norm(line[len("*** Delete File: "):].strip())
            if inside(path) and path.exists():
                path.unlink()
                log(f"DELETE {path}")
            i += 1
            continue
        i += 1


def apply_edit(inp: dict) -> None:
    raw = inp.get("path") or inp.get("filePath") or inp.get("file_path")
    if not raw:
        return
    path = norm(raw)
    if not path.exists():
        FAILS.append(f"edit missing file: {path}")
        return
    old = inp.get("oldString", "")
    new = inp.get("newString", "")
    text = path.read_text(encoding="utf-8")
    if old not in text:
        FAILS.append(f"edit oldString not found: {path}")
        return
    text = text.replace(old, new, -1 if inp.get("replaceAll") else 1)
    path.write_text(text, encoding="utf-8")
    log(f"EDIT {path}")


def should_run_shell(cmd: str, session: str) -> bool:
    if "rm -rf" in cmd:
        return False
    if session == "rewrite":
        return bool(re.search(
            r"(write_text|json\.dumps|cat\s*>|sed\s+-i|cp\s+|mkdir|ln\s+-s|tee\s+|>\s*\.?/?(agents|sop|policies|skills|runbooks|opencode\.json|\.agt))",
            cmd,
        ))
    if session == "main":
        markers = [
            'PERMS = """permissions:',
            "for name in ('orchestrator.md','auditor.md')",
            "p = Path('policies/release-predicates.json')",
            'mkdir -p "$R/.opencode/agents"',
            "d['type']='module'",
            "def _k1_env()",
            "ROOT=/tmp/opencode/factory-v2",
        ]
        return any(m in cmd for m in markers) and "cp -a \"$SRC\"/files" not in cmd
    return False


def run_shell(cmd: str, session: str) -> None:
    if not should_run_shell(cmd, session):
        return
    fixed = cmd.replace(OLD, str(ROOT))
    fixed = fixed.replace("/tmp/rewrite_factory_v2.py", str(ROOT / ".rewrite_factory_v2.py"))
    # The staging copy was already performed; never re-copy over the tree.
    if "cp -a \"$SRC\"/files" in fixed:
        return
    log(f"SHELL[{session}] {fixed.splitlines()[0][:100]}")
    proc = subprocess.run(
        fixed, shell=True, cwd=str(ROOT), text=True,
        capture_output=True, executable="/bin/bash",
    )
    if proc.returncode != 0:
        FAILS.append(f"shell failed ({session}): {fixed.splitlines()[0][:120]} :: {proc.stderr[-300:]}")


def main() -> int:
    if not ROOT.exists():
        print(f"missing target {ROOT}", file=sys.stderr)
        return 2
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    for session, sid in SESSIONS:
        log(f"===== {session} {sid} =====")
        for (data,) in con.execute(
            "select data from session_message where session_id=? and type='assistant' order by seq",
            (sid,),
        ):
            d = json.loads(data)
            for part in d.get("content") or []:
                if not isinstance(part, dict) or part.get("type") != "tool":
                    continue
                name = part.get("name")
                inp = (part.get("state") or {}).get("input") or {}
                try:
                    if name in ("patch", "apply_patch"):
                        text = inp.get("patchText") or inp.get("patch") or ""
                        if text:
                            apply_patch(text)
                    elif name == "write":
                        raw = inp.get("path") or inp.get("filePath")
                        if raw:
                            write_file(norm(raw), inp.get("content", ""))
                            log(f"WRITE {norm(raw)}")
                    elif name in ("edit", "multiedit"):
                        if isinstance(inp, dict):
                            apply_edit(inp)
                    elif name == "shell":
                        cmd = inp.get("command") or inp.get("cmd") or ""
                        if isinstance(cmd, str):
                            run_shell(cmd, session)
                except Exception as exc:  # noqa: BLE001
                    FAILS.append(f"{name} exception: {exc}")
    (ROOT / "RECOVERY.log").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    print("\n===== FAILURES =====")
    for f in FAILS:
        print(f)
    print(f"failures: {len(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
