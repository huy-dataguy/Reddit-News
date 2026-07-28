import json
import tempfile
import time
import unittest
from pathlib import Path

from jobs.export_story import (
    _fmt_int, _trim, build_story, export_story, load_post_bundle, pick_top_post,
    validate_story,
)
from reddit_crawler.storage import Storage

FIXTURE = {
    "post": {
        "post_id": "abc123",
        "sub_name": "LocalLLaMA",
        "subreddit_id": "t5_x",
        "title": "New 7B model beats GPT-4 on coding",
        "created_utc": 0,  # tuổi rất lớn — chỉ cần > 0 giờ
        "score": 5432,
        "num_comments": 321,
        "upvote_ratio": 0.97,
        "link_flair_text": "News",
    },
    "metrics": {"score": 6000, "num_comments": 400, "upvote_ratio": 0.96},
    "media": {},
    "analysis": {
        "topic": "Model 7B mới vượt GPT-4 về coding",
        "author_goal": "Chia sẻ benchmark",
        "problem_context": "Cộng đồng nghi ngờ benchmark tự công bố.",
        "community_consensus": "Đồng thuận rằng cần benchmark độc lập.",
        "opinion_groups": [
            {"summary": "Nhóm ủng hộ: kết quả ấn tượng.", "comment_ids": ["a", "b", "c"]},
            {"summary": "Nhóm hoài nghi: cần kiểm chứng độc lập.", "comment_ids": ["d"]},
            {"summary": "Nhóm thực dụng: chờ bản GGUF.", "comment_ids": ["e", "f"]},
            {"summary": "Nhóm thứ tư — không được dùng.", "comment_ids": []},
        ],
        "suggestions": [
            {"name": "Chạy benchmark local", "kind": "workflow", "description": "..."},
            {"name": "So sánh với model cũ", "kind": "analysis", "description": "..."},
        ],
        "learning_points": ["Benchmark tự công bố cần kiểm chứng độc lập."],
        "unanswered_questions": ["Bao giờ có bản quantized?"],
    },
}


class BuildStoryTest(unittest.TestCase):
    def setUp(self):
        self.story = build_story(FIXTURE, image_file="screenshot.png")

    def test_story_is_valid(self):
        self.assertEqual(validate_story(self.story), [])

    def test_metadata_and_output_name(self):
        self.assertEqual(self.story["composition"], "RedditStory")
        self.assertEqual(self.story["output"], "reddit-abc123")

    def test_metrics_prefer_latest_snapshot(self):
        data = self.story["data"]
        self.assertEqual(data["score"], "6.000")
        self.assertEqual(data["comments"], "400")
        self.assertEqual(data["upvoteRatio"], "96%")

    def test_exactly_three_reactions_from_opinion_groups(self):
        texts = [r["text"] for r in self.story["data"]["reactions"]]
        self.assertEqual(len(texts), 3)
        self.assertNotIn("Nhóm thứ tư — không được dùng.", texts)
        self.assertEqual(self.story["data"]["reactions"][0]["score"], "3")

    def test_eight_scenes_with_nonempty_lines(self):
        scenes = self.story["scenes"]
        self.assertEqual(len(scenes), 8)
        for s in scenes:
            self.assertTrue(s["lines"] and all(s["lines"]))
        self.assertIn("tỷ lệ upvote", scenes[1]["lines"][0])

    def test_lesson_and_question_from_analysis(self):
        self.assertIn("kiểm chứng độc lập", self.story["data"]["lesson"])
        self.assertIn("quantized", self.story["data"]["question"])


class SparseAnalysisTest(unittest.TestCase):
    def test_fallbacks_still_produce_valid_story(self):
        sparse = dict(FIXTURE, analysis={"community_consensus": "Đang thảo luận."})
        story = build_story(sparse, image_file="screenshot.png")
        self.assertEqual(validate_story(story), [])
        self.assertEqual(len(story["data"]["reactions"]), 3)


