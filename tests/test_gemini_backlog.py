from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from jobs.gemini_backlog import LEDGER_KIND, run_gemini_backlog_batch
from reddit_crawler.storage import Storage


def _post(post_id: str, *, score: int = 1, nsfw: bool = False) -> dict:
    return {
        "id": post_id,
        "name": f"t3_{post_id}",
        "subreddit_id": "t5_tech",
        "subreddit": "technology",
        "author": "alice",
        "created_utc": time.time() - score,
        "title": f"Post {post_id}",
        "selftext": "Evidence in the discussion.",
        "url": f"https://www.reddit.com/r/technology/comments/{post_id}/post/",
        "permalink": f"/r/technology/comments/{post_id}/post/",
        "is_self": True,
        "over_18": nsfw,
        "score": score,
        "num_comments": 1,
    }


def _seed_post(
    store: Storage,
    post_id: str,
    *,
    score: int = 1,
    nsfw: bool = False,
    comment_body: str | None = "Useful evidence",
) -> None:
    post = _post(post_id, score=score, nsfw=nsfw)
    store.upsert_post(post)
    store.snapshot_metrics(post)
    if comment_body is not None:
        store.upsert_comment({
            "id": f"c-{post_id}",
            "name": f"t1_c-{post_id}",
            "author": "bob",
            "body": comment_body,
            "score": score,
            "created_utc": time.time(),
            "parent_id": f"t3_{post_id}",
            "_depth": 0,
        }, post_id=post_id, subreddit_id="tech")


def _persist_analysis(db_path: str, post_id: str, provider: str) -> None:
    store = Storage(db_path, None)
    try:
        store.upsert_ai_post_analysis_v2({
            "post_id": post_id,
            "provider": provider,
            "model": "test-model" if provider == "gemini" else None,
            "status": "success",
            "payload_json": json.dumps({"topic": post_id}),
            "comment_count": 1,
            "input_tokens": 1,
            "output_tokens": 1,
            "generated_at": time.time(),
            "error": None,
        })
        store.commit()
    finally:
        store.close()


class GeminiBacklogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="gemini-backlog-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db = self.root / "test.db"
        self.reports = self.root / "reports" / "operations"

    def _successful_generator(self, calls: list[tuple[str, str, int]]):
        def generate(db_path: str, post_id: str, *, provider: str, comment_limit: int):
            calls.append((post_id, provider, comment_limit))
            self.assertEqual(provider, "gemini")
            _persist_analysis(db_path, post_id, "gemini")
            return {"post_id": post_id, "provider": "gemini", "model": "test-model"}
        return generate

    def test_drains_candidates_beyond_old_top_200_selection(self) -> None:
        store = Storage(str(self.db), None)
        for index in range(205):
            _seed_post(store, f"p{index:03d}", score=1000 - index)
        store.commit()
        store.close()

        calls: list[tuple[str, str, int]] = []
        with patch(
            "jobs.gemini_backlog.generate_post_analysis_v2",
            side_effect=self._successful_generator(calls),
        ):
            for _ in range(6):
                result = run_gemini_backlog_batch(
                    str(self.db), limit=50, output_dir=self.reports,
                )
                if result["queue"]["remaining"] == 0:
                    break

        self.assertEqual(len(calls), 205)
        self.assertIn("p204", {call[0] for call in calls})
        self.assertEqual(result["queue"]["completed"], 205)
        self.assertEqual(result["queue"]["remaining"], 0)

    def test_gemini_only_and_skips_done_nsfw_and_posts_without_usable_comments(self) -> None:
        store = Storage(str(self.db), None)
        _seed_post(store, "eligible", score=50)
        _seed_post(store, "local-is-not-done", score=40)
        _seed_post(store, "done", score=30)
        _seed_post(store, "nsfw", score=20, nsfw=True)
        _seed_post(store, "no-comment", score=10, comment_body=None)
        _seed_post(store, "removed-comment", score=5, comment_body="[removed]")
        store.commit()
        store.close()
        _persist_analysis(str(self.db), "local-is-not-done", "local")
        _persist_analysis(str(self.db), "done", "gemini")

        calls: list[tuple[str, str, int]] = []
        with patch(
            "jobs.gemini_backlog.generate_post_analysis_v2",
            side_effect=self._successful_generator(calls),
        ):
            result = run_gemini_backlog_batch(
                str(self.db), limit=10, comment_limit=77, output_dir=self.reports,
            )

        self.assertEqual({call[0] for call in calls}, {"eligible", "local-is-not-done"})
        self.assertTrue(all(call[1:] == ("gemini", 77) for call in calls))
        self.assertEqual(result["attempted"], 2)
        self.assertEqual(result["queue"]["eligible"], 3)
        self.assertEqual(result["queue"]["completed"], 3)

    def test_failure_backoff_blocking_and_new_work_progress_without_overwriting_local(self) -> None:
        store = Storage(str(self.db), None)
        _seed_post(store, "fails", score=100, comment_body="SECRET COMMENT BODY")
        _seed_post(store, "later", score=10)
        store.commit()
        store.close()
        _persist_analysis(str(self.db), "fails", "local")

        calls: list[str] = []

        def generate(db_path: str, post_id: str, *, provider: str, comment_limit: int):
            calls.append(post_id)
            self.assertEqual(provider, "gemini")
            if post_id == "fails":
                raise RuntimeError("SECRET COMMENT BODY sk-test-secret rate limit 429")
            _persist_analysis(db_path, post_id, "gemini")
            return {"post_id": post_id, "provider": "gemini"}

        with patch("jobs.gemini_backlog.generate_post_analysis_v2", side_effect=generate):
            first = run_gemini_backlog_batch(
                str(self.db), limit=1, retry_after_hours=6, max_attempts=2,
                output_dir=self.reports,
            )
            second = run_gemini_backlog_batch(
                str(self.db), limit=1, retry_after_hours=6, max_attempts=2,
                output_dir=self.reports,
            )
            waiting = run_gemini_backlog_batch(
                str(self.db), limit=1, retry_after_hours=6, max_attempts=2,
                output_dir=self.reports,
            )
            terminal = run_gemini_backlog_batch(
                str(self.db), limit=1, retry_after_hours=0, max_attempts=2,
                output_dir=self.reports,
            )

        self.assertEqual(first["results"][0]["status"], "error")
        self.assertEqual(second["results"][0]["post_id"], "later")
        self.assertEqual(waiting["attempted"], 0)
        self.assertEqual(terminal["results"][0]["status"], "blocked")
        self.assertEqual(calls, ["fails", "later", "fails"])

        conn = sqlite3.connect(f"file:{self.db.resolve()}?mode=ro", uri=True)
        try:
            analysis = conn.execute(
                "SELECT provider, status FROM ai_post_analysis_v2 WHERE post_id='fails'"
            ).fetchone()
            ledger = conn.execute(
                "SELECT status, error FROM enrichment_state WHERE post_id='fails' AND kind=?",
                (LEDGER_KIND,),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(tuple(analysis), ("local", "success"))
        self.assertEqual(ledger[0], "blocked")
        self.assertEqual(json.loads(ledger[1])["attempt_count"], 2)

        report_text = Path(terminal["report_json_path"]).read_text(encoding="utf-8")
        self.assertNotIn("SECRET COMMENT BODY", report_text)
        self.assertNotIn("sk-test-secret", report_text)
        self.assertEqual(terminal["queue"]["blocked"], 1)
        self.assertEqual(terminal["manual_review"][0]["post_id"], "fails")

    def test_stale_running_is_retryable(self) -> None:
        store = Storage(str(self.db), None)
        _seed_post(store, "stale", score=1)
        store.set_enrichment_state(
            "stale", LEDGER_KIND, "running",
            json.dumps({"attempt_count": 1, "provider": "gemini"}),
        )
        store.conn.execute(
            "UPDATE enrichment_state SET attempted_at=? WHERE post_id='stale' AND kind=?",
            (time.time() - 7200, LEDGER_KIND),
        )
        store.commit()
        store.close()

        calls: list[tuple[str, str, int]] = []
        with patch(
            "jobs.gemini_backlog.generate_post_analysis_v2",
            side_effect=self._successful_generator(calls),
        ):
            result = run_gemini_backlog_batch(
                str(self.db), limit=1, retry_after_hours=1, output_dir=self.reports,
            )

        self.assertEqual(result["attempted"], 1)
        self.assertEqual(result["results"][0]["attempt_count"], 2)

    def test_hard_attempt_limit(self) -> None:
        store = Storage(str(self.db), None)
        for index in range(10):
            _seed_post(store, f"p{index}", score=100 - index)
        store.commit()
        store.close()

        calls: list[tuple[str, str, int]] = []
        with patch(
            "jobs.gemini_backlog.generate_post_analysis_v2",
            side_effect=self._successful_generator(calls),
        ):
            result = run_gemini_backlog_batch(
                str(self.db), limit=3, output_dir=self.reports,
            )

        self.assertEqual(len(calls), 3)
        self.assertEqual(result["attempted"], 3)
        self.assertEqual(result["queue"]["remaining"], 7)
        self.assertTrue(Path(result["report_json_path"]).is_file())
        self.assertTrue(Path(result["report_markdown_path"]).is_file())

if __name__ == "__main__":
    unittest.main()
