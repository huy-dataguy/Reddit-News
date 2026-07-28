from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from reddit_crawler.analytics import latest_ai_digest
from reddit_crawler.llm import (
    POST_ANALYSIS_V2_INSTRUCTIONS, DigestContent, OpinionGroup, OpinionPoint,
    PostAnalysis, PostAnalysisV2, ResourceItem, ResourceSuggestion, Story,
    _ground_post_analysis_v2, _parse_digest_json, _post_analysis_v2_quality_issues, _sanitize_digest,
    _sanitize_post_analysis, _sanitize_post_analysis_v2, _urls_in_post_bundle,
    generate_digest, generate_post_analysis_v2, local_post_analysis_v2,
)
from reddit_crawler.storage import Storage


class LlmDigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.db = self.root / "test.db"
        store = Storage(str(self.db), None)
        now = time.time()
        posts = [
            {
                "id": "gpt", "name": "t3_gpt", "subreddit_id": "t5_ai",
                "subreddit": "LocalLLaMA", "author": "alice", "created_utc": now - 300,
                "title": "GPT-6 model benchmark announced", "score": 120,
                "num_comments": 30, "over_18": False,
                "url": "https://example.com/gpt", "thumbnail": "https://img.example/gpt.jpg",
            },
            {
                "id": "claude", "name": "t3_claude", "subreddit_id": "t5_ai",
                "subreddit": "ClaudeAI", "author": "bob", "created_utc": now - 600,
                "title": "Claude context token update", "score": 80,
                "num_comments": 20, "over_18": False,
            },
        ]
        for post in posts:
            store.upsert_post(post)
            store.snapshot_metrics(post)
        store.close()

    def test_local_digest_is_persisted_and_sourced(self) -> None:
        result = generate_digest(str(self.db), "3h", "local", 10)
        self.assertEqual(result["provider"], "local")
        self.assertEqual(result["artifact_kind"], "provisional")
        self.assertTrue(result["payload"]["model_updates"])
        valid = {"gpt", "claude"}
        for update in result["payload"]["model_updates"]:
            self.assertTrue(set(update["source_post_ids"]) <= valid)
        saved = latest_ai_digest(self.db, "3h")
        self.assertEqual(saved["digest_id"], result["digest_id"])

    def test_sanitizer_drops_unknown_sources_and_uses_crawled_image(self) -> None:
        digest = DigestContent(
            title="x", executive_summary="x", key_numbers=[], model_updates=[], comparisons=[],
            stories=[
                Story(category="ai", headline="real", summary="x", why_it_matters="x",
                      key_facts=[], confidence=.5, source_post_ids=["gpt"],
                      image_url="https://invented.invalid/x.jpg"),
                Story(category="ai", headline="fake", summary="x", why_it_matters="x",
                      key_facts=[], confidence=.5, source_post_ids=["unknown"]),
            ], watchlist=[], methodology_note="x",
        )
        clean = _sanitize_digest(digest, [{
            "post_id": "gpt", "title": "GPT", "source_url": "https://example.com",
            "reddit_url": None, "subreddit": "ai", "image_url": "https://img.example/gpt.jpg",
        }])
        self.assertEqual([story.headline for story in clean.stories], ["real"])
        self.assertEqual(clean.stories[0].image_url, "https://img.example/gpt.jpg")

    def test_parser_ignores_agent_citations_after_json(self) -> None:
        raw = """Verified sources follow.\n```json
{"language":"vi","title":"x","executive_summary":"x","key_numbers":[],
"model_updates":[],"comparisons":[],"stories":[],"watchlist":[],
"methodology_note":"x","sources":[]}
```\nSources: https://example.com"""
        self.assertEqual(_parse_digest_json(raw).title, "x")

    def test_post_analysis_keeps_only_crawled_comment_evidence(self) -> None:
        bundle = {
            "post": {"post_id": "gpt", "selftext": "", "article_body": ""},
            "comments": [{
                "comment_id": "c1", "body": "Try https://example.com/tool", "score": 5,
            }],
        }
        analysis = PostAnalysis(
            source_post_id="invented", topic="x", author_goal="x", problem_context="x",
            community_consensus="x", methodology_note="x",
            opinion_groups=[OpinionGroup(
                label="x", stance="resource", summary="x",
                comment_ids=["c1", "fake", "c1"], support_count=99,
            )],
            suggestions=[
                ResourceSuggestion(
                    name="real", kind="tool", description="x",
                    url="https://example.com/tool", comment_ids=["c1"],
                ),
                ResourceSuggestion(
                    name="invented", kind="tool", description="x",
                    url="https://invented.invalid", comment_ids=["c1"],
                ),
                ResourceSuggestion(
                    name="no evidence", kind="tool", description="x", comment_ids=["fake"],
                ),
            ],
        )
        clean = _sanitize_post_analysis(analysis, bundle)
        self.assertEqual(clean.source_post_id, "gpt")
        self.assertEqual(clean.opinion_groups[0].comment_ids, ["c1"])
        self.assertEqual(clean.opinion_groups[0].support_count, 1)
        self.assertEqual(clean.suggestions[0].url, "https://example.com/tool")
        self.assertIsNone(clean.suggestions[1].url)
        self.assertEqual(len(clean.suggestions), 2)

    def test_v2_sanitizer_rewrites_evidence_and_drops_invented_urls(self) -> None:
        bundle = {
            "post": {"post_id": "gpt", "selftext": "", "article_body": ""},
            "comments": [{
                "comment_id": "c1", "author": "alice", "score": 7,
                "body": "Use https://example.com/tool for this workflow.",
            }],
        }
        analysis = PostAnalysisV2(
            source_post_id="wrong", domain="devtools", topic="Grounded topic",
            author_summary="A sufficiently specific summary of the source material and its result.",
            context="Context", verdict="A grounded verdict remains provisional.",
            methodology_note="method",
            key_points=[
                OpinionPoint(claim="real", evidence="invented", stance="support",
                             comment_ids=["c1", "fake", "c1"]),
                OpinionPoint(claim="fake", evidence="invented", stance="support",
                             comment_ids=["fake"]),
            ],
            resources=[
                ResourceItem(name="real", kind="tool", description="x",
                             url="https://example.com/tool", confidence="unverified"),
                ResourceItem(name="bad url", kind="tool", description="x",
                             url="https://invented.invalid", confidence="unverified",
                             source_comment_id="c1"),
                ResourceItem(name="ungrounded", kind="tool", description="x",
                             url="https://invented.invalid/2", confidence="unverified"),
            ],
        )
        clean = _sanitize_post_analysis_v2(analysis, bundle)
        self.assertEqual(clean.source_post_id, "gpt")
        self.assertEqual([point.claim for point in clean.key_points], ["real"])
        self.assertEqual(clean.key_points[0].comment_ids, ["c1"])
        self.assertIn("u/alice, 7 upvote, comment c1", clean.key_points[0].evidence)
        self.assertIn("Use https://example.com/tool for this workflow.", clean.key_points[0].evidence)
        self.assertEqual(clean.resources[0].source_comment_id, "c1")
        self.assertIsNone(clean.resources[1].url)
        self.assertIn("không xuất hiện", clean.resources[1].note)
        self.assertEqual(len(clean.resources), 2)

    def test_v2_quality_gate_flags_ungrounded_generic_output(self) -> None:
        bundle = {
            "post": {"post_id": "gpt", "selftext": "", "article_body": ""},
            "comments": [{"comment_id": "c1", "body": "grounded", "score": 1}],
        }
        analysis = PostAnalysisV2(
            source_post_id="gpt", domain="ai_ml", topic="x",
            author_summary="Bài viết này nói về AI.", context="x",
            verdict="A non-empty verdict.", methodology_note="x",
            key_points=[OpinionPoint(
                claim="x", evidence="x", stance="support", comment_ids=["fake"],
            )],
            resources=[ResourceItem(
                name="x", kind="tool", description="x",
                url="https://invented.invalid", confidence="unverified",
            )],
            action_items=["Đọc kỹ thread gốc để tìm hiểu thêm."],
        )
        issues = _post_analysis_v2_quality_issues(analysis, bundle)
        self.assertIn("generic_author_summary", issues)
        self.assertIn("ungrounded_key_point:0", issues)
        self.assertIn("invented_resource_url:0", issues)
        self.assertIn("generic_only_actions", issues)

    def test_v2_grounding_drops_one_bad_point_without_discarding_valid_output(self) -> None:
        bundle = {
            "post": {"post_id": "gpt", "selftext": "", "article_body": ""},
            "comments": [{
                "comment_id": "c1", "author": "alice", "score": 4,
                "body": "A concrete observation grounded in the crawled discussion.",
            }],
        }
        analysis = PostAnalysisV2(
            source_post_id="gpt", domain="ai_ml", topic="Tác động vận hành",
            author_summary=(
                "Tác giả nêu một thay đổi cụ thể và mô tả tác động trực tiếp "
                "đến cách hệ thống được vận hành trong thực tế."
            ),
            context="Thảo luận tập trung vào hậu quả và cách đánh giá bằng chứng.",
            verdict="Kết luận cần giữ phạm vi theo đúng dữ liệu đã được crawl.",
            methodology_note="Tổng hợp bài gốc và bình luận đã lưu.",
            key_points=[
                OpinionPoint(
                    claim="Luận điểm có nguồn.", evidence="model supplied",
                    stance="support", comment_ids=["c1"],
                ),
                OpinionPoint(
                    claim="Luận điểm không có nguồn.", evidence="model supplied",
                    stance="caveat", comment_ids=["invented"],
                ),
            ],
            action_items=["Đối chiếu số liệu trong bài với nguồn công khai liên quan."],
        )

        grounded = _ground_post_analysis_v2(analysis, bundle)

        self.assertEqual([point.claim for point in grounded.key_points], ["Luận điểm có nguồn."])
        self.assertIn("comment c1", grounded.key_points[0].evidence)

    def test_local_v2_is_truthfully_labeled_provisional_source_extract(self) -> None:
        title = "A new compiler workflow"
        bundle = {
            "post": {
                "post_id": "p1", "title": title, "subreddit": "programming",
                "score": 12, "selftext": "", "article_body": "",
            },
            "comments": [{
                "comment_id": "c1", "author": "alice", "score": 4,
                "body": "This concrete comment contains enough source text to extract.",
            }],
        }
        analysis = local_post_analysis_v2(bundle)
        self.assertEqual(analysis.language, "source")
        self.assertEqual(analysis.domain, "devtools")
        self.assertEqual(analysis.topic, title)
        self.assertIn("provisional_local_extract", analysis.quality_issues)
        self.assertIn("không dùng LLM", analysis.methodology_note)
        self.assertIn("u/alice", analysis.key_points[0].evidence)
        self.assertFalse(any("Đọc kỹ thread" in action for action in analysis.action_items))

    def test_local_v2_handles_comment_urls_without_schema_validation_error(self) -> None:
        analysis = local_post_analysis_v2({
            "post": {
                "post_id": "p1", "title": "A compiler tool", "subreddit": "programming",
                "score": 5, "selftext": "", "article_body": "",
            },
            "comments": [{
                "comment_id": "c1", "author": "alice", "score": 2,
                "body": "Try https://example.com/tool for this concrete workflow.",
            }],
        })
        self.assertEqual(analysis.resources[0].url, "https://example.com/tool")
        self.assertIn(analysis.resources[0].kind, {
            "github_repo", "arxiv_paper", "tech_blog", "tool", "doc", "other",
        })

    def test_v2_url_grounding_handles_quotes_and_corrects_resource_attribution(self) -> None:
        bundle = {
            "post": {"post_id": "p1", "selftext": "", "article_body": ""},
            "comments": [
                {"comment_id": "c1", "author": "alice", "score": 3,
                 "body": 'Use "https://example.com/tool" for the build.'},
                {"comment_id": "c2", "author": "bob", "score": 1,
                 "body": "No URL in this comment."},
            ],
        }
        self.assertEqual(_urls_in_post_bundle(bundle), {"https://example.com/tool"})
        analysis = PostAnalysisV2(
            source_post_id="p1", domain="devtools", topic="Compiler workflow",
            author_summary="A concrete compiler workflow is described with a linked tool.",
            context="Build tooling", verdict="The tool still requires independent evaluation.",
            methodology_note="method",
            resources=[ResourceItem(
                name="Tool", kind="tool", description="Build helper",
                url="https://example.com/tool", confidence="verified",
                source_comment_id="c2",
            )],
        )
        issues = _post_analysis_v2_quality_issues(analysis, bundle)
        self.assertIn("misattributed_resource_comment:0", issues)
        self.assertIn("unverified_resource_confidence:0", issues)
        clean = _sanitize_post_analysis_v2(analysis, bundle)
        self.assertEqual(clean.resources[0].source_comment_id, "c1")
        self.assertEqual(clean.resources[0].confidence, "unverified")

    def test_auto_fallback_does_not_overwrite_existing_llm_analysis(self) -> None:
        store = Storage(str(self.db), None)
        store.upsert_comment({
            "id": "c1", "name": "t1_c1", "author": "reader", "score": 2,
            "body": "A concrete comment with https://example.com/tool",
        }, post_id="gpt")
        old_payload = {"topic": "Trusted old result", "domain": "ai_ml"}
        store.upsert_ai_post_analysis_v2({
            "post_id": "gpt", "provider": "gemini", "model": "good-model",
            "status": "success", "payload_json": json.dumps(old_payload),
            "comment_count": 1, "input_tokens": 10, "output_tokens": 5,
            "generated_at": time.time() - 60, "error": None,
        })
        store.commit()
        store.close()

        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=True),
            patch(
                "reddit_crawler.llm.gemini_post_analysis_v2",
                side_effect=RuntimeError("provider down"),
            ),
        ):
            result = generate_post_analysis_v2(str(self.db), "gpt", provider="auto")
        self.assertEqual(result["artifact_kind"], "provisional")
        self.assertFalse(result["persisted"])
        with sqlite3.connect(self.db) as conn:
            row = conn.execute(
                "SELECT provider, model, payload_json, error FROM ai_post_analysis_v2 "
                "WHERE post_id='gpt'"
            ).fetchone()
        self.assertEqual(row[0:2], ("gemini", "good-model"))
        self.assertEqual(json.loads(row[2]), old_payload)
        self.assertIsNone(row[3])

    def test_analysis_upsert_clears_stale_nullable_provenance(self) -> None:
        store = Storage(str(self.db), None)
        base = {
            "post_id": "gpt", "status": "success", "payload_json": "{}",
            "comment_count": 1, "input_tokens": 0, "output_tokens": 0,
            "generated_at": time.time(),
        }
        store.upsert_ai_post_analysis_v2({
            **base, "provider": "local-fallback", "model": None, "error": "provider down",
        })
        store.upsert_ai_post_analysis_v2({
            **base, "provider": "gemini", "model": "new-model", "error": None,
        })
        store.commit()
        row = store.conn.execute(
            "SELECT provider, model, error FROM ai_post_analysis_v2 WHERE post_id='gpt'"
        ).fetchone()
        store.close()
        self.assertEqual(row, ("gemini", "new-model", None))

    def test_existing_incomplete_schema_fails_loudly_without_mutation(self) -> None:
        db = self.root / "legacy.db"
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE fact_post(post_id TEXT PRIMARY KEY)")
            before = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        with self.assertRaisesRegex(RuntimeError, "schema.*migration 9"):
            Storage(str(db), None)
        with sqlite3.connect(db) as conn:
            after = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        self.assertEqual(after, before)

    def test_v2_contract_rejects_legacy_domain_and_prompt_marks_input_untrusted(self) -> None:
        with self.assertRaises(ValidationError):
            PostAnalysisV2(
                source_post_id="p", domain="ai_models", topic="x",
                author_summary="A detailed enough source summary for validation.",
                context="x", verdict="A verdict", methodology_note="x",
            )
        instructions = POST_ANALYSIS_V2_INSTRUCTIONS.lower()
        self.assertIn("dữ liệu không đáng", instructions)
        self.assertIn("system prompt", instructions)
        self.assertIn("credentials", instructions)


if __name__ == "__main__":
    unittest.main()
