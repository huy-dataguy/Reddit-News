#!/usr/bin/env bash
# scripts/verify-clean-python-lock.sh
# Verifies Python environment has correct packages from requirements.lock.
# Does NOT read .env or print secret values.
#
# Usage:
#   scripts/verify-clean-python-lock.sh          # verify current .venv
#   scripts/verify-clean-python-lock.sh --strict  # also run pip check

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
venv_python="${VENV_PYTHON:-$repo_root/.venv/bin/python}"
venv_pip="${VENV_PIP:-$repo_root/.venv/bin/pip}"
lock_file="$repo_root/requirements.lock"
strict=false

for arg in "$@"; do
  case "$arg" in
    --strict) strict=true ;;
  esac
done

pass() { printf 'ok: %s\n' "$1"; }
fail() { printf 'error: %s\n' "$1" >&2; exit 1; }

cd -- "$repo_root"

test -f "$lock_file" || fail "requirements.lock not found at $lock_file"
pass "requirements.lock exists"

test -x "$venv_python" || fail "Python venv not found: $venv_python — run: python3 -m venv .venv && .venv/bin/pip install -r requirements.lock"
pass "Python venv is available"

python_version="$("$venv_python" --version 2>&1)"
pass "Python version: $python_version"

# Check that every pinned package in requirements.lock is installed at the pinned version
missing=0
while IFS= read -r line; do
  # Skip comments and empty lines
  [[ "$line" =~ ^#.*$ ]] && continue
  [[ -z "$line" ]] && continue

  # Extract package==version
  pkg="${line%%==*}"
  ver="${line#*==}"
  if [[ "$pkg" == "$line" ]]; then
    # No == found, skip (might be a comment or different format)
    continue
  fi

  installed_ver="$("$venv_pip" show "$pkg" 2>/dev/null | grep -i '^Version:' | awk '{print $2}' || echo 'NOT_FOUND')"
  if [[ "$installed_ver" != "$ver" ]]; then
    printf 'MISMATCH: %s expected==%s got==%s\n' "$pkg" "$ver" "$installed_ver" >&2
    missing=$((missing + 1))
  fi
done < "$lock_file"

if [[ $missing -gt 0 ]]; then
  fail "$missing package(s) have version mismatches — run: .venv/bin/pip install -r requirements.lock"
fi
pass "all pinned packages match requirements.lock"

if $strict; then
  "$venv_pip" check
  pass "pip check: no dependency conflicts"
fi

pass "Python lock verification complete"
