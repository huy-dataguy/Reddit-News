from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from jobs.incremental import load_subs
from reddit_crawler.analytics import classify_domain
from reddit_crawler.storage import Storage
from web.app import app


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.db = self.root / "web.db"
        self.now = time.time()
        store = Storage(str(self.db), None)
        posts = [
            {
                "id": "ai", "name": "t3_ai", "subreddit_id": "t5_ai",
                "subreddit": "LocalLLaMA", "author": "alice",
                "created_utc": self.now - 300, "title": "A new LLM agent benchmark",
                "selftext": "Agent benchmark details", "score": 120,
                "num_comments": 8, "over_18": False,
                "permalink": "/r/LocalLLaMA/comments/ai/example/",
            },
            {
                "id": "sec", "name": "t3_sec", "subreddit_id": "t5_sec",
                "subreddit": "netsec", "author": "bob",
                "created_utc": self.now - 600, "title": "Critical CVE patch",
                "selftext": "Patch immediately", "score": 90,
                "num_comments": 5, "over_18": False,
            },
            {
                "id": "rust", "name": "t3_rust", "subreddit_id": "t5_rust",
                "subreddit": "rust", "author": "carol",
                "created_utc": self.now - 900, "title": "Rust CLI release",
                "selftext": "A useful developer tool", "score": 60,
                "num_comments": 3, "over_18": False,
            },
            {
                "id": "empty", "name": "t3_empty", "subreddit_id": "t5_misc",
                "subreddit": "technology", "author": "dave",
                "created_utc": self.now - 1200, "title": "Post without comments",
                "score": 2, "num_comments": 0, "over_18": False,
            },
        ]
        for post in posts:
            store.upsert_post(post)
            store.snapshot_metrics(post)

        store.upsert_comment({
            "id": "c1", "name": "t1_c1", "subreddit_id": "t5_ai",
            "subreddit": "LocalLLaMA", "author": "reader",
            "created_utc": self.now - 120, "body": "Useful evidence",
            "score": 7,
        }, post_id="ai")
        store.upsert_ai_post_analysis({
            "post_id": "ai", "provider": "gemini", "model": "gemini-old",
            "status": "success", "payload_json": json.dumps({
                "domain": classify_domain(posts[0]), "topic": "Old V1 AI summary",
                "community_consensus": "Old consensus",
            }), "comment_count": 1, "generated_at": self.now - 200,
        })
        store.upsert_ai_post_analysis_v2({
            "post_id": "ai", "provider": "local", "model": None,
            "status": "success", "payload_json": json.dumps({
                "topic": "Fresh V2 agent analysis", "author_summary": "Local extraction",
                "key_points": [], "action_items": [], "warnings": [],
            }), "comment_count": 1, "generated_at": self.now - 100,
        })
        store.upsert_ai_post_analysis({
            "post_id": "sec", "provider": "local", "model": None,
            "status": "success", "payload_json": json.dumps({
                "domain": classify_domain(posts[1]), "topic": "Security patch analysis",
                "community_consensus": "Patch now",
            }), "comment_count": 0, "generated_at": self.now - 150,
        })
        store.upsert_ai_post_analysis_v2({
            "post_id": "sec", "provider": "openai", "model": "broken",
            "status": "success", "payload_json": "{}", "comment_count": 0,
            "generated_at": self.now - 20,
        })
        store.upsert_ai_post_analysis_v2({
            "post_id": "rust", "provider": "openai", "model": "gpt-test",
            "status": "success", "payload_json": json.dumps({
                "topic": "Rust tooling analysis", "author_summary": "Developer tooling",
                "key_points": [], "action_items": ["Evaluate the CLI"], "warnings": [],
            }), "comment_count": 0, "generated_at": self.now - 50,
        })
        store.set_enrichment_state("ai", "comments", "success")
        store.commit()
        store.close()

        self.client = TestClient(app)
        self.db_patch = patch("web.app.DB_PATH", str(self.db))
        self.db_patch.start()

        self.subs_file = self.root / "subs.txt"
        self.subs_patch = patch("web.app.SUBS_FILE", self.subs_file)
        self.subs_patch.start()

    def tearDown(self) -> None:
        self.subs_patch.stop()
        self.db_patch.stop()
        self.client.close()

    def _write_subs(self, content: str) -> None:
        self.subs_file.write_text(content, encoding="utf-8")

    def test_unified_feed_prioritizes_llm_and_falls_back_per_post(self) -> None:
        response = self.client.get("/api/knowledge/feed?limit=2&offset=0")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 3)
        self.assertEqual(body["count"], 2)
        self.assertTrue(body["has_more"])

        all_items = self.client.get("/api/knowledge/feed?limit=20").json()["items"]
        by_id = {item["post_id"]: item for item in all_items}
        self.assertEqual(by_id["ai"]["analysis_version"], "v1")
        self.assertEqual(by_id["ai"]["analysis"]["topic"], "Old V1 AI summary")
        self.assertEqual(by_id["ai"]["provider"], "gemini")
        self.assertEqual(by_id["ai"]["model"], "gemini-old")
        self.assertTrue(by_id["ai"]["is_ai"])
        self.assertEqual(by_id["sec"]["analysis_version"], "v1")
        self.assertEqual(by_id["sec"]["provider_label"], "Trích xuất local · chưa xác minh")
        self.assertFalse(by_id["sec"]["is_ai"])

        queried = self.client.get("/api/knowledge/feed?q=security").json()
        self.assertEqual([item["post_id"] for item in queried["items"]], ["sec"])
        domain = by_id["sec"]["domain_id"]
        filtered = self.client.get(f"/api/knowledge/feed?domain={domain}").json()
        self.assertEqual([item["post_id"] for item in filtered["items"]], ["sec"])

        subs = self.client.get("/api/knowledge/feed?sub=localllama").json()
        self.assertEqual([item["post_id"] for item in subs["items"]], ["ai"])
        self.assertEqual(subs["total"], 1)
        sub_names = [entry["name"] for entry in subs["subreddits"]]
        self.assertIn("LocalLLaMA", sub_names)
        self.assertIn("netsec", sub_names)

        alias = self.client.get("/api/knowledge/feed/v2?limit=20").json()
        self.assertEqual(
            {item["post_id"] for item in alias["items"]},
            {item["post_id"] for item in all_items},
        )

    def test_today_is_useful_and_explicitly_provisional_without_digest(self) -> None:
        response = self.client.get("/api/today?period=day&limit=3")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["provisional"])
        self.assertIsNone(body["digest"])
        self.assertEqual(body["provider"]["name"], "none")
        self.assertGreaterEqual(len(body["highlights"]), 1)
        self.assertGreaterEqual(len(body["signals"]), 1)

    def test_today_exposes_quality_fields_when_mart_is_built(self) -> None:
        from reddit_crawler.marts import build_post_quality_mart
        build_post_quality_mart(self.db, hours=72, now=self.now)
        response = self.client.get("/api/today?period=day&limit=3")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertGreaterEqual(len(body["highlights"]), 1)
        first = body["highlights"][0]
        self.assertIn("quality_score", first)
        self.assertIn("score_ratio", first)
        self.assertIn("engagement_ratio", first)
        self.assertIn("upvote_ratio", first)
        self.assertGreater(first["quality_score"], 0.0)

    def test_today_marks_a_digest_from_another_period_as_provisional(self) -> None:
        store = Storage(str(self.db), None)
        store.upsert_ai_digest({
            "digest_id": "digest-3h", "period": "3h",
            "window_start": self.now - 10800, "window_end": self.now,
            "provider": "openai", "model": "gpt-test", "status": "success",
            "title": "3h briefing", "executive_summary": "Three hour window",
            "payload_json": json.dumps({"title": "3h briefing"}),
            "source_count": 3, "generated_at": self.now,
        })
        store.commit()
        store.close()

        body = self.client.get("/api/today?period=day&limit=3").json()
        self.assertTrue(body["provisional"])
        self.assertEqual(body["digest"]["period"], "3h")
        self.assertFalse(body["digest"]["requested_period_match"])
        self.assertIn("đang hiển thị briefing 3h", body["message"])

    def test_health_reports_counts_freshness_and_local_as_degraded(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["counts"]["posts"], 4)
        self.assertEqual(body["counts"]["analyses_v2"], 3)
        self.assertEqual(body["counts"]["analyses"], 3)
        self.assertEqual(body["stages"]["collector"]["freshness"], "fresh")
        self.assertEqual(body["stages"]["analysis"]["provider"], "openai")
        self.assertEqual(body["stages"]["analysis"]["model"], "gpt-test")
        self.assertEqual(body["stages"]["digest"]["freshness"], "missing")

    def test_post_detail_is_read_only_when_comments_are_missing(self) -> None:
        before = self._count("fact_comment")
        response = self.client.get("/api/posts/empty")
        after = self._count("fact_comment")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["comments"], [])
        self.assertEqual(after, before)

    def test_user_bookmark_details_returns_full_post_items(self) -> None:
        res1 = self.client.post("/api/user/bookmarks/ai")
        self.assertEqual(res1.status_code, 200)
        self.assertTrue(res1.json()["saved"])

        res2 = self.client.post("/api/user/bookmarks/sec")
        self.assertEqual(res2.status_code, 200)
        self.assertTrue(res2.json()["saved"])

        details = self.client.get("/api/user/bookmarks/details")
        self.assertEqual(details.status_code, 200)
        body = details.json()
        self.assertEqual(body["bookmarks"], ["sec", "ai"])
        self.assertEqual(body["count"], 2)
        item_ids = [item["post_id"] for item in body["items"]]
        self.assertEqual(item_ids, ["sec", "ai"])


    def test_social_roundup_endpoint(self) -> None:
        import math

        now = time.time()
        hour_start = math.floor((now - 3 * 3600) / 3600) * 3600
        store = Storage(str(self.db), None)
        store.upsert_ai_social_roundup({
            "cluster_id": "clu-1-00-ai_ml", "hour_start": hour_start,
            "source_post_ids": '["ai", "rust"]', "total_score": 180,
            "total_comments": 11, "n_posts": 2, "topic_vi": "AI agent benchmark",
            "domain_id": "ai_ml", "provider": "local", "model": None,
            "status": "success", "title": "⚡ Roundup cũ của giờ",
            "full_post_text": "Bài tổng hợp đã lưu của cửa sổ hiện tại.",
            "generated_at": now - 60,
        })
        store.commit()
        store.close()

        response = self.client.get("/api/social/roundup?hours=3&top=3")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["cached"])
        self.assertEqual(body["count"], 1)
        item = body["items"][0]
        self.assertEqual(item["cluster_id"], "clu-1-00-ai_ml")
        self.assertEqual(item["total_score"], 180)
        self.assertEqual(item["total_comments"], 11)
        self.assertEqual(item["source_post_ids"], ["ai", "rust"])
        self.assertIn("full_post_text", item)
        self.assertEqual(item["hour_start"], hour_start)

    def test_subs_list_reads_config_file_preserving_order(self) -> None:
        self._write_subs("# Nguồn crawl:\ncodex\nr/ClaudeAI\nartificial\n\n")
        response = self.client.get("/api/subs")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 3)
        self.assertEqual(
            [sub["name"] for sub in body["subs"]],
            ["codex", "ClaudeAI", "artificial"],
        )
        self.assertTrue(body["file"].endswith("subs.txt"))

    def test_subs_update_writes_file_and_collector_reads_it_next_run(self) -> None:
        self._write_subs("# Nguồn crawl:\ncodex\nClaudeAI\n")
        response = self.client.post("/api/subs", json={
            "subs": ["r/machinelearning", "Anthropic", "codex"],
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 3)
        self.assertEqual(
            [sub["name"] for sub in body["subs"]],
            ["machinelearning", "Anthropic", "codex"],
        )

        self.assertEqual(
            load_subs(self.subs_file),
            ["machinelearning", "Anthropic", "codex"],
        )
        content = self.subs_file.read_text(encoding="utf-8")
        self.assertIn("# Nguồn crawl:\n", content)
        self.assertNotIn("r/ClaudeAI", content)

    def test_subs_update_validates_names_and_payload(self) -> None:
        self._write_subs("codex\n")
        invalid = self.client.post("/api/subs", json={"subs": ["bad name!", "ok_sub"]})
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("không hợp lệ", invalid.json()["detail"])
        self.assertEqual(load_subs(self.subs_file), ["codex"])

        no_key = self.client.post("/api/subs", json={"names": ["codex"]})
        self.assertEqual(no_key.status_code, 400)

        empty = self.client.post("/api/subs", json={"subs": []})
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["count"], 0)
        self.assertEqual(load_subs(self.subs_file), [])

    def test_subs_update_deduplicates_and_skips_blank(self) -> None:
        response = self.client.post("/api/subs", json={
            "subs": ["codex", "", "  ", "r/codex", "Codex", "Anthropic"],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [sub["name"] for sub in response.json()["subs"]],
            ["codex", "Anthropic"],
        )

    def _count(self, table: str) -> int:
        import sqlite3

        conn = sqlite3.connect(f"file:{self.db.resolve()}?mode=ro", uri=True)
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
