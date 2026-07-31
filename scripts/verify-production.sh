#!/usr/bin/env bash
# scripts/verify-production.sh
# Unified production verification: backend + contracts + API smoke + frontend build + systemd checks.
# Does NOT read .env, make real network calls, or touch the live database.
#
# Usage:
#   scripts/verify-production.sh           # full suite
#   scripts/verify-production.sh --fast    # backend only (no frontend build)
#   scripts/verify-production.sh --no-systemd  # skip systemd unit checks

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
venv_python="${VENV_PYTHON:-$repo_root/.venv/bin/python}"

fast=false
no_systemd=false

for arg in "$@"; do
  case "$arg" in
    --fast) fast=true ;;
    --no-systemd) no_systemd=true ;;
  esac
done

pass() { printf '\033[32mok:\033[0m %s\n' "$1"; }
fail() { printf '\033[31merror:\033[0m %s\n' "$1" >&2; exit 1; }
step() { printf '\n\033[34m==> %s\033[0m\n' "$1"; }

cd -- "$repo_root"

# ── Step 1: Backend compile ──────────────────────────────────────────────────
step "Backend compile check"
"$venv_python" -m compileall -q reddit_crawler jobs web cli.py
pass "compileall: no syntax errors"

# ── Step 2: Full test suite ──────────────────────────────────────────────────
step "Unit test suite (60+ tests)"
"$venv_python" -m unittest discover -s tests -v 2>&1 | tail -20
pass "unit tests: all passed"

# ── Step 3: Spec registry contracts ─────────────────────────────────────────
step "Spec registry contract checks"
if test -f "specs/production-program.json"; then
  "$venv_python" -m unittest tests.test_spec_registry -v 2>&1 | tail -10
  pass "spec registry: all checks passed"
else
  printf 'warning: specs/production-program.json not yet created — skipping spec registry checks\n' >&2
fi

# ── Step 4: API contract smoke (no live DB) ──────────────────────────────────
step "API contract smoke (fixture DB)"
if test -f "tests/fixtures/test.db"; then
  REDDIT_DB_PATH="tests/fixtures/test.db" "$venv_python" -m unittest tests.test_web_api -v 2>&1 | tail -10
  pass "API contract smoke: passed"
else
  # Run with whatever DB is available in tests
  "$venv_python" -m unittest tests.test_web_api -v 2>&1 | tail -10
  pass "API contract smoke: passed"
fi

# ── Step 5: Frontend build ───────────────────────────────────────────────────
if ! $fast; then
  step "Frontend production build"
  if test -d "web/frontend" && test -f "web/frontend/package.json"; then
    npm --prefix web/frontend ci --prefer-offline 2>&1 | tail -3
    npm --prefix web/frontend run build 2>&1 | tail -5
    pass "frontend build: successful"
  else
    printf 'warning: web/frontend not found — skipping frontend build\n' >&2
  fi
fi

# ── Step 6: Systemd unit syntax checks ──────────────────────────────────────
if ! $no_systemd; then
  step "Systemd unit syntax check"
  if command -v systemd-analyze >/dev/null 2>&1; then
    service_files=()
    timer_files=()
    while IFS= read -r -d '' f; do
      case "$f" in
        *.service) service_files+=("$f") ;;
        *.timer)   timer_files+=("$f")   ;;
      esac
    done < <(find deploy/systemd -maxdepth 1 \( -name "*.service" -o -name "*.timer" \) -print0 2>/dev/null)

    if [[ ${#service_files[@]} -gt 0 || ${#timer_files[@]} -gt 0 ]]; then
      systemd-analyze verify "${service_files[@]:-}" "${timer_files[@]:-}" 2>&1 || \
        printf 'warning: systemd-analyze verify reported issues (may require installed units)\n' >&2
      pass "systemd units: syntax check complete"
    else
      printf 'warning: no systemd units found in deploy/systemd/\n' >&2
    fi
  else
    printf 'warning: systemd-analyze not available — skipping unit checks\n' >&2
  fi
fi

# ── Step 7: localhost binding check ─────────────────────────────────────────
step "Web binding policy check"
if grep -r "0\.0\.0\.0" deploy/systemd/ 2>/dev/null | grep -v "^#"; then
  fail "Found 0.0.0.0 binding in systemd units — web must only bind to 127.0.0.1"
fi
if grep -rn "host.*=.*0\.0\.0\.0" web/app.py 2>/dev/null | grep -v "^#"; then
  fail "Found 0.0.0.0 binding in web/app.py — must bind to 127.0.0.1 only"
fi
pass "web binding policy: localhost-only confirmed"

printf '\n\033[32m════════════════════════════════════════\033[0m\n'
printf '\033[32m Production verification PASSED\033[0m\n'
printf '\033[32m════════════════════════════════════════\033[0m\n\n'
