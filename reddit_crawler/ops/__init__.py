"""reddit_crawler/ops — Operations and release engineering module.

Provides backup, restore-drill, permission checks, ops alerts, capacity gate,
and release manifest functionality for Reddit Radar internal production.

All operations are non-destructive by default (dry-run, read-only paths).
No .env values, DB secrets, or raw payloads are printed or stored.
"""
from __future__ import annotations

from .alerts import OpsAlert, AlertSeverity, alert_collector
from .backup import backup_db, BackupManifest
from .capacity import CapacityGate, capacity_check
from .permissions import check_permissions
from .release import ReleaseManifest, create_release_manifest

__all__ = [
    "OpsAlert",
    "AlertSeverity",
    "alert_collector",
    "backup_db",
    "BackupManifest",
    "CapacityGate",
    "capacity_check",
    "check_permissions",
    "ReleaseManifest",
    "create_release_manifest",
]
