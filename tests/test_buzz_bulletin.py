from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from reddit_crawler.analytics import latest_ai_buzz_bulletin
from reddit_crawler.llm import _buzz_clean, build_buzz_bundle, buzz_window, generate_buzz_bulletin
from reddit_crawler.storage import Storage
from web.app import app


class BuzzWindowTests(unittest.TestCase):
    def test_week_window_is_7_days_and_labelled(self) -> None:
        start, end, vi, en = buzz_window("week", now=time.time())
        self.assertAlmostEqual((end - start) / 86400, 7.0, places=6)
        self.assertRegex(vi, r"^TUẦN \d+/\d{4}$")
        self.assertRegex(en, r"^WEEK \d+/\d{4}$")

    def test_month_window_is_labelled_and_bounded(self) -> None:
        start, end, vi, en = buzz_window("month", now=time.time())
        days = (end - start) / 86400
        self.assertGreaterEqual(days, 28.0)
        self.assertLessEqual(days, 31.0)
        self.assertRegex(vi, r"^THÁNG \d+/\d{4}$")
        self.assertRegex(en, r"^[A-Z]+/\d{4}$")


class BuzzBulletinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.db = self.root / "buzz.db"
        store = Storage(str(self.db), None)
        self.week_start, self.week_end, _, _ = buzz_window("week")
        mid_week = (self.week_start + self.week_end) / 2
        posts = [
            {
                "id": "inwk", "name": "t3_inwk", "subreddit_id": "t5_ai",
                "subreddit": "LocalLLaMA", "author": "alice",
                "created_utc": mid_week, "title": "Week window hot release",
                "score": 200, "num_comments": 40, "over_18": False,
            },
            {
                "id": "inwk2", "name": "t3_inwk2", "subreddit_id": "t5_ai",
                "subreddit": "ClaudeAI", "author": "bob",
                "created_utc": mid_week - 200, "title": "Week window security thread",
                "score": 90, "num_comments": 12, "over_18": False,
            },
            {
                "id": "zero", "name": "t3_zero", "subreddit_id": "t5_ai",
                "subreddit": "technology", "author": "carol",
                "created_utc": mid_week, "title": "Zero score post",
                "score": 0, "num_comments": 1, "over_18": False,
            },
            {
                "id": "out", "name": "t3_out", "subreddit_id": "t5_ai",
                "subreddit": "LocalLLaMA", "author": "dave",
                "created_utc": time.time(), "title": "Post outside window",
                "score": 500, "num_comments": 99, "over_18": False,
            },
        ]
        for post in posts:
            store.upsert_post(post)
            store.snapshot_metrics(post)
        store.upsert_comment({
            "id": "c1", "name": "t1_c1", "subreddit_id": "t5_ai",
            "subreddit": "LocalLLaMA", "author": "reader",
            "created_utc": mid_week, "body": "Strong evidence comment",
            "score": 15,
        }, post_id="inwk")
        store.close()

    def test_bundle_only_includes_window_posts_with_score(self) -> None:
        bundle = build_buzz_bundle(self.db, self.week_start, self.week_end, limit=60)
        ids = [item["post_id"] for item in bundle]
        self.assertIn("inwk", ids)
        self.assertIn("inwk2", ids)
        self.assertNotIn("zero", ids)
        self.assertNotIn("out", ids)
        top = bundle[0]
        self.assertEqual(top["score"], 200)
        self.assertEqual(len(top["comments"]), 1)

    def test_local_generate_persists_and_latest_reads_it(self) -> None:
        result = generate_buzz_bulletin(self.db, "week", provider="local")
        self.assertEqual(result["provider"], "local")
        self.assertGreaterEqual(result["source_count"], 2)
        text = result["full_bulletin_text"]
        self.assertIn("★ BẢN TIN CÔNG NGHỆ", text)
        self.assertIn("TUẦN", text)
        self.assertIn("🔹", text)
        self.assertIn("Xem nội dung chi tiết", text)
        self.assertTrue(result["stories"])
        saved = latest_ai_buzz_bulletin(self.db, "week")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["bulletin_id"], result["bulletin_id"])
        self.assertEqual(saved["payload"]["stories"], result["stories"])

    def test_month_generate_uses_month_window(self) -> None:
        result = generate_buzz_bulletin(self.db, "month", provider="local")
        self.assertIn("THÁNG", result["full_bulletin_text"])

    def test_invalid_period_raises(self) -> None:
        with self.assertRaises(ValueError):
            generate_buzz_bulletin(self.db, "year", provider="local")

    def test_buzz_clean_truncates_long_headlines(self) -> None:
        self.assertEqual(_buzz_clean("  a  b "), "a b")
        long = "x" * 300
        cleaned = _buzz_clean(long)
        self.assertEqual(len(cleaned), 160)
        self.assertTrue(cleaned.endswith("…"))


class BuzzWebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.db = self.root / "web.db"
        Storage(str(self.db), None).close()
        self.client = TestClient(app)
        self.db_patch = patch("web.app.DB_PATH", str(self.db))
        self.db_patch.start()

    def tearDown(self) -> None:
        self.db_patch.stop()

    def test_buzz_falls_back_live_when_no_cached_bulletin(self) -> None:
        response = self.client.get("/api/v1/buzz?period=month")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["cached"])
        self.assertEqual(data["period"], "month")

    def test_buzz_serves_cached_bulletin_when_available(self) -> None:
        store = Storage(str(self.db), None)
        month_start, month_end, _, _ = buzz_window("month")
        store.upsert_post({
            "id": "m1", "name": "t3_m1", "subreddit_id": "t5_ai",
            "subreddit": "LocalLLaMA", "author": "alice",
            "created_utc": (month_start + month_end) / 2,
            "title": "Month window flagship model launch",
            "score": 300, "num_comments": 60, "over_18": False,
        })
        store.snapshot_metrics({
            "id": "m1", "name": "t3_m1", "subreddit_id": "t5_ai",
            "subreddit": "LocalLLaMA", "author": "alice",
            "created_utc": (month_start + month_end) / 2,
            "title": "Month window flagship model launch",
            "score": 300, "num_comments": 60, "over_18": False,
        })
        store.close()
        generate_buzz_bulletin(self.db, "month", provider="local")
        response = self.client.get("/api/v1/buzz?period=month")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["cached"])
        self.assertIn("THÁNG", data["title"])
        self.assertIn("Xem nội dung chi tiết", data["full_bulletin_text"])
        self.assertIn("provider", data)

    def test_buzz_rejects_unknown_period(self) -> None:
        response = self.client.get("/api/v1/buzz?period=year")
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
