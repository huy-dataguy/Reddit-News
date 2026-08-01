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

    def test_ai_social_roundup_upsert(self) -> None:
        self.store.upsert_ai_social_roundup({
            "cluster_id": "clu-1-00-ai_ml", "hour_start": 1000.0,
            "source_post_ids": '["p1"]', "total_score": 100, "total_comments": 10,
            "n_posts": 1, "topic_vi": "Chủ đề A", "domain_id": "ai_ml",
            "provider": "local", "status": "success",
            "title": "Bài cũ", "full_post_text": "Text cũ", "generated_at": 100.0,
        })
        self.store.upsert_ai_social_roundup({
            "cluster_id": "clu-1-00-ai_ml", "hour_start": 1000.0,
            "source_post_ids": '["p1", "p2"]', "total_score": 250, "total_comments": 30,
            "n_posts": 2, "topic_vi": "Chủ đề A cập nhật", "domain_id": "ai_ml",
            "provider": "gemini", "model": "g-test", "status": "success",
            "title": "Bài mới", "full_post_text": "Text mới", "generated_at": 200.0,
        })
        row = self.store.conn.execute(
            "SELECT cluster_id, n_posts, total_score, title, provider, model "
            "FROM ai_social_roundup WHERE cluster_id='clu-1-00-ai_ml'"
        ).fetchone()
        self.assertEqual(row, ("clu-1-00-ai_ml", 2, 250, "Bài mới", "gemini", "g-test"))
        self.assertEqual(
            self.store.conn.execute(
                "SELECT COUNT(*) FROM ai_social_roundup"
            ).fetchone()[0],
            1,
        )
        with self.assertRaises(ValueError):
            self.store.upsert_ai_social_roundup({"total_score": 1})


if __name__ == "__main__":
    unittest.main()
