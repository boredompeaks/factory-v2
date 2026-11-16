#!/usr/bin/env bash
# Verify a session-root workspace: shared-tree symlinks, generated config
# drift, carbon MCP discovery, .agt policy load, and mapped worktree
# head/tree/branch/task identity. Exit non-zero on any failure with a
# one-line reason.
set -uo pipefail

FACTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${1:-}"
if [[ -z "$WORKSPACE" ]]; then
  echo "verify_workspace: usage: $0 <workspace-dir>" >&2
  exit 2
fi
WORKSPACE="$(cd "$WORKSPACE" 2>/dev/null && pwd)" || {
  echo "verify_workspace: no such workspace: ${1}" >&2
  exit 2
}

fail() {
  echo "verify_workspace: FAIL: $*" >&2
  exit 1
}

# 1. Shared trees must be symlinks to the factory (no copies, no drift).
for pair in ".opencode:.opencode" ".agt:.agt" ".agents:.agents"; do
  link="${pair%%:*}"
  target="${pair##*:}"
  path="$WORKSPACE/$link"
  [[ -L "$path" ]] || fail "$link is not a symlink (stale copy?)"
  want="$(readlink -f "$FACTORY/$target" 2>/dev/null || true)"
  got="$(readlink -f "$path" 2>/dev/null || true)"
  [[ -n "$want" && "$got" == "$want" ]] ||
    fail "$link -> ${got:-<broken>} (expected $want)"
  echo "symlink: $link -> $got"
done

# 2. Generated config must match the generator byte-for-byte (drift gate).
python3 "$FACTORY/scripts/gen_opencode_config.py" --workspace "$WORKSPACE" --check ||
  fail "opencode.json drift (run gen_opencode_config.py --workspace $WORKSPACE --write)"
echo "config: $WORKSPACE/opencode.json in sync"

# 3. The carbon server must actually spawn and answer tools/list.
python3 "$FACTORY/scripts/mcp_discover.py" \
  --server carbon-orchestrator \
  --config "$WORKSPACE/opencode.json" >/dev/null ||
  fail "mcp_discover failed for carbon-orchestrator"
echo "mcp: carbon-orchestrator discovery OK"

# 4. The authz policy file must be valid JSON.
python3 - "$WORKSPACE/.agt/policy.json" <<'PY' || fail ".agt/policy.json missing or invalid"
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    policy = json.load(fh)
if not isinstance(policy, dict) or not policy:
    raise SystemExit("policy must be a non-empty object")
PY
echo "policy: .agt/policy.json loads"

# 5. Mapped worktrees must be on their branch and at the dispatch head/tree.
#    VERIFY_DISPATCH / VERIFY_LANE / VERIFY_TASK_ID narrow the assertion; with
#    none set, the newest dispatch record must match exactly one mapped lane.
_wt_args=(--map "$FACTORY/files/policies/worktree-map.json")
[[ -n "${VERIFY_DISPATCH:-}" ]] && _wt_args+=(--dispatch "$VERIFY_DISPATCH")
[[ -n "${VERIFY_LANE:-}" ]] && _wt_args+=(--lane "$VERIFY_LANE")
[[ -n "${VERIFY_TASK_ID:-}" ]] && _wt_args+=(--task-id "$VERIFY_TASK_ID")
python3 "$FACTORY/scripts/verify_worktrees.py" "${_wt_args[@]}" ||
  fail "worktree head/tree/branch/task mismatch (see verify_worktrees.py)"
echo "worktrees: branch + dispatch head/tree/task verified"

echo "verify_workspace: OK ($WORKSPACE)"
