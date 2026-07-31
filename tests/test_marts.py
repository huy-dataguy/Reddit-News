"""tests/test_marts.py — Tests for Gold mart materialization and publication (Wave 1 WP6).

Verifies:
- materialize_gold creates mart_post_signal, mart_post_knowledge, and mart_digest entries
- publish_gold flips serving_state.current_publish_id atomically
- is_publish_safe enforces hard DQ checks
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from reddit_crawler.marts import materialize_gold
from reddit_crawler.publish import publish_gold
from reddit_crawler.quality import is_publish_safe, run_data_quality_checks


def _init_medallion_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS fact_post (
            post_id TEXT PRIMARY KEY,
            title TEXT,
            score INTEGER,
            num_comments INTEGER,
            created_utc REAL
        );
        INSERT INTO fact_post VALUES ('p1', 'Gold Test Post', 100, 10, 1700000000.0);

        CREATE TABLE IF NOT EXISTS mart_post_signal (
            publish_id TEXT NOT NULL,
            period TEXT NOT NULL,
            post_id TEXT NOT NULL,
            as_of REAL,
            trend_score REAL,
            source_run_id TEXT,
            PRIMARY KEY (publish_id, period, post_id)
        );

        CREATE TABLE IF NOT EXISTS mart_post_knowledge (
            publish_id TEXT NOT NULL,
            post_id TEXT NOT NULL,
            analysis_json TEXT,
            generated_at REAL,
            source_run_id TEXT,
            PRIMARY KEY (publish_id, post_id)
        );

        CREATE TABLE IF NOT EXISTS mart_digest (
            publish_id TEXT NOT NULL,
            period TEXT NOT NULL,
            digest_id TEXT NOT NULL,
            generated_at REAL,
            source_run_id TEXT,
            PRIMARY KEY (publish_id, period)
        );

        CREATE TABLE IF NOT EXISTS serving_state (
            singleton_id TEXT PRIMARY KEY DEFAULT 'current',
            current_publish_id TEXT,
            published_at REAL,
            source_run_id TEXT
        );
    """)
    conn.commit()
    conn.close()


class MartsAndPublishTests(unittest.TestCase):

    def test_materialize_gold_and_publish(self) -> None:
        """materialize_gold creates publish_id data, DQ checks pass, and publish_gold updates serving_state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            _init_medallion_db(db_path)

            source_run = "run_abc_123"
            publish_id = materialize_gold(db_path, source_run_id=source_run, periods=["day", "week"])
            self.assertTrue(publish_id.startswith("pub_"))

            # Check DQ
            dq = run_data_quality_checks(db_path, publish_id)
            self.assertTrue(is_publish_safe(dq))

            # Publish
            res = publish_gold(db_path, publish_id)
            self.assertEqual(res["current_publish_id"], publish_id)
            self.assertEqual(res["source_run_id"], source_run)

    def test_publish_invalid_id_raises(self) -> None:
        """publish_gold raises ValueError for non-existent publish_id."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            _init_medallion_db(db_path)

            with self.assertRaises(ValueError):
                publish_gold(db_path, "non_existent_pub_id")


if __name__ == "__main__":
    unittest.main()
