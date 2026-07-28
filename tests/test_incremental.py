from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobs.incremental import run_incremental
from reddit_crawler.storage import Storage


def make_post(number: int) -> dict:
    return {
        "id": f"p{number}", "name": f"t3_p{number}", "subreddit_id": "t5_s1",
        "subreddit": "technology", "author": "alice", "created_utc": 100 + number,
        "title": f"Post {number}", "score": number, "ups": number,
        "upvote_ratio": 1.0, "num_comments": 0,
    }


class IncrementalTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.store = Storage(str(root / "test.db"), None)
        self.posts = [make_post(i) for i in range(5, 0, -1)]

    def tearDown(self) -> None:
        self.store.close()

    def listing(self, _client, _sub, **kwargs):
        posts = self.posts
        start_after = kwargs.get("start_after")
        if start_after:
            index = next(i for i, post in enumerate(posts) if post["name"] == start_after)
            posts = posts[index + 1:]
        limit = kwargs.get("max_items")
        return iter(posts[:limit] if limit else posts)

    def test_limited_bootstrap_resumes_without_gaps(self) -> None:
        common = (
            patch("jobs.incremental.crawl.fetch_subreddit", return_value={
                "id": "s1", "name": "t5_s1", "display_name": "technology",
            }),
            patch("jobs.incremental.crawl.info_by_ids", return_value=iter(())),
            patch("jobs.incremental.crawl.iter_listing", side_effect=self.listing),
        )
        with common[0], common[1], common[2]:
            for _ in range(3):
                run_incremental(object(), self.store, ["technology"], max_per_sub=2,
                                refresh_hours=0)
            self.posts.insert(0, make_post(6))
            run_incremental(object(), self.store, ["technology"], max_per_sub=2,
                            refresh_hours=0)
        ids = {row[0] for row in self.store.conn.execute("SELECT post_id FROM fact_post")}
        self.assertEqual(ids, {f"p{i}" for i in range(1, 7)})
        self.assertIsNone(self.store.get_state("bootstrap:sub:technology:new"))
        self.assertEqual(self.store.get_state("sub:technology:new")["last_utc"], 106)


if __name__ == "__main__":
    unittest.main()
