from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from cli import build_parser
from reddit_crawler.marts import build_post_quality_mart
from reddit_crawler.storage import Storage


def _seed_two_sub_fixture(db: str | Path, now: float) -> None:
    """Sub A (lớn, baseline ~80 upvote) và Sub B (nhỏ, baseline ~3 upvote)."""
    store = Storage(str(db), None)
    for i, (sub, score, comments) in enumerate([
        # sub A: 7 bài quanh mức 60-100 upvote
        ("A", 100, 40), ("A", 95, 30), ("A", 90, 25), ("A", 80, 20),
        ("A", 70, 15), ("A", 60, 12), ("A", 50, 10),
        # sub B: 7 bài quanh mức 1-8 upvote, một bài tranh luận sôi nổi
        ("B", 8, 16), ("B", 5, 2), ("B", 4, 1), ("B", 3, 1),
        ("B", 2, 1), ("B", 2, 0), ("B", 1, 0),
    ]):
        post_id = f"p{i}"
        store.upsert_post({
            "id": post_id, "name": f"t3_{post_id}", "subreddit_id": f"t5_{sub}",
            "subreddit": f"sub{sub}", "author": "alice", "created_utc": now - 60,
            "title": post_id, "score": score, "num_comments": comments,
            "over_18": False,
        })
        store.snapshot_metrics({
            "id": post_id, "score": score, "num_comments": comments,
        })
    store.commit()
    store.close()


class QualityMartTests(unittest.TestCase):
    def test_mart_build_is_idempotent_and_within_bounds(self) -> None:
        root = Path(tempfile.mkdtemp())
        db = root / "mart.db"
        now = time.time()
        _seed_two_sub_fixture(db, now)

        first = build_post_quality_mart(db, hours=72, now=now)
        second = build_post_quality_mart(db, hours=72, now=now)
        self.assertEqual(first["posts"], 14)
        self.assertEqual(first["subreddits"], 2)
        self.assertEqual(first["posts"], second["posts"])

        store = Storage(str(db), None)
        store.conn.row_factory = sqlite3.Row
        try:
            rows = {r[0]: r for r in store.conn.execute(
                "SELECT post_id, quality_score, score_percentile, comments_percentile,"
                " upvote_ratio, score_ratio, comments_ratio, engagement_ratio"
                " FROM mart_post_quality"
            ) if r[0]}
            self.assertEqual(len(rows), 14)
            for row in rows.values():
                self.assertGreaterEqual(row["score_percentile"], 0.0)
                self.assertLessEqual(row["score_percentile"], 1.0)
                self.assertGreaterEqual(row["comments_percentile"], 0.0)
                self.assertLessEqual(row["comments_percentile"], 1.0)
                self.assertGreaterEqual(row["upvote_ratio"], 0.0)
                self.assertLessEqual(row["upvote_ratio"], 1.0)
                self.assertGreater(row["quality_score"], 0.0)
        finally:
            store.close()

        # Chạy lại không làm thay đổi điểm (idempotent)
        third = build_post_quality_mart(db, hours=72, now=now)
        self.assertEqual(second["posts"], third["posts"])

    def test_quality_formula_and_relative_heat(self) -> None:
        root = Path(tempfile.mkdtemp())
        db = root / "formula.db"
        now = time.time()
        _seed_two_sub_fixture(db, now)
        build_post_quality_mart(db, hours=72, now=now)

        store = Storage(str(db), None)
        store.conn.row_factory = sqlite3.Row
        try:
            row = store.conn.execute(
                "SELECT * FROM mart_post_quality WHERE post_id='p7'"
            ).fetchone()
        finally:
            store.close()

        # p7: sub B median score 3 / comments 1 -> ratio 8/3 và 16/1
        score_ratio = 8 / 3
        comments_ratio = 16 / 1
        relative_heat = 0.5 * (score_ratio + comments_ratio) / (0.5 * (score_ratio + comments_ratio) + 1)
        engagement = min((16 / 8) / 2.0, 1.0)
        freshness = 1.0 / (1.0 + (60 / 3600) / 12.0)
        expected = 100 * (0.45 * relative_heat + 0.25 * engagement + 0.15 * 0.75 + 0.15 * freshness)
        self.assertAlmostEqual(row["quality_score"], round(expected, 2), places=1)
        self.assertGreater(row["score_ratio"], 2.0)
        self.assertGreater(row["comments_ratio"], 10.0)
        self.assertAlmostEqual(row["engagement_ratio"], 2.0)

    def test_cli_transform_quality_is_registered(self) -> None:
        args = build_parser().parse_args(["transform-quality", "--hours", "24"])
        self.assertEqual(args.func.__name__, "cmd_transform_quality")
        self.assertEqual(args.hours, 24)


if __name__ == "__main__":
    unittest.main()
