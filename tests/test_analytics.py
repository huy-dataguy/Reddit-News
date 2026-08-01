from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from reddit_crawler.analytics import classify_domain, post_detail, trending_posts
from reddit_crawler.marts import build_post_quality_mart
from reddit_crawler.storage import Storage


class AnalyticsTests(unittest.TestCase):
    def test_domain_classification_uses_subreddit_and_topic(self) -> None:
        self.assertEqual(classify_domain({"subreddit": "codex", "title": "Word workflow"}), "ai_ml")
        self.assertEqual(
            classify_domain({"subreddit": "technology", "title": "Critical CVE security patch"}),
            "security",
        )

    def test_feed_and_detail_prefer_llm_over_local_and_keep_v1_compatibility(self) -> None:
        root = Path(tempfile.mkdtemp())
        db = root / "test.db"
        store = Storage(str(db), None)
        now = time.time()
        for post_id in ("both", "v1"):
            post = {
                "id": post_id, "name": f"t3_{post_id}", "subreddit_id": "t5_s1",
                "subreddit": "programming", "author": "alice", "created_utc": now - 60,
                "title": post_id, "score": 10, "num_comments": 2, "over_18": False,
            }
            store.upsert_post(post)
            store.snapshot_metrics(post)
        store.upsert_ai_post_analysis({
            "post_id": "both", "provider": "openai", "model": "old",
            "status": "success", "payload_json": json.dumps({
                "topic": "V1 LLM wins", "domain": "software_dev",
            }), "generated_at": now - 10,
        })
        store.upsert_ai_post_analysis_v2({
            "post_id": "both", "provider": "local", "model": None,
            "status": "success", "payload_json": json.dumps({
                "topic": "V2 local extract", "domain": "devtools",
            }), "generated_at": now,
        })
        store.upsert_ai_post_analysis({
            "post_id": "v1", "provider": "gemini", "model": "legacy",
            "status": "success", "payload_json": json.dumps({
                "topic": "V1 fallback", "domain": "cybersecurity",
            }), "generated_at": now,
        })
        store.upsert_ai_post_analysis_v2({
            "post_id": "v1", "provider": "gemini", "model": "broken-v2",
            "status": "success", "payload_json": "{}", "generated_at": now + 1,
        })
        store.commit()
        store.close()

        items = {item["post_id"]: item for item in trending_posts(db, period="day", now=now)}
        self.assertEqual(items["both"]["analysis"]["topic"], "V1 LLM wins")
        self.assertEqual(items["both"]["analysis_provider"], "openai")
        self.assertEqual(items["both"]["analysis_version"], 1)
        self.assertEqual(items["v1"]["analysis"]["topic"], "V1 fallback")
        self.assertEqual(items["v1"]["domain_id"], "security")
        self.assertEqual(items["v1"]["analysis_version"], 1)

        detail = post_detail(db, "both")
        self.assertEqual(detail["analysis"]["topic"], "V1 LLM wins")
        self.assertEqual(detail["analysis_provider"], "openai")
        self.assertEqual(detail["analysis_version"], 1)

    def test_velocity_moves_a_post_up(self) -> None:
        root = Path(tempfile.mkdtemp())
        db = root / "test.db"
        store = Storage(str(db), None)
        now = time.time()
        for post_id in ("fast", "slow"):
            store.upsert_post({
                "id": post_id, "name": f"t3_{post_id}", "subreddit_id": "t5_s1",
                "subreddit": "technology", "author": "alice", "created_utc": now - 1800,
                "title": post_id, "score": 10, "num_comments": 2, "over_18": False,
            })
        store.conn.executemany(
            "INSERT INTO fact_post_metrics(post_id,observed_at,score,num_comments) VALUES (?,?,?,?)",
            [
                ("fast", now - 600, 10, 2), ("fast", now, 110, 22),
                ("slow", now - 600, 10, 2), ("slow", now, 11, 2),
            ],
        )
        store.commit()
        store.close()
        ranked = trending_posts(db, period="3h", now=now)
        self.assertEqual([item["post_id"] for item in ranked], ["fast", "slow"])
        self.assertGreater(ranked[0]["score_velocity"], ranked[1]["score_velocity"])

    def test_period_never_falls_back_to_stale_posts(self) -> None:
        root = Path(tempfile.mkdtemp())
        db = root / "test.db"
        now = time.time()
        store = Storage(str(db), None)
        store.upsert_post({
            "id": "old", "name": "t3_old", "subreddit_id": "t5_s1",
            "subreddit": "technology", "author": "alice",
            "created_utc": now - 10 * 86400, "title": "Old signal",
            "score": 100, "num_comments": 20, "over_18": False,
        })
        store.commit()
        store.close()
        self.assertEqual(trending_posts(db, period="day", now=now), [])

    def test_local_extract_does_not_receive_llm_value_bonus(self) -> None:
        root = Path(tempfile.mkdtemp())
        db = root / "test.db"
        now = time.time()
        store = Storage(str(db), None)
        for post_id in ("raw", "local"):
            post = {
                "id": post_id, "name": f"t3_{post_id}", "subreddit_id": "t5_s1",
                "subreddit": "technology", "author": "alice", "created_utc": now - 60,
                "title": "Same signal", "score": 10, "num_comments": 2,
                "over_18": False,
            }
            store.upsert_post(post)
            store.snapshot_metrics(post)
        store.upsert_ai_post_analysis_v2({
            "post_id": "local", "provider": "local", "status": "success",
            "payload_json": json.dumps({"topic": "Extract only", "domain": "other"}),
            "generated_at": now,
        })
        store.commit()
        store.close()
        by_id = {item["post_id"]: item for item in trending_posts(db, period="day", now=now)}
        self.assertEqual(
            by_id["local"]["composite_value_score"],
            by_id["raw"]["composite_value_score"],
        )


    def test_relative_heat_ranks_small_sub_winner_above_big_sub_average(self) -> None:
        root = Path(tempfile.mkdtemp())
        db = root / "rel.db"
        now = time.time()
        store = Storage(str(db), None)
        fixture = [
            # sub A (lớn): median ~80 upvote
            ("A", 100, 40), ("A", 95, 30), ("A", 90, 25), ("A", 80, 20),
            ("A", 70, 15), ("A", 60, 12), ("A", 50, 10),
            # sub B (nhỏ): median 3 upvote; b1 (8 upvote, 16 comment) nổi trội tương đối
            ("B", 8, 16), ("B", 5, 2), ("B", 4, 1), ("B", 3, 1),
            ("B", 2, 1), ("B", 2, 0), ("B", 1, 0),
        ]
        for i, (sub, score, comments) in enumerate(fixture):
            post_id = f"p{i}"
            store.upsert_post({
                "id": post_id, "name": f"t3_{post_id}", "subreddit_id": f"t5_{sub}",
                "subreddit": f"sub{sub}", "author": "alice", "created_utc": now - 60,
                "title": post_id, "score": score, "num_comments": comments,
                "over_18": False,
            })
            store.snapshot_metrics({"id": post_id, "score": score, "num_comments": comments})
        store.commit()
        store.close()

        build_post_quality_mart(db, hours=72, now=now)
        ranked = trending_posts(db, period="day", now=now)
        by_id = {item["post_id"]: item for item in ranked}

        # p7 = bài 8 upvote của sub B phải lên đầu; trên cả bài 100 upvote của sub A
        self.assertEqual(ranked[0]["post_id"], "p7")
        self.assertGreater(
            by_id["p7"]["composite_value_score"],
            by_id["p0"]["composite_value_score"],
        )
        self.assertGreater(by_id["p7"]["quality_score"], by_id["p0"]["quality_score"])
        self.assertGreater(by_id["p7"]["score_ratio"], 2.0)
        self.assertAlmostEqual(by_id["p7"]["engagement_ratio"], 2.0)

    def test_outlier_score_keeps_high_percentile_in_mart(self) -> None:
        root = Path(tempfile.mkdtemp())
        db = root / "outlier.db"
        now = time.time()
        store = Storage(str(db), None)
        for i, (sub, score) in enumerate([("A", 500), ("A", 50), ("B", 150), ("B", 10)]):
            post_id = f"o{i}"
            store.upsert_post({
                "id": post_id, "name": f"t3_{post_id}", "subreddit_id": f"t5_{sub}",
                "subreddit": f"sub{sub}", "author": "alice", "created_utc": now - 60,
                "title": post_id, "score": score, "num_comments": 5,
                "over_18": False,
            })
            store.snapshot_metrics({"id": post_id, "score": score, "num_comments": 5})
        store.commit()
        store.close()

        build_post_quality_mart(db, hours=72, now=now)
        store = Storage(str(db), None)
        store.conn.row_factory = __import__("sqlite3").Row
        try:
            rows = {r[0]: r for r in store.conn.execute(
                "SELECT post_id, score_percentile, quality_score FROM mart_post_quality"
            )}
        finally:
            store.close()

        # o0: 500 upvote trong sub 2 bài -> percentile thô 0.5, bị guard đẩy lên >= 0.9
        self.assertGreaterEqual(rows["o0"]["score_percentile"], 0.9)
        # o2: 150 upvote không đủ ngưỡng 200 -> giữ percentile thô 0.5
        self.assertLess(rows["o2"]["score_percentile"], 0.9)


if __name__ == "__main__":
    unittest.main()
