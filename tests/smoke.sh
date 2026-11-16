#!/usr/bin/env bash
set -euo pipefail

factory_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
smoke_root="$factory_root/.smoke"
trap 'rm -rf "$smoke_root"' EXIT
rm -rf "$smoke_root"
mkdir -p "$smoke_root/evidence" "$smoke_root/factory-home" "$smoke_root/integration" "$smoke_root/task"

export FACTORY_EVIDENCE_ROOT="$smoke_root/evidence"
export FACTORY_HOME="$smoke_root/factory-home"
export PATH="$factory_root/bin:$PATH"

repo="$smoke_root/integration"
task_wt="$smoke_root/task"
task_id="T-SMOKE-001"
branch="smoke/task"

git -C "$repo" init -q
git -C "$repo" config user.name "Factory Smoke"
git -C "$repo" config user.email "factory-smoke@example.invalid"
printf '# Synthetic task\n' > "$repo/README.md"
git -C "$repo" add README.md
git -C "$repo" commit -qm "Initialize synthetic repository"
git -C "$repo" worktree add -q -b "$branch" "$task_wt" HEAD

(
  cd "$task_wt"
  "$factory_root/bin/factory" task new \
    --kind DOC --lane spec-comp --scope README.md \
    --worktree "$task_wt" --branch "$branch" --task-id "$task_id" --json >/dev/null
  printf '\nSmoke-tested locally.\n' >> README.md
  git add README.md
  git commit -qm "Smoke test factory task" -m "Task-Id: $task_id"
  "$factory_root/bin/factory" gate --task-id "$task_id" --checks SCOPE,CONVENTION
  "$factory_root/bin/factory" verify --task-id "$task_id"
)

"$factory_root/bin/factory" release --task-id "$task_id" --repo "$repo" --dry-run

echo "factory smoke: PASS"
