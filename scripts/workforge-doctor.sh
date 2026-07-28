#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
workforge_bin="${WORKFORGE_BIN:-$repo_root/../workforge/.venv/bin/workforge}"
workforge_python="${WORKFORGE_PYTHON:-$repo_root/../workforge/.venv/bin/python}"

pass() {
  printf 'ok: %s\n' "$1"
}

warn() {
  printf 'warning: %s\n' "$1" >&2
}

fail() {
  printf 'error: %s\n' "$1" >&2
  exit 1
}

cd -- "$repo_root"

git rev-parse --verify HEAD >/dev/null 2>&1 \
  || fail "Reddit Radar must be a git repository with at least one commit"
pass "git repository has a committed HEAD"

test -x "$workforge_bin" || fail "WorkForge executable not found: $workforge_bin"
test -x "$workforge_python" || fail "WorkForge Python not found: $workforge_python"
workforge_revision="$(git -C "$repo_root/../workforge" rev-parse --short HEAD)"
pass "WorkForge executable is available at revision $workforge_revision"

"$workforge_python" - <<'PY'
from importlib import import_module

for module in ("workforge", "temporalio", "asyncpg", "fastapi", "uvicorn"):
    import_module(module)
PY
pass "WorkForge and durable/dashboard Python extras import successfully"

command -v claude >/dev/null 2>&1 || fail "authenticated Claude CLI is required"
claude_version="$(claude --version 2>/dev/null | head -n 1)"
pass "Claude CLI is available${claude_version:+ ($claude_version)}"

test -f AGENTS.md || fail "AGENTS.md is required for dynamic prompts"
test -f CLAUDE.md || fail "CLAUDE.md is required for repository guidance"
if test -L AGENTS.md && test "$(readlink AGENTS.md)" = "CLAUDE.md"; then
  pass "AGENTS.md points to CLAUDE.md"
else
  warn "AGENTS.md is not the expected symlink to CLAUDE.md; verify both stay aligned"
fi

for ignored_path in .env reddit.db reddit.db-wal reddit.db-shm raw/probe reports/probe \
  .workforge/probe .venv/probe web/frontend/node_modules/probe; do
  git check-ignore -q -- "$ignored_path" \
    || fail "runtime path is not ignored: $ignored_path"
done
tracked_runtime="$(git ls-files -- .env reddit.db 'reddit.db-*' 'reddit.db.backup-*' \
  'raw/**' 'reports/**' '.workforge/**')"
test -z "$tracked_runtime" \
  || fail "secret, live-data or WorkForge runtime paths are tracked"
pass "secrets, live data, WorkForge state and build dependencies are ignored"

spec_count=0
for spec_path in workforge-specs/*.md; do
  case "$spec_path" in
    workforge-specs/README.md|workforge-specs/TEMPLATE.md) continue ;;
  esac
  "$workforge_bin" validate "$spec_path" >/dev/null
  printf 'ok: validated %s\n' "$spec_path"
  spec_count=$((spec_count + 1))
done
test "$spec_count" -gt 0 || fail "no runnable WorkForge specs found"

"$workforge_bin" status --repo .
"$workforge_bin" conflicts --repo .

if test -n "$(git status --porcelain)"; then
  warn "base repo is dirty; a new lane will branch from HEAD and omit these changes"
else
  pass "base repo is clean and ready to be used as a lane base"
fi

if command -v temporal >/dev/null 2>&1; then
  pass "optional Temporal CLI is available"
else
  warn "optional Temporal CLI is not installed; direct dynamic mode is still ready"
fi

pass "Reddit Radar WorkForge direct-mode setup is ready"
