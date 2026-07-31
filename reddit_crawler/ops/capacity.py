"""reddit_crawler/ops/capacity.py — Disk capacity gate and retention planner.

Checks disk usage against thresholds from the medallion spec:
- Warning: < 20% free OR < 40 GiB free → degraded, alert
- Critical: < 8% free OR < 15 GiB free → stop non-essential batch jobs
- Halt: < 5% free OR < 10 GiB free → hard stop all file-creating operations

Retention planner always runs dry-run by default.
Delete operations require explicit manual approval per spec.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


# Thresholds (configurable via env; these are defaults from spec)
DEFAULT_WARN_PCT = 20.0    # Warn below 20% free
DEFAULT_CRIT_PCT = 8.0     # Critical below 8% free (spec: 10% or 20 GiB)
DEFAULT_HALT_PCT = 5.0     # Hard halt below 5% free (spec: 10% or 15 GiB)

DEFAULT_WARN_GIB = 40.0
DEFAULT_CRIT_GIB = 15.0
DEFAULT_HALT_GIB = 10.0

GIB = 1024 ** 3


class CapacityStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    HALT = "halt"


@dataclass
class CapacityGate:
    """Result of a disk capacity check."""
    status: CapacityStatus
    filesystem: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    free_pct: float
    free_gib: float
    warn_threshold_pct: float
    crit_threshold_pct: float
    halt_threshold_pct: float
    warn_threshold_gib: float
    crit_threshold_gib: float
    halt_threshold_gib: float
    message: str
    should_stop_batch: bool  # True if CRITICAL or HALT
    should_hard_stop: bool   # True if HALT

    def is_safe_for_large_copy(self) -> bool:
        return self.status == CapacityStatus.OK

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "filesystem": self.filesystem,
            "total_bytes": self.total_bytes,
            "used_bytes": self.used_bytes,
            "free_bytes": self.free_bytes,
            "free_pct": self.free_pct,
            "free_gib": self.free_gib,
            "message": self.message,
            "should_stop_batch": self.should_stop_batch,
            "should_hard_stop": self.should_hard_stop,
        }


def capacity_check(
    path: str | Path = ".",
    *,
    warn_pct: float = DEFAULT_WARN_PCT,
    crit_pct: float = DEFAULT_CRIT_PCT,
    halt_pct: float = DEFAULT_HALT_PCT,
    warn_gib: float = DEFAULT_WARN_GIB,
    crit_gib: float = DEFAULT_CRIT_GIB,
    halt_gib: float = DEFAULT_HALT_GIB,
) -> CapacityGate:
    """Check disk capacity for the filesystem containing `path`.

    Returns a CapacityGate with status OK/WARNING/CRITICAL/HALT.
    Uses the more conservative of percentage and GiB thresholds.
    """
    path = Path(path).resolve()
    usage = shutil.disk_usage(str(path))

    total = usage.total
    used = usage.used
    free = usage.free
    free_pct = (free / total * 100) if total > 0 else 0.0
    free_gib = free / GIB

    # Determine status (fail on EITHER pct or GiB threshold)
    if free_pct < halt_pct or free_gib < halt_gib:
        status = CapacityStatus.HALT
        message = (
            f"HALT: disk critically low — {free_gib:.1f} GiB / {free_pct:.1f}% free. "
            "All file-creating operations must stop."
        )
    elif free_pct < crit_pct or free_gib < crit_gib:
        status = CapacityStatus.CRITICAL
        message = (
            f"CRITICAL: disk low — {free_gib:.1f} GiB / {free_pct:.1f}% free. "
            "Non-essential batch/backfill must stop."
        )
    elif free_pct < warn_pct or free_gib < warn_gib:
        status = CapacityStatus.WARNING
        message = (
            f"WARNING: disk space low — {free_gib:.1f} GiB / {free_pct:.1f}% free. "
            "Monitor and plan archive before operations."
        )
    else:
        status = CapacityStatus.OK
        message = f"OK: {free_gib:.1f} GiB / {free_pct:.1f}% free"

    return CapacityGate(
        status=status,
        filesystem=str(path),
        total_bytes=total,
        used_bytes=used,
        free_bytes=free,
        free_pct=round(free_pct, 2),
        free_gib=round(free_gib, 2),
        warn_threshold_pct=warn_pct,
        crit_threshold_pct=crit_pct,
        halt_threshold_pct=halt_pct,
        warn_threshold_gib=warn_gib,
        crit_threshold_gib=crit_gib,
        halt_threshold_gib=halt_gib,
        message=message,
        should_stop_batch=status in (CapacityStatus.CRITICAL, CapacityStatus.HALT),
        should_hard_stop=status == CapacityStatus.HALT,
    )


def retention_plan_dry_run(
    raw_dir: str | Path,
    reports_dir: str | Path,
    *,
    bronze_hot_days: int = 180,
    metrics_detail_days: int = 90,
) -> dict:
    """Dry-run retention planner: reports what WOULD be archived/cleaned.

    Always dry-run. Actual deletion requires manual approval.
    Returns a summary dict with bytes/object counts by category.
    """
    raw_dir = Path(raw_dir)
    reports_dir = Path(reports_dir)

    import time
    cutoff_bronze = time.time() - bronze_hot_days * 86400
    cutoff_metrics = time.time() - metrics_detail_days * 86400

    result = {
        "dry_run": True,
        "bronze_hot_days": bronze_hot_days,
        "metrics_detail_days": metrics_detail_days,
        "categories": {},
        "total_would_archive_bytes": 0,
        "total_would_archive_files": 0,
        "message": "DRY-RUN: no files will be deleted. Manual approval required for actual deletion.",
    }

    # Scan raw/bronze/ for old partitions
    bronze_root = raw_dir / "bronze"
    if bronze_root.exists():
        old_files = []
        old_bytes = 0
        for f in bronze_root.rglob("*.jsonl.gz"):
            try:
                mtime = f.stat().st_mtime
                if mtime < cutoff_bronze:
                    old_files.append(str(f))
                    old_bytes += f.stat().st_size
            except OSError:
                pass
        result["categories"]["bronze_cold"] = {
            "files": len(old_files),
            "bytes": old_bytes,
            "action": "archive (manual)",
        }
        result["total_would_archive_bytes"] += old_bytes
        result["total_would_archive_files"] += len(old_files)

    # Scan legacy raw/ JSONL files
    if raw_dir.exists():
        legacy_files = list(raw_dir.glob("*.jsonl"))
        legacy_bytes = sum(f.stat().st_size for f in legacy_files if f.exists())
        result["categories"]["raw_legacy_jsonl"] = {
            "files": len(legacy_files),
            "bytes": legacy_bytes,
            "action": "keep (compatibility window)",
        }

    # Scan reports/
    if reports_dir.exists():
        report_files = list(reports_dir.rglob("*.json")) + list(reports_dir.rglob("*.md"))
        report_bytes = sum(f.stat().st_size for f in report_files if f.exists())
        result["categories"]["reports"] = {
            "files": len(report_files),
            "bytes": report_bytes,
            "action": "keep (manual review for archival)",
        }

    return result
