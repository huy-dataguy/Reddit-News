"""tests/test_backup_restore.py — Tests for backup, restore-drill, and capacity gate.

Tests use temporary directories and in-memory/temp SQLite databases.
Never reads or writes to the live reddit.db or .env.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from reddit_crawler.ops.backup import backup_db, BackupManifest, restore_drill
from reddit_crawler.ops.capacity import capacity_check, CapacityStatus, retention_plan_dry_run


def _make_test_db(path: str) -> None:
    """Create a minimal SQLite DB that resembles the reddit schema."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_migration (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT OR IGNORE INTO schema_migration (version, name) VALUES (9, 'test_baseline');

        CREATE TABLE IF NOT EXISTS fact_post (
            post_id TEXT PRIMARY KEY,
            title TEXT
        );
        INSERT OR IGNORE INTO fact_post VALUES ('test1', 'Test Post 1');
        INSERT OR IGNORE INTO fact_post VALUES ('test2', 'Test Post 2');

        CREATE TABLE IF NOT EXISTS fact_comment (
            comment_id TEXT PRIMARY KEY,
            body TEXT
        );
    """)
    conn.commit()
    conn.close()


class BackupTests(unittest.TestCase):

    def test_backup_creates_artifact_and_manifest(self) -> None:
        """backup_db creates artifact file and manifest JSON in backup_dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            backup_dir = os.path.join(tmpdir, "backups")
            _make_test_db(db_path)

            manifest = backup_db(db_path, backup_dir)
            self.assertIsInstance(manifest, BackupManifest)
            self.assertTrue(manifest.backup_id)
            self.assertEqual(manifest.quick_check, "ok")
            self.assertIn("ok", manifest.foreign_key_check)
            self.assertGreater(manifest.size_bytes, 0)
            self.assertEqual(len(manifest.sha256), 64)  # SHA-256 hex

            # Artifact file should exist
            artifact_path = Path(backup_dir) / manifest.artifact
            self.assertTrue(artifact_path.exists())

            # Manifest JSON should exist and parse
            manifest_files = list(Path(backup_dir).glob("*.manifest.json"))
            self.assertEqual(len(manifest_files), 1)
            saved = BackupManifest.from_file(manifest_files[0])
            self.assertEqual(saved.backup_id, manifest.backup_id)

    def test_backup_dry_run_no_files_created(self) -> None:
        """backup_db --dry-run does not create any files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            backup_dir = os.path.join(tmpdir, "backups")
            _make_test_db(db_path)

            manifest = backup_db(db_path, backup_dir, dry_run=True)
            self.assertIn("[dry-run]", manifest.artifact)
            self.assertIn("[dry-run]", manifest.sha256)
            # No actual backup directory should be created in dry_run
            # (backup_dir.mkdir is skipped in dry_run)
            artifact_path = Path(backup_dir) / manifest.artifact.replace("[dry-run] ", "")
            self.assertFalse(artifact_path.exists())

    def test_backup_counts_populated(self) -> None:
        """Backup manifest includes row counts from key tables."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            backup_dir = os.path.join(tmpdir, "backups")
            _make_test_db(db_path)

            manifest = backup_db(db_path, backup_dir)
            self.assertIn("fact_post", manifest.counts)
            self.assertEqual(manifest.counts["fact_post"], 2)

    def test_backup_missing_db_raises(self) -> None:
        """backup_db raises FileNotFoundError for non-existent database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(FileNotFoundError):
                backup_db(
                    os.path.join(tmpdir, "nonexistent.db"),
                    os.path.join(tmpdir, "backups"),
                )

    def test_restore_drill_pass(self) -> None:
        """restore_drill returns pass with quick_check=ok for valid backup."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            backup_dir = os.path.join(tmpdir, "backups")
            _make_test_db(db_path)

            # First create a backup
            backup_db(db_path, backup_dir)

            # Now run restore drill
            result = restore_drill(backup_dir)
            self.assertEqual(result["drill_result"], "pass")
            self.assertEqual(result["quick_check"], "ok")
            self.assertTrue(result["sha256_verified"])
            self.assertTrue(result["live_db_untouched"])
            self.assertIn("fact_post", result["counts"])
            self.assertEqual(result["counts"]["fact_post"], 2)

    def test_restore_drill_no_live_db_modified(self) -> None:
        """restore_drill leaves the source database untouched."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            backup_dir = os.path.join(tmpdir, "backups")
            _make_test_db(db_path)

            original_mtime = os.path.getmtime(db_path)
            backup_db(db_path, backup_dir)
            restore_drill(backup_dir)

            # Source DB should not have been modified
            self.assertEqual(os.path.getmtime(db_path), original_mtime)

    def test_restore_drill_no_manifests_raises(self) -> None:
        """restore_drill raises FileNotFoundError when no backup manifests exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            empty_dir = os.path.join(tmpdir, "empty_backups")
            os.makedirs(empty_dir)
            with self.assertRaises(FileNotFoundError):
                restore_drill(empty_dir)

    def test_manifest_roundtrip(self) -> None:
        """BackupManifest saves and loads correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            backup_dir = os.path.join(tmpdir, "backups")
            _make_test_db(db_path)

            manifest = backup_db(db_path, backup_dir)
            manifest_files = list(Path(backup_dir).glob("*.manifest.json"))
            loaded = BackupManifest.from_file(manifest_files[0])
            self.assertEqual(manifest.backup_id, loaded.backup_id)
            self.assertEqual(manifest.sha256, loaded.sha256)
            self.assertEqual(manifest.counts, loaded.counts)


class CapacityGateTests(unittest.TestCase):

    def test_capacity_check_returns_result(self) -> None:
        """capacity_check returns a CapacityGate with valid fields."""
        result = capacity_check(".")
        self.assertIn(result.status, list(CapacityStatus))
        self.assertGreater(result.total_bytes, 0)
        self.assertGreater(result.free_gib, 0)
        self.assertIsInstance(result.message, str)
        self.assertIsInstance(result.should_stop_batch, bool)
        self.assertIsInstance(result.should_hard_stop, bool)

    def test_capacity_halt_threshold(self) -> None:
        """capacity_check returns HALT when free is below halt threshold."""
        # Set thresholds to 100% to force HALT
        result = capacity_check(
            ".",
            warn_pct=100.0,
            crit_pct=100.0,
            halt_pct=100.0,
            warn_gib=99999.0,
            crit_gib=99999.0,
            halt_gib=99999.0,
        )
        self.assertEqual(result.status, CapacityStatus.HALT)
        self.assertTrue(result.should_stop_batch)
        self.assertTrue(result.should_hard_stop)

    def test_capacity_ok_threshold(self) -> None:
        """capacity_check returns OK when thresholds are 0."""
        result = capacity_check(
            ".",
            warn_pct=0.0,
            crit_pct=0.0,
            halt_pct=0.0,
            warn_gib=0.0,
            crit_gib=0.0,
            halt_gib=0.0,
        )
        self.assertEqual(result.status, CapacityStatus.OK)
        self.assertFalse(result.should_stop_batch)
        self.assertFalse(result.should_hard_stop)

    def test_capacity_to_dict(self) -> None:
        """CapacityGate.to_dict() returns all required keys."""
        result = capacity_check(".")
        d = result.to_dict()
        required = {
            "status", "filesystem", "total_bytes", "used_bytes", "free_bytes",
            "free_pct", "free_gib", "message", "should_stop_batch", "should_hard_stop",
        }
        for key in required:
            self.assertIn(key, d)


class RetentionPlanTests(unittest.TestCase):

    def test_retention_plan_is_dry_run(self) -> None:
        """retention_plan_dry_run always returns dry_run=True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = retention_plan_dry_run(tmpdir, tmpdir)
            self.assertTrue(result["dry_run"])
            self.assertIn("categories", result)
            self.assertIn("total_would_archive_bytes", result)
            self.assertIn("message", result)
            self.assertIn("DRY-RUN", result["message"])

    def test_retention_plan_no_files_deleted(self) -> None:
        """retention_plan_dry_run does not delete any files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create some test files
            test_file = Path(tmpdir) / "test.jsonl"
            test_file.write_text("test", encoding="utf-8")

            result = retention_plan_dry_run(tmpdir, tmpdir)
            # File should still exist
            self.assertTrue(test_file.exists())


if __name__ == "__main__":
    unittest.main()
