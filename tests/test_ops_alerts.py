"""tests/test_ops_alerts.py — Tests for structured ops alert system.

Verifies:
- fire() creates alerts with correct deduplication
- resolve() creates recovery events
- Fake clock can be used for time-sensitive tests
- Invalid alert types are rejected
- list_firing() returns only active alerts
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from reddit_crawler.ops.alerts import (
    AlertCollector,
    AlertSeverity,
    OpsAlert,
    VALID_ALERT_TYPES,
    alert_collector,
)


class AlertCollectorTests(unittest.TestCase):

    def _collector(self) -> tuple[AlertCollector, str]:
        tmpdir = tempfile.mkdtemp()
        store_path = os.path.join(tmpdir, "alerts.json")
        return AlertCollector(store_path), tmpdir

    def test_fire_creates_alert(self) -> None:
        """fire() creates a new alert with state=firing."""
        collector, tmpdir = self._collector()
        alert = collector.fire(
            "crawl_stale", "reddit", AlertSeverity.WARNING,
            "Reddit crawl has not run in > 2 hours",
        )
        self.assertEqual(alert.type, "crawl_stale")
        self.assertEqual(alert.state, "firing")
        self.assertEqual(alert.occurrences, 1)
        self.assertEqual(alert.severity, "warning")
        self.assertIsInstance(alert.id, str)

    def test_fire_deduplicates_same_alert(self) -> None:
        """Firing same alert twice increments occurrences, not creates duplicate."""
        collector, tmpdir = self._collector()
        collector.fire("disk_warning", "db-volume", AlertSeverity.WARNING, "Disk at 85%")
        collector.fire("disk_warning", "db-volume", AlertSeverity.WARNING, "Disk at 87%")

        alerts = collector.list_firing()
        # Should be only one active disk_warning for db-volume
        disk_alerts = [a for a in alerts if a.type == "disk_warning" and a.resource == "db-volume"]
        self.assertEqual(len(disk_alerts), 1)
        self.assertEqual(disk_alerts[0].occurrences, 2)
        self.assertEqual(disk_alerts[0].summary, "Disk at 87%")

    def test_fire_different_resources_not_deduplicated(self) -> None:
        """Alerts for different resources are separate records."""
        collector, tmpdir = self._collector()
        collector.fire("disk_warning", "db-volume", AlertSeverity.WARNING, "Disk A low")
        collector.fire("disk_warning", "raw-volume", AlertSeverity.WARNING, "Disk B low")

        alerts = collector.list_firing()
        disk_alerts = [a for a in alerts if a.type == "disk_warning"]
        self.assertEqual(len(disk_alerts), 2)

    def test_resolve_fires_recovery_event(self) -> None:
        """resolve() removes firing alert and creates a resolved record."""
        collector, tmpdir = self._collector()
        collector.fire("backup_stale", "reddit.db", AlertSeverity.CRITICAL, "Backup overdue")
        resolved = collector.resolve("backup_stale", "reddit.db", "Backup completed")

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.state, "resolved")
        self.assertEqual(resolved.type, "backup_stale")

        # Should no longer appear in firing
        firing = collector.list_firing()
        self.assertFalse(any(a.type == "backup_stale" for a in firing))

    def test_resolve_nonexistent_returns_none(self) -> None:
        """resolve() returns None when no matching firing alert exists."""
        collector, tmpdir = self._collector()
        result = collector.resolve("crawl_stale", "nonexistent-resource")
        self.assertIsNone(result)

    def test_invalid_alert_type_raises(self) -> None:
        """fire() raises ValueError for unknown alert types."""
        collector, tmpdir = self._collector()
        with self.assertRaises(ValueError) as ctx:
            collector.fire("invalid_type", "resource", AlertSeverity.WARNING, "test")
        self.assertIn("invalid_type", str(ctx.exception))

    def test_list_all_includes_resolved(self) -> None:
        """list_all() includes both firing and resolved alerts."""
        collector, tmpdir = self._collector()
        collector.fire("web_health_fail", "dashboard", AlertSeverity.CRITICAL, "500 error")
        collector.resolve("web_health_fail", "dashboard", "Service recovered")

        all_alerts = collector.list_all()
        self.assertTrue(any(a.state == "resolved" for a in all_alerts))

    def test_alert_store_is_json(self) -> None:
        """Alert store file is valid JSON parseable with standard library."""
        collector, tmpdir = self._collector()
        collector.fire("dq_hard_fail", "gold-publish", AlertSeverity.CRITICAL, "DQ gate failed")

        store_path = Path(collector.store_path)
        self.assertTrue(store_path.exists())
        data = json.loads(store_path.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)

    def test_clear_resolved_removes_old(self) -> None:
        """clear_resolved() removes resolved alerts older than 24 hours."""
        collector, tmpdir = self._collector()
        collector.fire("timer_fail", "crawl-timer", AlertSeverity.WARNING, "Timer missed")
        collector.resolve("timer_fail", "crawl-timer", "Timer recovered")

        # Manually age the resolved alert
        alerts = collector._load()
        for k, v in alerts.items():
            if v.state == "resolved":
                v.last_seen_at = time.time() - 90000  # 25 hours ago
        collector._save(alerts)

        removed = collector.clear_resolved()
        self.assertGreater(removed, 0)
        self.assertEqual(len(collector.list_all()), 0)

    def test_alert_collector_factory(self) -> None:
        """alert_collector() factory returns AlertCollector instance."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "alerts.json")
            coll = alert_collector(path)
            self.assertIsInstance(coll, AlertCollector)

    def test_fake_clock_for_time_sensitive_dedup(self) -> None:
        """Alerts use last_seen_at update timestamp correctly with fake clock."""
        collector, tmpdir = self._collector()
        fake_time = [1000.0]

        def mock_time():
            return fake_time[0]

        with patch("reddit_crawler.ops.alerts.time.time", side_effect=mock_time):
            fake_time[0] = 1000.0
            alert1 = collector.fire("crawl_stale", "reddit", AlertSeverity.WARNING, "Stale")
            fake_time[0] = 2000.0
            alert2 = collector.fire("crawl_stale", "reddit", AlertSeverity.WARNING, "Still stale")

        firing = collector.list_firing()
        self.assertEqual(len(firing), 1)
        self.assertEqual(firing[0].first_seen_at, 1000.0)
        self.assertEqual(firing[0].last_seen_at, 2000.0)
        self.assertEqual(firing[0].occurrences, 2)

    def test_all_valid_alert_types_accepted(self) -> None:
        """All VALID_ALERT_TYPES can be fired without ValueError."""
        collector, tmpdir = self._collector()
        for alert_type in VALID_ALERT_TYPES:
            # Should not raise
            collector.fire(alert_type, f"resource-{alert_type}", AlertSeverity.INFO, "test")

        # All should appear in list_firing
        firing = collector.list_firing()
        fired_types = {a.type for a in firing}
        self.assertEqual(fired_types, VALID_ALERT_TYPES)


if __name__ == "__main__":
    unittest.main()
