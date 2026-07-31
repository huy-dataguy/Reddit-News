"""reddit_crawler/ops/alerts.py — Structured local ops alert system.

Alerts are written as structured JSON and surfaced in /api/health and
production status CLI. No external webhook/email/Telegram until operator
configures and approves a sink.

Alert types (per release-engineering spec):
- crawl_stale: Reddit collection freshness SLO breach
- timer_fail: systemd timer/service repeated failure or budget exceeded
- dq_hard_fail: data quality hard gate fail / Gold publish fail
- llm_breaker_open: LLM circuit breaker open beyond window
- backup_stale: backup not completed within RPO window
- restore_stale: restore drill not run within 90 days
- disk_warning: disk usage above warning threshold
- disk_critical: disk usage above critical threshold
- web_health_fail: web health check fails after deploy

Deduplication: one active alert per (type, resource, state).
Recovery events fire when state returns to normal.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


VALID_ALERT_TYPES = {
    "crawl_stale",
    "timer_fail",
    "dq_hard_fail",
    "llm_breaker_open",
    "backup_stale",
    "restore_stale",
    "disk_warning",
    "disk_critical",
    "web_health_fail",
}


@dataclass
class OpsAlert:
    """OpsAlertV1: structured alert record as per release-engineering spec."""
    id: str
    type: str
    resource: str
    severity: str
    state: str  # "firing" | "resolved"
    first_seen_at: float
    last_seen_at: float
    occurrences: int
    summary: str
    evidence_path: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "OpsAlert":
        return cls(**d)

    def dedup_key(self) -> str:
        return f"{self.type}::{self.resource}::{self.state}"


def _make_alert_id() -> str:
    return str(uuid.uuid4())


class AlertCollector:
    """Manages the local structured alert store.

    Alerts are stored as a JSON file at the configured path.
    Deduplication ensures only one active alert per (type, resource, state).
    """

    def __init__(self, store_path: Path | str):
        self.store_path = Path(store_path)

    def _load(self) -> dict[str, OpsAlert]:
        if not self.store_path.exists():
            return {}
        try:
            raw = json.loads(self.store_path.read_text(encoding="utf-8"))
            return {k: OpsAlert.from_dict(v) for k, v in raw.items()}
        except Exception:
            return {}

    def _save(self, alerts: dict[str, OpsAlert]) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({k: v.to_dict() for k, v in alerts.items()}, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.store_path)

    def fire(
        self,
        alert_type: str,
        resource: str,
        severity: AlertSeverity | str,
        summary: str,
        evidence_path: Optional[str] = None,
    ) -> OpsAlert:
        """Record a firing alert. Deduplicates on (type, resource, 'firing')."""
        if alert_type not in VALID_ALERT_TYPES:
            raise ValueError(f"Unknown alert type: {alert_type!r}. Valid: {VALID_ALERT_TYPES}")
        if isinstance(severity, AlertSeverity):
            severity = severity.value

        alerts = self._load()
        now = time.time()
        key = f"{alert_type}::{resource}::firing"
        if key in alerts:
            existing = alerts[key]
            existing.last_seen_at = now
            existing.occurrences += 1
            existing.summary = summary
            if evidence_path:
                existing.evidence_path = evidence_path
        else:
            alerts[key] = OpsAlert(
                id=_make_alert_id(),
                type=alert_type,
                resource=resource,
                severity=severity,
                state="firing",
                first_seen_at=now,
                last_seen_at=now,
                occurrences=1,
                summary=summary,
                evidence_path=evidence_path,
            )
        self._save(alerts)
        return alerts[key]

    def resolve(
        self,
        alert_type: str,
        resource: str,
        summary: str = "",
    ) -> Optional[OpsAlert]:
        """Resolve a firing alert and record a resolved event."""
        alerts = self._load()
        now = time.time()
        fire_key = f"{alert_type}::{resource}::firing"
        resolved = None
        if fire_key in alerts:
            orig = alerts.pop(fire_key)
            res_key = f"{alert_type}::{resource}::resolved"
            alerts[res_key] = OpsAlert(
                id=_make_alert_id(),
                type=alert_type,
                resource=resource,
                severity=orig.severity,
                state="resolved",
                first_seen_at=orig.first_seen_at,
                last_seen_at=now,
                occurrences=orig.occurrences,
                summary=summary or f"Resolved: {orig.summary}",
                evidence_path=orig.evidence_path,
            )
            resolved = alerts[res_key]
        self._save(alerts)
        return resolved

    def list_firing(self) -> list[OpsAlert]:
        alerts = self._load()
        return [a for a in alerts.values() if a.state == "firing"]

    def list_all(self) -> list[OpsAlert]:
        return list(self._load().values())

    def clear_resolved(self) -> int:
        """Remove resolved alerts older than 24 hours. Returns count removed."""
        alerts = self._load()
        cutoff = time.time() - 86400
        to_remove = [k for k, v in alerts.items() if v.state == "resolved" and v.last_seen_at < cutoff]
        for k in to_remove:
            del alerts[k]
        self._save(alerts)
        return len(to_remove)


def alert_collector(store_path: Path | str | None = None) -> AlertCollector:
    """Get an AlertCollector for the given path (default: reports/operations/alerts.json)."""
    if store_path is None:
        store_path = Path("reports/operations/alerts.json")
    return AlertCollector(store_path)
