from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reddit_crawler.storage import Storage


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = Storage(str(self.root / "test.db"), None)

    def tearDown(self) -> None:
        self.store.close()

    def test_foreign_keys_are_enabled_and_stubs_keep_integrity(self) -> None:
        self.assertEqual(self.store.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.store.upsert_comment(
            {"id": "c1", "name": "t1_c1", "subreddit_id": "t5_s1", "author": "alice"},
            post_id="p1",
        )
        self.assertEqual(self.store.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_partial_subreddit_does_not_erase_rich_metadata(self) -> None:
        self.store.upsert_subreddit({
            "name": "t5_s1", "display_name": "technology", "title": "Technology",
            "subscribers": 123, "lang": "en",
        })
        self.store.upsert_subreddit({"name": "t5_s1", "display_name": "technology"})
        row = self.store.conn.execute(
            "SELECT title, subscribers, lang FROM dim_subreddit WHERE subreddit_id='s1'"
        ).fetchone()
        self.assertEqual(row, ("Technology", 123, "en"))

    def test_post_media_extracts_preview_and_rejects_unsafe_url(self) -> None:
        self.store.upsert_post({
            "id": "p2", "name": "t3_p2", "preview": {"images": [{"source": {
                "url": "https://img.example/a.jpg?x=1&amp;y=2", "width": 1200, "height": 630,
            }}]}, "thumbnail": "javascript:alert(1)",
        })
        row = self.store.conn.execute(
            "SELECT image_url, thumbnail_url, width, height FROM fact_post_media WHERE post_id='p2'"
        ).fetchone()
        self.assertEqual(row, ("https://img.example/a.jpg?x=1&y=2", None, 1200, 630))


if __name__ == "__main__":
    unittest.main()
