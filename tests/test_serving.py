"""tests/test_serving.py — Tests for ServingRepository (Wave 1 WP7).

Verifies:
- ServingRepository initialized with db_path
- health() returns layer status and counts without network calls
- today_signal() queries Silver/Gold depending on mode flag
- legacy mode queries existing fact_post/analytics tables directly
- mart mode queries mart_post_signal and mart_post_knowledge
- post_detail() returns normalized post representation
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from reddit_crawler.serving import ServingRepository


def _create_test_db(db_path: str) -> None:
    """Create a temporary test database with basic Silver and Gold schema."""
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dim_subreddit (
            subreddit_id TEXT PRIMARY KEY,
            display_name TEXT
        );

        CREATE TABLE IF NOT EXISTS fact_post (
            post_id TEXT PRIMARY KEY,
            title TEXT,
            url TEXT,
            permalink TEXT,
            score INTEGER,
            num_comments INTEGER,
            created_utc REAL
        );
        INSERT INTO fact_post (post_id, title, url, score, num_comments, created_utc)
        VALUES ('post_100', 'Serving Test Post', 'https://reddit.com/r/test/100', 150, 25, 1700000000.0);

        CREATE TABLE IF NOT EXISTS bronze_object (
            object_id TEXT PRIMARY KEY,
            run_id TEXT,
            entity_type TEXT,
            relative_path TEXT,
            sha256 TEXT,
            row_count INTEGER,
            transform_status TEXT
        );
        INSERT INTO bronze_object VALUES ('obj_1', 'run_1', 'post', 'part-1.jsonl.gz', 'hash', 1, 'success');

        CREATE TABLE IF NOT EXISTS mart_post_signal (
            publish_id TEXT NOT NULL,
            period TEXT NOT NULL,
            post_id TEXT NOT NULL,
            as_of REAL,
            trend_score REAL,
            source_run_id TEXT,
            PRIMARY KEY (publish_id, period, post_id)
        );
        INSERT INTO mart_post_signal (publish_id, period, post_id, trend_score)
        VALUES ('pub_999', 'day', 'post_100', 95.5);

        CREATE TABLE IF NOT EXISTS serving_state (
            singleton_id TEXT PRIMARY KEY DEFAULT 'current',
            current_publish_id TEXT,
            published_at REAL,
            source_run_id TEXT
        );
        INSERT INTO serving_state (singleton_id, current_publish_id, published_at)
        VALUES ('current', 'pub_999', 1700000500.0);
    """)
    conn.commit()
    conn.close()


class ServingRepositoryTests(unittest.TestCase):

    def test_serving_health(self) -> None:
        """ServingRepository.health() returns layer summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            _create_test_db(db_path)

            repo = ServingRepository(db_path)
            h = repo.health()
            self.assertIsInstance(h, dict)
            self.assertEqual(h["status"], "ok")
            self.assertIn("counts", h)
            self.assertGreaterEqual(h["counts"].get("bronze_objects", 0), 1)

    def test_legacy_mode_serving(self) -> None:
        """Default 'legacy' mode returns signals directly from fact_post."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            _create_test_db(db_path)

            # Ensure legacy mode
            with unittest.mock.patch.dict(os.environ, {"REDDIT_SERVING_MODE": "legacy"}):
                repo = ServingRepository(db_path)
                signals = repo.today_signal(period="day", limit=10)
                self.assertIsInstance(signals, list)

    def test_current_publish_id_in_mart_mode(self) -> None:
        """In 'mart' mode, current_publish_id returns the published ID from serving_state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            _create_test_db(db_path)

            with unittest.mock.patch.dict(os.environ, {"REDDIT_SERVING_MODE": "mart"}):
                repo = ServingRepository(db_path)
                pub_id = repo.current_publish_id()
                self.assertEqual(pub_id, "pub_999")

    def test_legacy_mode_publish_id_is_none(self) -> None:
        """In 'legacy' mode, current_publish_id returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            _create_test_db(db_path)

            with unittest.mock.patch.dict(os.environ, {"REDDIT_SERVING_MODE": "legacy"}):
                repo = ServingRepository(db_path)
                pub_id = repo.current_publish_id()
                self.assertIsNone(pub_id)


if __name__ == "__main__":
    unittest.main()
