from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from reddit_crawler.resources import (
    clean_url,
    extract_resources_from_text,
    scan_and_store_resources,
)
from reddit_crawler.storage import Storage


class ResourceExtractionTests(unittest.TestCase):
    def test_clean_url_removes_tracking_parameters(self) -> None:
        url, domain = clean_url(
            "https://www.github.com/acme/tool?utm_source=reddit&tab=readme#install"
        )
        self.assertEqual(domain, "github.com")
        self.assertEqual(url, "https://www.github.com/acme/tool?tab=readme")

    def test_scan_persists_using_current_schema_without_confidence_column(self) -> None:
        root = Path(tempfile.mkdtemp())
        db = root / "test.db"
        store = Storage(str(db), None)
        now = time.time()
        post = {
            "id": "p1", "name": "t3_p1", "subreddit_id": "t5_dev",
            "subreddit": "programming", "author": "poster", "created_utc": now,
            "title": "Useful repo", "selftext": "", "score": 3,
            "num_comments": 1, "over_18": False,
        }
        store.upsert_post(post)
        store.upsert_comment({
            "id": "c1", "name": "t1_c1", "author": "alice",
            "body": "Use https://github.com/acme/tool?utm_source=reddit for this task.",
            "score": 8, "created_utc": now, "subreddit_id": "t5_dev",
            "subreddit": "programming",
        }, post_id="p1")
        store.commit()
        schema_before = [
            row[1] for row in store.conn.execute("PRAGMA table_info(fact_extracted_resource)")
        ]
        store.close()

        result = scan_and_store_resources(db)
        self.assertEqual(result["total_extracted_resources"], 1)

        with sqlite3.connect(db) as conn:
            schema_after = [
                row[1] for row in conn.execute("PRAGMA table_info(fact_extracted_resource)")
            ]
            row = conn.execute(
                "SELECT post_id, comment_id, url, resource_type, author_name, score "
                "FROM fact_extracted_resource"
            ).fetchone()
        self.assertEqual(schema_after, schema_before)
        self.assertNotIn("confidence", schema_after)
        self.assertEqual(row, (
            "p1", "c1", "https://github.com/acme/tool", "github_repo", "alice", 8,
        ))

    def test_extractor_can_expose_nonpersisted_confidence_metadata(self) -> None:
        resources = extract_resources_from_text(
            "See https://arxiv.org/abs/1234.5678", post_id="p1",
        )
        self.assertEqual(resources[0]["confidence"], "unverified")


if __name__ == "__main__":
    unittest.main()
