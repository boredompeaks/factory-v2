#!/usr/bin/env bash
set -euo pipefail

force=0
worktree=""
while (($#)); do
  case "$1" in
    --force) force=1; shift ;;
    --worktree)
      (($# >= 2)) || { echo "usage: install.sh [--force] [--worktree PATH]" >&2; exit 2; }
      worktree=$2
      shift 2
      ;;
    -h|--help)
      echo "Usage: install.sh [--force] [--worktree PATH]"
      exit 0
      ;;
    *) echo "install.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
done
worktree=${worktree:-$PWD}
repo=$(git -C "$worktree" rev-parse --show-toplevel) || {
  echo "install.sh: not a Git worktree: $worktree" >&2
  exit 1
}
hooks_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
current=$(git -C "$repo" config --local --get core.hooksPath || true)
if [[ -n "$current" ]]; then
  current_abs=$(python3 - "$repo" "$current" <<'PY'
import os, sys
repo, configured = sys.argv[1:]
path = configured if os.path.isabs(configured) else os.path.join(repo, configured)
print(os.path.realpath(path))
PY
)
  if [[ "$current_abs" == "$hooks_dir" ]]; then
    echo "factory hooks already installed in $repo"
    exit 0
  fi
  if ((!force)); then
    echo "install.sh: custom core.hooksPath is already set to '$current'; use --force to replace it" >&2
    exit 1
  fi
fi
git -C "$repo" config --local core.hooksPath "$hooks_dir"
echo "installed factory hooks in $repo"
