from __future__ import annotations

import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cli import cmd_analyze_top, cmd_enrich
from jobs.enrich import run_enrichment
from jobs.pipeline import analyze_top_posts_v2, run_pipeline
from jobs.report import _atomic_write, create_report
from reddit_crawler.storage import Storage


def make_post(post_id: str, *, score: int = 100) -> dict:
    return {
        "id": post_id,
        "name": f"t3_{post_id}",
        "subreddit_id": "t5_tech",
        "subreddit": "technology",
        "author": "alice",
        "created_utc": time.time() - 60,
        "title": f"Post {post_id}",
        "selftext": "See https://github.com/example/project for details.",
        "url": f"https://www.reddit.com/r/technology/comments/{post_id}/post/",
        "permalink": f"/r/technology/comments/{post_id}/post/",
        "is_self": True,
        "over_18": False,
        "score": score,
        "num_comments": 1,
    }


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.db = self.root / "test.db"
        store = Storage(str(self.db), None)
        for post_id in ("missing", "ready"):
            post = make_post(post_id, score=100 if post_id == "missing" else 50)
            store.upsert_post(post)
            store.snapshot_metrics(post)
        store.upsert_comment({
            "id": "existing", "name": "t1_existing", "author": "bob",
            "body": "Already stored", "score": 3,
        }, post_id="ready", subreddit_id="tech")
        store.set_enrichment_state("ready", "comments", "success")
        store.commit()
        store.close()

    def test_enrichment_fetches_only_missing_comments_and_extracts_resources(self) -> None:
        fetched_post = make_post("missing")
        fetched_comments = [{
            "id": "c1", "name": "t1_c1", "author": "carol",
            "body": "Useful docs: https://docs.python.org/3/", "score": 12,
            "created_utc": time.time(), "parent_id": "t3_missing", "_depth": 0,
        }]
        client = object()
        with (
            patch("jobs.enrich.trending_posts", return_value=[
                {"post_id": "missing", "subreddit": "technology"},
                {"post_id": "ready", "subreddit": "technology"},
            ]),
            patch(
                "jobs.enrich.crawl.fetch_post_with_comments",
                return_value=(fetched_post, fetched_comments),
            ) as fetch,
        ):
            result = run_enrichment(
                str(self.db), raw_dir=None, period="day", limit=2,
                depth=2, kind="both", client=client,
            )

        fetch.assert_called_once()
        self.assertEqual(result["comments_fetched"], 1)
        self.assertEqual(result["comment_posts_skipped"], 1)
        self.assertGreaterEqual(result["resources_extracted"], 2)

        conn = Storage(str(self.db), None)
        try:
            self.assertEqual(
                conn.conn.execute(
                    "SELECT COUNT(*) FROM fact_comment WHERE post_id='missing'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.conn.execute(
                    "SELECT status FROM enrichment_state "
                    "WHERE post_id='missing' AND kind='comments'"
                ).fetchone()[0],
                "success",
            )
            self.assertGreaterEqual(
                conn.conn.execute(
                    "SELECT COUNT(*) FROM fact_extracted_resource"
                ).fetchone()[0],
                2,
            )
        finally:
            conn.close()

    def test_enrichment_rolls_back_partial_comments_and_retries_error_state(self) -> None:
        fetched_post = make_post("missing")
        fetched_comments = [
            {"id": "c1", "name": "t1_c1", "author": "carol", "body": "first"},
            {"id": "c2", "name": "t1_c2", "author": "dave", "body": "second"},
        ]
        original_upsert = Storage.upsert_comment

        def fail_on_second(store, comment, post_id, subreddit_id=None):
            if comment.get("id") == "c2":
                raise RuntimeError("synthetic write failure")
            return original_upsert(store, comment, post_id, subreddit_id)

        with (
            patch("jobs.enrich.trending_posts", return_value=[
                {"post_id": "missing", "subreddit": "technology"},
            ]),
            patch(
                "jobs.enrich.crawl.fetch_post_with_comments",
                return_value=(fetched_post, fetched_comments),
            ),
            patch.object(Storage, "upsert_comment", fail_on_second),
        ):
            failed = run_enrichment(
                str(self.db), raw_dir=None, period="day", limit=1,
                kind="comments", client=object(), retry_after_hours=0,
            )
        self.assertEqual(failed["failed"], 1)
        store = Storage(str(self.db), None)
        try:
            self.assertEqual(store.conn.execute(
                "SELECT COUNT(*) FROM fact_comment WHERE post_id='missing'"
            ).fetchone()[0], 0)
            self.assertEqual(store.conn.execute(
                "SELECT status FROM enrichment_state "
                "WHERE post_id='missing' AND kind='comments'"
            ).fetchone()[0], "error")
        finally:
            store.close()

        with (
            patch("jobs.enrich.trending_posts", return_value=[
                {"post_id": "missing", "subreddit": "technology"},
            ]),
            patch(
                "jobs.enrich.crawl.fetch_post_with_comments",
                return_value=(fetched_post, fetched_comments),
            ) as fetch,
        ):
            retried = run_enrichment(
                str(self.db), raw_dir=None, period="day", limit=1,
                kind="comments", client=object(), retry_after_hours=0,
            )
        fetch.assert_called_once()
        self.assertEqual(retried["comment_posts_enriched"], 1)

    def test_enrichment_backlog_advances_beyond_trending_candidates(self) -> None:
        store = Storage(str(self.db), None)
        store.set_enrichment_state("missing", "comments", "empty")
        for index in range(3):
            post = make_post(f"backlog-{index}", score=10 - index)
            post["created_utc"] = time.time() - 3600 * (index + 1)
            store.upsert_post(post)
            store.snapshot_metrics(post)
        zero_comment = make_post("zero-comments")
        zero_comment["num_comments"] = 0
        store.upsert_post(zero_comment)
        nsfw = make_post("nsfw")
        nsfw["over_18"] = True
        store.upsert_post(nsfw)
        store.commit()
        store.close()

        def fetched(_client, post_id, **_kwargs):
            return make_post(post_id), [{
                "id": f"comment-{post_id}",
                "name": f"t1_comment-{post_id}",
                "author": "carol",
                "body": f"Evidence for {post_id}",
                "score": 2,
                "created_utc": time.time(),
                "parent_id": f"t3_{post_id}",
                "_depth": 0,
            }]

        with (
            patch("jobs.enrich.trending_posts") as trending,
            patch("jobs.enrich.crawl.fetch_post_with_comments", side_effect=fetched) as fetch,
        ):
            first = run_enrichment(
                str(self.db), raw_dir=None, period="day", limit=2,
                kind="comments", client=object(), backlog=True,
            )
            second = run_enrichment(
                str(self.db), raw_dir=None, period="day", limit=2,
                kind="comments", client=object(), backlog=True,
            )

        trending.assert_not_called()
        attempted_ids = [call.args[1] for call in fetch.call_args_list]
        self.assertEqual(first["comment_posts_attempted"], 2)
        self.assertEqual(second["comment_posts_attempted"], 1)
        self.assertEqual(set(attempted_ids), {
            "backlog-0", "backlog-1", "backlog-2",
        })
        self.assertNotIn("zero-comments", attempted_ids)
        self.assertNotIn("nsfw", attempted_ids)

    def test_enrichment_zero_limit_performs_no_work(self) -> None:
        with patch("jobs.enrich.trending_posts") as trending:
            result = run_enrichment(
                str(self.db), raw_dir=None, period="day", limit=0,
                kind="both", client=object(),
            )
        trending.assert_not_called()
        self.assertEqual(result["candidates"], 0)
        self.assertEqual(result["comment_posts_attempted"], 0)

    def test_analyze_top_uses_v2_and_skips_fresh_matching_provider(self) -> None:
        store = Storage(str(self.db), None)
        store.upsert_comment({
            "id": "missing-comment", "name": "t1_missing-comment", "author": "dana",
            "body": "Enough evidence to analyze", "score": 5,
        }, post_id="missing", subreddit_id="tech")
        store.upsert_ai_post_analysis_v2({
            "post_id": "missing", "provider": "gemini", "model": "test-model",
            "status": "success", "payload_json": "{}", "comment_count": 1,
            "input_tokens": 1, "output_tokens": 1, "generated_at": time.time(),
            "error": None,
        })
        store.commit()
        store.close()

        generated = Mock(return_value={"post_id": "ready", "provider": "gemini"})
        with (
            patch("jobs.pipeline.trending_posts", return_value=[
                {"post_id": "missing"}, {"post_id": "ready"},
            ]),
            patch("jobs.pipeline.generate_post_analysis_v2", generated),
        ):
            result = analyze_top_posts_v2(
                str(self.db), "day", limit=1, provider="gemini", freshness_hours=24,
            )

        generated.assert_called_once_with(
            str(self.db), "ready", provider="gemini", comment_limit=120,
        )
        self.assertEqual(result["analyzed"], 1)
        self.assertEqual(result["skipped_fresh"], 1)

    def test_analyze_top_bounds_attempts_when_every_provider_call_fails(self) -> None:
        candidates = [{"post_id": f"candidate-{index}"} for index in range(5)]
        with (
            patch("jobs.pipeline.trending_posts", return_value=candidates),
            patch("jobs.pipeline._analysis_state", return_value={"comment_count": 1}),
            patch(
                "jobs.pipeline.generate_post_analysis_v2",
                side_effect=RuntimeError("provider unavailable"),
            ) as generate,
        ):
            result = analyze_top_posts_v2(
                str(self.db), "day", limit=2, provider="gemini",
            )
        self.assertEqual(result["attempted"], 2)
        self.assertEqual(result["failed"], 2)
        self.assertEqual(generate.call_count, 2)

    def test_pipeline_runs_stages_in_evidence_first_order(self) -> None:
        calls: list[str] = []

        def stage(name: str, value: dict):
            def run(*_args, **_kwargs):
                calls.append(name)
                return value
            return run

        with (
            patch("jobs.pipeline.run_enrichment", stage("enrich", {"attempted": 1})),
            patch("jobs.pipeline.analyze_top_posts_v2", stage("analyze", {"analyzed": 1})),
            patch("jobs.pipeline.generate_digest", stage("digest", {"digest_id": "d1"})),
            patch("jobs.pipeline.create_report", stage("report", {"json_path": "r.json"})),
        ):
            result = run_pipeline(
                db_path=str(self.db), raw_dir=None, period="day",
                enrich_limit=1, analysis_limit=1, digest_limit=5,
                provider="local", report_dir=str(self.root / "reports"),
            )

        self.assertEqual(calls, ["enrich", "analyze", "digest", "report"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["stages"]["digest"]["digest_id"], "d1")

    def test_pipeline_is_partial_without_requested_analysis_or_with_provisional_output(self) -> None:
        base = {
            "enrich": {"failed": 0},
            "digest": {"digest_id": "d1", "artifact_kind": "llm"},
            "report": {"json_path": "r.json"},
        }
        for analyze in (
            {"attempted": 0, "analyzed": 0, "failed": 0, "provisional": 0},
            {"attempted": 1, "analyzed": 1, "failed": 0, "provisional": 1},
        ):
            with (
                patch("jobs.pipeline.run_enrichment", return_value=base["enrich"]),
                patch("jobs.pipeline.analyze_top_posts_v2", return_value=analyze),
                patch("jobs.pipeline.generate_digest", return_value=base["digest"]),
                patch("jobs.pipeline.create_report", return_value=base["report"]),
            ):
                result = run_pipeline(
                    db_path=str(self.db), raw_dir=None, period="day",
                    enrich_limit=1, analysis_limit=1, provider="auto",
                )
            self.assertEqual(result["status"], "partial")

    def test_report_writes_timestamped_and_latest_files(self) -> None:
        report_dir = self.root / "reports"
        result = create_report(str(self.db), "day", limit=2, output_dir=report_dir)

        json_path = Path(result["json_path"])
        markdown_path = Path(result["markdown_path"])
        self.assertTrue(json_path.is_file())
        self.assertTrue(markdown_path.is_file())
        self.assertTrue((report_dir / "day" / "latest.json").is_file())
        self.assertTrue((report_dir / "day" / "latest.md").is_file())
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["period"], "day")
        self.assertEqual(len(payload["items"]), 2)

    def test_report_does_not_mix_digest_periods_and_archives_do_not_collide(self) -> None:
        store = Storage(str(self.db), None)
        store.upsert_ai_digest({
            "digest_id": "three-hour", "period": "3h", "provider": "openai",
            "status": "success", "payload_json": json.dumps({"title": "3h only"}),
            "generated_at": time.time(),
        })
        store.commit()
        store.close()
        first = create_report(str(self.db), "day", output_dir=self.root / "reports")
        second = create_report(str(self.db), "day", output_dir=self.root / "reports")
        self.assertIsNone(first["report"]["ai_digest"])
        self.assertNotEqual(first["json_path"], second["json_path"])

    def test_atomic_write_supports_concurrent_writers(self) -> None:
        target = self.root / "latest.txt"
        values = ["alpha" * 1000, "beta" * 1000]
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda value: _atomic_write(target, value), values))
        self.assertIn(target.read_text(encoding="utf-8"), values)

    def test_cli_commands_return_nonzero_for_any_partial_failure(self) -> None:
        enrich_args = SimpleNamespace(
            db=str(self.db), raw=None, period="day", limit=2, depth=2,
            kind="resources", retry_after_hours=0,
        )
        with patch("jobs.enrich.run_enrichment", return_value={
            "period": "day", "comments_fetched": 0, "resources_extracted": 0,
            "failed": 1, "comment_posts_attempted": 0, "comment_posts_enriched": 0,
        }):
            self.assertEqual(cmd_enrich(enrich_args), 2)

        analyze_args = SimpleNamespace(
            db=str(self.db), period="day", limit=2, provider="auto",
            comment_limit=120, freshness_hours=24,
        )
        with patch("jobs.pipeline.analyze_top_posts_v2", return_value={
            "period": "day", "analyzed": 1, "skipped_fresh": 0,
            "skipped_no_comments": 0, "failed": 1, "attempted": 2,
            "provisional": 0,
        }):
            self.assertEqual(cmd_analyze_top(analyze_args), 2)


if __name__ == "__main__":
    unittest.main()
