#!/usr/bin/env bash
# ==============================================================================
# REDDIT RADAR — AUTOMATED AUTOSTART SETUP SCRIPT
# Installs and enables all Systemd User Services & Timers for autostart on boot.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "==> Setting up Reddit Radar Autostart & Timers..."
echo "    Project Root: ${PROJECT_ROOT}"

# 1. Enable linger so user systemd services run even before/after GUI login
echo "==> Enabling loginctl linger for user ${USER}..."
loginctl enable-linger "${USER}" || true

# 2. Create systemd user config folder
USER_SYSTEMD_DIR="${HOME}/.config/systemd/user"
mkdir -p "${USER_SYSTEMD_DIR}"

# 3. Copy all service and timer definitions from repo
echo "==> Copying systemd configuration files from deploy/systemd/..."
cp -f "${PROJECT_ROOT}/deploy/systemd/"*.service "${USER_SYSTEMD_DIR}/"
cp -f "${PROJECT_ROOT}/deploy/systemd/"*.timer "${USER_SYSTEMD_DIR}/"

# 4. Reload user systemd daemon
echo "==> Reloading systemd user daemon..."
systemctl --user daemon-reload

# 5. Enable and start web dashboard & all background timers
echo "==> Enabling and starting services and timers..."

systemctl --user enable --now reddit-web.service
systemctl --user enable --now reddit-crawl.timer
systemctl --user enable --now reddit-enrich.timer
systemctl --user enable --now reddit-gemini-backlog.timer
systemctl --user enable --now reddit-ai@3h.timer
systemctl --user enable --now reddit-ai@day.timer
systemctl --user enable --now reddit-report@day.timer
systemctl --user enable --now reddit-backup.timer

echo ""
echo "════════════════════════════════════════════════════════════════"
echo " ✅ Autostart Setup Complete!"
echo "    Web Dashboard: http://127.0.0.1:8080"
echo "    Linger: Enabled (runs automatically when machine turns on)"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "==> Active Timers Summary:"
systemctl --user list-timers --no-pager
