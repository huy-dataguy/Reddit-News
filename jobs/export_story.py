"""story-export: chọn tin giá trị cao và xuất story JSON cho MediaWorkflow.

Cầu nối Reddit Radar → repo-review-studio: post nổi bật (trend_score) đã có
Community Intelligence được map deterministic sang schema
``RedditStoryData`` (16 field) + 8 câu thoại, ghi vào ``stories/`` của studio.
Không gọi LLM: ưu tiên ``ai_post_analysis_v2``, rồi mới fallback V1.
"""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.request
from pathlib import Path
from typing import Any

from reddit_crawler.analytics import trending_posts

# Reddit hiện là sibling trực tiếp của MediaWorkflow (Reddit dời khỏi Startup/ 2026-07-20).
DEFAULT_STUDIO = (
    Path(__file__).resolve().parent.parent.parent / "MediaWorkflow"
)
FALLBACK_IMAGE = "screenshot.png"
REQUIRED_FIELDS = [
    "postId", "subreddit", "title", "score", "comments", "upvoteRatio",
    "image", "source", "hook", "hookSub", "daily", "dailySub",
    "policy", "reactions", "lesson", "question",
]


def _fmt_int(n: Any) -> str:
    """Định dạng số kiểu VN: 21985 -> '21.985'."""
    try:
        return f"{int(n):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "0"


