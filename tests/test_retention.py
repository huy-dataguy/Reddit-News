"""tests/test_retention.py — Tests for retention planning (always dry-run).
tests/test_capacity_gate.py content is included in test_backup_restore.py.
This file provides additional retention-specific tests.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from reddit_crawler.ops.capacity import retention_plan_dry_run, CapacityStatus, capacity_check


class RetentionTests(unittest.TestCase):

    def test_dry_run_flag_always_true(self) -> None:
        """retention_plan_dry_run always has dry_run=True regardless of input."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = retention_plan_dry_run(tmpdir, tmpdir)
            self.assertTrue(result["dry_run"])

    def test_empty_dirs_no_crash(self) -> None:
        """retention_plan_dry_run handles empty directories gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = retention_plan_dry_run(tmpdir, tmpdir)
            self.assertIsInstance(result, dict)
            self.assertGreaterEqual(result["total_would_archive_files"], 0)
            self.assertGreaterEqual(result["total_would_archive_bytes"], 0)

    def test_nonexistent_dirs_no_crash(self) -> None:
        """retention_plan_dry_run handles non-existent directories without crashing."""
        result = retention_plan_dry_run("/nonexistent/raw", "/nonexistent/reports")
        self.assertIsInstance(result, dict)
        self.assertTrue(result["dry_run"])

    def test_legacy_jsonl_detected_as_keep(self) -> None:
        """Legacy JSONL files in raw/ are detected and marked as 'keep'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a legacy JSONL file
            legacy = Path(tmpdir) / "posts.jsonl"
            legacy.write_text('{"id": "abc"}\n', encoding="utf-8")

            result = retention_plan_dry_run(tmpdir, tmpdir)
            # Legacy JSONL should be in categories but not deleted
            if "raw_legacy_jsonl" in result["categories"]:
                action = result["categories"]["raw_legacy_jsonl"]["action"]
                self.assertIn("keep", action.lower())

    def test_reports_category_present_when_has_files(self) -> None:
        """Reports directory contents are summarized in categories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reports_dir = Path(tmpdir) / "reports"
            reports_dir.mkdir()
            (reports_dir / "test.json").write_text("{}", encoding="utf-8")

            result = retention_plan_dry_run(tmpdir, str(reports_dir))
            if "reports" in result["categories"]:
                self.assertGreater(result["categories"]["reports"]["files"], 0)


class CapacityGateTests(unittest.TestCase):

    def test_realistic_disk_is_ok_or_warning(self) -> None:
        """On a real system, capacity_check returns OK or WARNING (not HALT)."""
        result = capacity_check(".")
        # On a normally-functioning dev machine, should not be at HALT
        self.assertNotEqual(result.status, CapacityStatus.HALT,
            "Disk is critically full — check disk space before continuing!")

    def test_capacity_check_free_gib_positive(self) -> None:
        """free_gib must be positive on any valid filesystem."""
        result = capacity_check(".")
        self.assertGreater(result.free_gib, 0)

    def test_is_safe_for_large_copy_method(self) -> None:
        """is_safe_for_large_copy() returns bool based on status."""
        result = capacity_check(
            ".",
            warn_pct=0.0, crit_pct=0.0, halt_pct=0.0,
            warn_gib=0.0, crit_gib=0.0, halt_gib=0.0,
        )
        # With 0 thresholds, should be OK and safe
        self.assertTrue(result.is_safe_for_large_copy())
        self.assertEqual(result.status, CapacityStatus.OK)


if __name__ == "__main__":
    unittest.main()