class V2StoryTest(unittest.TestCase):
    def test_v2_fields_map_to_story_semantics(self):
        bundle = dict(FIXTURE, analysis={
            "topic": "Compiler mới",
            "author_summary": "Tác giả công bố một compiler mới.",
            "context": "Compiler nhắm tới thời gian build ngắn hơn.",
            "key_points": [
                {"claim": "Build nhanh hơn trong dự án lớn.", "comment_ids": ["c1"]},
                {"claim": "Plugin ecosystem còn thiếu.", "comment_ids": ["c2"]},
            ],
            "resources": [{"name": "Benchmark suite", "description": "Repo test"}],
            "action_items": ["Chạy benchmark suite trên một service đại diện."],
            "open_questions": ["Khi nào có plugin cho IDE?"],
            "verdict": "Đáng thử nghiệm có giới hạn trước khi áp dụng rộng.",
        })
        story = build_story(bundle, image_file="screenshot.png")
        self.assertEqual(validate_story(story), [])
        self.assertIn("Build nhanh", story["data"]["reactions"][0]["text"])
        self.assertIn("benchmark suite", story["data"]["policy"][0])
        self.assertIn("Đáng thử nghiệm", story["data"]["lesson"])
        self.assertIn("plugin", story["data"]["question"])
        self.assertIn("thời gian build", story["scenes"][2]["lines"][0])

    def test_database_loader_prefers_v2_and_picker_accepts_v2_only(self):
        root = Path(tempfile.mkdtemp())
        db = root / "test.db"
        store = Storage(str(db), None)
        now = time.time()
        post = {
            "id": "p1", "name": "t3_p1", "subreddit_id": "t5_dev",
            "subreddit": "programming", "author": "alice", "created_utc": now - 30,
            "title": "Compiler", "score": 10, "num_comments": 2,
            "upvote_ratio": .9, "over_18": False,
        }
        store.upsert_post(post)
        store.snapshot_metrics(post)
        store.upsert_ai_post_analysis({
            "post_id": "p1", "provider": "openai", "model": "v1",
            "status": "success", "payload_json": json.dumps({"topic": "old"}),
            "generated_at": now - 10,
        })
        store.upsert_ai_post_analysis_v2({
            "post_id": "p1", "provider": "gemini", "model": "v2",
            "status": "success", "payload_json": json.dumps({"topic": "new"}),
            "generated_at": now,
        })
        store.commit()
        store.close()

        self.assertEqual(pick_top_post(db, period="day"), "p1")
        bundle = load_post_bundle(db, "p1")
        self.assertEqual(bundle["analysis"]["topic"], "new")
        self.assertEqual(bundle["analysis_meta"]["provider"], "gemini")
        self.assertEqual(bundle["analysis_meta"]["version"], 2)

    def test_loader_prefers_v1_llm_over_v2_local_and_export_rejects_local_only(self):
        root = Path(tempfile.mkdtemp())
        db = root / "test.db"
        studio = root / "studio"
        (studio / "src").mkdir(parents=True)
        (studio / "stories").mkdir()
        store = Storage(str(db), None)
        now = time.time()
        for post_id in ("mixed", "local-only"):
            post = {
                "id": post_id, "name": f"t3_{post_id}", "subreddit_id": "t5_dev",
                "subreddit": "programming", "author": "alice", "created_utc": now - 30,
                "title": post_id, "score": 10, "num_comments": 2,
                "upvote_ratio": .9, "over_18": False,
            }
            store.upsert_post(post)
            store.snapshot_metrics(post)
            store.upsert_ai_post_analysis_v2({
                "post_id": post_id, "provider": "local-fallback", "status": "success",
                "payload_json": json.dumps({
                    "language": "source", "topic": "Local extract",
                    "quality_issues": ["provisional_local_extract"],
                }), "generated_at": now,
            })
        store.upsert_ai_post_analysis({
            "post_id": "mixed", "provider": "openai", "model": "v1",
            "status": "success", "payload_json": json.dumps({
                "language": "vi", "topic": "LLM result",
                "community_consensus": "Grounded summary",
            }), "generated_at": now - 10,
        })
        store.commit()
        store.close()

        bundle = load_post_bundle(db, "mixed")
        self.assertEqual(bundle["analysis"]["topic"], "LLM result")
        self.assertEqual(bundle["analysis_meta"]["provider"], "openai")
        with self.assertRaisesRegex(SystemExit, "Gemini/OpenAI"):
            export_story(
                db, post_id="local-only", studio=studio, dry_check=True,
            )
        store = Storage(str(db), None)
        store.conn.execute("DELETE FROM ai_post_analysis WHERE post_id='mixed'")
        store.commit()
        store.close()
        with self.assertRaisesRegex(SystemExit, "Gemini/OpenAI"):
            pick_top_post(db, period="day")


class HelperTest(unittest.TestCase):
    def test_fmt_int_vietnamese_thousands(self):
        self.assertEqual(_fmt_int(21985), "21.985")
        self.assertEqual(_fmt_int("bad"), "0")

    def test_trim_adds_ellipsis(self):
        self.assertEqual(_trim("a b  c", 100), "a b c")
        self.assertTrue(_trim("x" * 200, 90).endswith("…"))


if __name__ == "__main__":
    unittest.main()