def _trim(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def pick_top_post(db_path: str | Path, period: str = "week") -> str:
    """Post trend_score cao nhất có phân tích Gemini/OpenAI publishable."""
    with _connect(db_path) as conn:
        analyzed = {
            r["post_id"]
            for r in conn.execute(
                "select post_id from ai_post_analysis_v2 "
                "where status='success' and provider in ('gemini','openai') "
                "union select post_id from ai_post_analysis "
                "where status='success' and provider in ('gemini','openai')"
            )
        }
    if not analyzed:
        raise SystemExit(
            "chưa có PostAnalysis Gemini/OpenAI publishable — chạy analyze-top trước"
        )
    for item in trending_posts(db_path, period=period, limit=200):
        if item["post_id"] in analyzed:
            return item["post_id"]
    raise SystemExit(
        f"không có post nào trong cửa sổ '{period}' có phân tích Gemini/OpenAI"
    )


def load_post_bundle(db_path: str | Path, post_id: str) -> dict[str, Any]:
    with _connect(db_path) as conn:
        post = conn.execute(
            "select p.*, s.display_name as sub_name from fact_post p"
            " left join dim_subreddit s on s.subreddit_id = p.subreddit_id"
            " where p.post_id = ?",
            (post_id,),
        ).fetchone()
        if post is None:
            raise SystemExit(f"post {post_id} không có trong DB")
        metrics = conn.execute(
            "select * from fact_post_metrics where post_id = ?"
            " order by observed_at desc limit 1",
            (post_id,),
        ).fetchone()
        media = conn.execute(
            "select image_url, thumbnail_url from fact_post_media where post_id = ? limit 1",
            (post_id,),
        ).fetchone()
        analysis_rows = []
        for version, table in ((2, "ai_post_analysis_v2"), (1, "ai_post_analysis")):
            row = conn.execute(
                f"select provider, model, payload_json, generated_at from {table} "
                "where post_id = ? and status = 'success'",
                (post_id,),
            ).fetchone()
            if row:
                analysis_rows.append((version, row))
    analysis: dict[str, Any] = {}
    analysis_meta: dict[str, Any] = {}
    ranked_rows = sorted(
        analysis_rows,
        key=lambda value: (
            40 if value[0] == 2 and value[1]["provider"] in {"gemini", "openai"}
            else 30 if value[0] == 1 and value[1]["provider"] in {"gemini", "openai"}
            else 20 if value[0] == 2 else 10,
            float(value[1]["generated_at"] or 0),
        ),
        reverse=True,
    )
    for version, analysis_row in ranked_rows:
        try:
            candidate = json.loads(analysis_row["payload_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(candidate, dict) and any(candidate.get(field) for field in (
            "topic", "author_summary", "author_goal", "verdict", "community_consensus",
            "key_points", "opinion_groups",
        )):
            analysis = candidate
            analysis_meta = {
                "provider": analysis_row["provider"], "model": analysis_row["model"],
                "generated_at": analysis_row["generated_at"], "version": version,
            }
            break
    return {
        "post": dict(post),
        "metrics": dict(metrics) if metrics else {},
        "media": dict(media) if media else {},
        "analysis": analysis,
        "analysis_meta": analysis_meta,
    }


def build_story(bundle: dict[str, Any], image_file: str) -> dict[str, Any]:
    """Map bundle -> story JSON (data + scenes thoại). Thuần, test được."""
    post, metrics, analysis = bundle["post"], bundle["metrics"], bundle["analysis"]
    sub = post.get("sub_name") or post.get("subreddit_id") or "reddit"
    sub_disp = sub if str(sub).startswith("r/") else f"r/{sub}"
    score = metrics.get("score", post.get("score", 0))
    comments = metrics.get("num_comments", post.get("num_comments", 0))
    ratio = metrics.get("upvote_ratio", post.get("upvote_ratio")) or 0
    age_h = max(1, round((time.time() - (post.get("created_utc") or time.time())) / 3600))

    points = [point for point in analysis.get("key_points", []) if point.get("claim")]
    groups = [group for group in analysis.get("opinion_groups", []) if group.get("summary")]
    reaction_sources = (
        [(point["claim"], point.get("comment_ids", [])) for point in points]
        if points else [(group["summary"], group.get("comment_ids", [])) for group in groups]
    )
    reactions = [{
        "score": _fmt_int(len(comment_ids) or 1), "text": _trim(text, 140),
    } for text, comment_ids in reaction_sources[:3]]
    while len(reactions) < 3:
        filler = (
            analysis.get("claimed_results")
            or [analysis.get("verdict") or analysis.get("author_summary")
                or analysis.get("community_consensus", "")]
        )
        reactions.append(
            {"score": "1", "text": _trim(filler[0] or "Cộng đồng đang tiếp tục thảo luận.", 140)}
        )

    policy_src = (
        analysis.get("action_items", [])
        or [r.get("name") or r.get("description", "") for r in analysis.get("resources", [])]
        or
        [s.get("name") or s.get("description", "") for s in analysis.get("suggestions", [])]
        or analysis.get("learning_points", [])
        or [post.get("link_flair_text") or "Tin nổi bật"]
    )
    policy = [_trim(p, 90) for p in policy_src if p][:3]

    lessons = (
        ([analysis.get("verdict")] if analysis.get("verdict") else [])
        or analysis.get("action_items", [])
        or analysis.get("learning_points", [])
        or [analysis.get("community_consensus", "")]
    )
    lesson = _trim(lessons[0] or "Theo dõi thêm để rút bài học.", 160)
    questions = analysis.get("open_questions") or analysis.get("unanswered_questions") or []
    question = _trim(
        questions[0] if questions else "Bạn nghĩ gì về tín hiệu này?", 140
    )

    topic = analysis.get("topic") or _trim(post.get("title", ""), 90)
    consensus = analysis.get("verdict") or analysis.get("community_consensus", "")

    data = {
        "postId": post["post_id"],
        "subreddit": sub_disp,
        "title": post.get("title", ""),
        "score": _fmt_int(score),
        "comments": _fmt_int(comments),
        "upvoteRatio": f"{round(float(ratio) * 100)}%",
        "image": image_file,
        "source": f"reddit.com/{sub_disp}/comments/{post['post_id']}",
        "hook": _fmt_int(score),
        "hookSub": f"upvote trên {sub_disp} sau {age_h} giờ",
        "daily": _fmt_int(comments),
        "dailySub": "bình luận đang tranh luận",
        "policy": policy,
        "reactions": reactions,
        "lesson": lesson,
        "question": question,
    }

    scenes = [
        {"lines": [f"Cộng đồng {sub_disp} đang dậy sóng về chủ đề: {topic}."]},
        {"lines": [
            f"Bài đăng đạt {data['score']} điểm với {data['comments']} bình luận,"
            f" tỷ lệ upvote {data['upvoteRatio']}."
        ]},
        {"lines": [_trim(analysis.get("context") or analysis.get("author_summary")
                         or analysis.get("problem_context") or analysis.get("author_goal")
                         or f"Chủ đề đang được {sub_disp} theo dõi sát.", 180)]},
        {"lines": [f"Ba điểm đáng chú ý nhất: {'; '.join(policy)}." if policy
                   else "Cộng đồng đưa ra nhiều đề xuất đáng chú ý."]},
        {"lines": [_trim(reactions[0]["text"], 180)]},
        {"lines": [_trim(consensus or reactions[-1]["text"], 180)]},
        {"lines": [lesson]},
        {"lines": [question]},
    ]

    return {
        "composition": "RedditStory",
        "output": f"reddit-{post['post_id']}",
        "data": data,
        "scenes": scenes,
    }


def validate_story(story: dict[str, Any]) -> list[str]:
    """Trả danh sách lỗi; rỗng nghĩa là hợp lệ."""
    errors = []
    data = story.get("data", {})
    for f in REQUIRED_FIELDS:
        v = data.get(f)
        if v in (None, "", []):
            errors.append(f"data.{f} rỗng")
    if len(data.get("reactions", [])) != 3:
        errors.append("cần đúng 3 reactions")
    if not 1 <= len(data.get("policy", [])) <= 3:
        errors.append("policy phải có 1..3 mục")
    scenes = story.get("scenes", [])
    if len(scenes) != 8:
        errors.append(f"cần đúng 8 scene, có {len(scenes)}")
    if any(not s.get("lines") or not all(s["lines"]) for s in scenes):
        errors.append("mỗi scene cần ≥1 câu thoại không rỗng")
    return errors


def fetch_image(bundle: dict[str, Any], studio: Path, post_id: str) -> str:
    url = bundle["media"].get("image_url") or bundle["media"].get("thumbnail_url")
    if not url:
        return FALLBACK_IMAGE
    dest = studio / "public" / f"reddit-{post_id}.jpg"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "reddit-radar-export/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            dest.write_bytes(resp.read())
        return dest.name
    except Exception as exc:  # ảnh chỉ là trang trí — export không được chết vì nó
        print(f"tải ảnh thất bại ({exc}); dùng fallback {FALLBACK_IMAGE}")
        return FALLBACK_IMAGE


def export_story(
    db_path: str | Path = "reddit.db",
    *,
    post_id: str | None = None,
    period: str = "week",
    studio: str | Path | None = None,
    dry_check: bool = False,
) -> Path:
    studio_dir = Path(studio) if studio else DEFAULT_STUDIO
    if not (studio_dir / "src").is_dir():
        raise SystemExit(f"không thấy studio tại {studio_dir}")
    pid = post_id or pick_top_post(db_path, period=period)
    bundle = load_post_bundle(db_path, pid)
    if not bundle["analysis"]:
        raise SystemExit(
            f"post {pid} chưa có Community Intelligence — chạy: cli.py analyze-post {pid}"
        )
    provider = bundle["analysis_meta"].get("provider")
    version = bundle["analysis_meta"].get("version")
    quality_issues = set(bundle["analysis"].get("quality_issues") or [])
    if provider not in {"gemini", "openai"}:
        raise SystemExit(
            f"post {pid} chỉ có bản local/provisional; cần Gemini/OpenAI trước khi xuất video"
        )
    if version == 2 and (
        bundle["analysis"].get("language") != "vi"
        or quality_issues.intersection({
            "provisional_local_extract", "semantic_synthesis_not_run", "provider_chain_failed",
        })
    ):
        raise SystemExit(f"post {pid} có V2 analysis chưa publishable")
    image_file = FALLBACK_IMAGE if dry_check else fetch_image(bundle, studio_dir, pid)
    story = build_story(bundle, image_file)
    errors = validate_story(story)
    if errors:
        raise SystemExit("story không hợp lệ: " + "; ".join(errors))
    out = studio_dir / "stories" / f"reddit-{pid}.json"
    out.write_text(json.dumps(story, ensure_ascii=False, indent=2))
    print(f"OK {out}")
    print(f"render: cd '{studio_dir}' && bash scripts/make-video.sh stories/{out.name}")
    return out
