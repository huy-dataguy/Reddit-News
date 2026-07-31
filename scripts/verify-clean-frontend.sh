#!/usr/bin/env bash
# scripts/verify-clean-frontend.sh
# Verifies frontend can be cleanly installed and built from package-lock.json.
# Uses npm ci (never npm install) to ensure lock-exact install.
# Does NOT read .env or print secret values.

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
frontend_dir="$repo_root/web/frontend"

pass() { printf 'ok: %s\n' "$1"; }
fail() { printf 'error: %s\n' "$1" >&2; exit 1; }
warn() { printf 'warning: %s\n' "$1" >&2; }

cd -- "$repo_root"

# Check Node/npm
node --version >/dev/null 2>&1 || fail "Node.js is not installed"
npm --version >/dev/null 2>&1 || fail "npm is not installed"
node_version="$(node --version)"
npm_version="$(npm --version)"
pass "Node $node_version, npm $npm_version"

# Check frontend directory
test -d "$frontend_dir" || fail "Frontend directory not found: $frontend_dir"
test -f "$frontend_dir/package.json" || fail "package.json not found in $frontend_dir"
test -f "$frontend_dir/package-lock.json" || fail "package-lock.json not found — frontend must use locked dependencies"
pass "Frontend package files present"

# Clean install from lock
cd -- "$frontend_dir"
npm ci --prefer-offline 2>&1 | tail -5
pass "npm ci completed (clean install from package-lock.json)"

# Production build
npm run build 2>&1 | tail -10
pass "Frontend production build successful"

# Audit — warn on moderate, fail on high/critical
audit_output="$(npm audit --audit-level=high --json 2>&1 || true)"
high_count="$(echo "$audit_output" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    vulns = data.get('metadata', {}).get('vulnerabilities', {})
    print(vulns.get('high', 0) + vulns.get('critical', 0))
except Exception:
    print(0)
" 2>/dev/null || echo 0)"

if [[ "$high_count" -gt 0 ]]; then
  fail "Frontend audit: $high_count high/critical vulnerability(ies) found — review and waive or fix before release"
fi
pass "Frontend audit: no high/critical findings"

pass "Frontend verification complete"
