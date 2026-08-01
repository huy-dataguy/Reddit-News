import math
import sqlite3
import time
from pathlib import Path
from typing import Union, List
import uuid

def materialize_gold(db_path: Union[str, Path], source_run_id: str, periods: List[str]) -> str:
    publish_id = f"pub_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    
    conn = sqlite3.connect(str(db_path), timeout=30)
    
    # Just creating basic records in gold tables
    # For now, simplistic materialization just inserting from fact_post 
    # to mart_post_signal and mart_post_knowledge
    
    try:
        with conn:
            now_ts = time.time()
            for period in periods:
                conn.execute(
                    "INSERT INTO mart_post_signal (publish_id, period, post_id, as_of, trend_score, source_run_id) "
                    "SELECT ?, ?, post_id, ?, score, ? FROM fact_post LIMIT 100",
                    (publish_id, period, now_ts, source_run_id)
                )

            conn.execute(
                "INSERT INTO mart_post_knowledge (publish_id, post_id, analysis_json, generated_at, source_run_id) "
                "SELECT ?, post_id, '{}', ?, ? FROM fact_post LIMIT 100",
                (publish_id, now_ts, source_run_id)
            )
            
            for period in periods:
                conn.execute(f"""
                    INSERT INTO mart_digest (publish_id, period, digest_id, generated_at, source_run_id)
                    VALUES ('{publish_id}', '{period}', 'digest_{period}', {time.time()}, '{source_run_id}')
                """)
                
    finally:
        conn.close()
        
    return publish_id


def _percentile(sorted_values: list[float], value: float) -> float:
    """Percentile (0-1) của ``value`` trong danh sách đã sort."""
    if not sorted_values:
        return 0.0
    lower = sum(1 for v in sorted_values if v < value)
    return lower / len(sorted_values)


def build_post_quality_mart(
    db_path: Union[str, Path],
    *,
    hours: float = 72,
    now: float | None = None,
) -> dict:
    """Tính mart đặc trưng chất lượng cho các post trong cửa sổ ``hours``.

    Đặc trưng chuẩn hóa theo từng subreddit (baseline rolling window):
    - ``score_ratio`` / ``comments_ratio``: score/comments của post chia median
      của chính sub trong window -> đo "độ nóng tương đối" (sub nhỏ vẫn phân biệt).
    - ``score_percentile`` / ``comments_percentile``: hạng tương đối trong sub.
    - ``engagement_ratio``: comments trên mỗi upvote (đo độ tranh luận).
    - ``upvote_ratio``: tỷ lệ ủng hộ từ metrics mới nhất.
    - ``quality_score``: composite 0-100:
        100 * (0.45 * relative_heat + 0.25 * engagement + 0.15 * upvote + 0.15 * freshness)
      với ``relative_heat = 0.5 * (score_ratio + comments_ratio) / (0.5 * (score_ratio + comments_ratio) + 1)``.

    Idempotent: upsert theo ``post_id``.
    """
    hours = max(1.0, min(float(hours), 24 * 7))
    now = now if now is not None else time.time()
    cutoff = now - hours * 3600

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA foreign_keys=ON")
            # Schema phải tồn tại trước khi ghi mart
            conn.executescript(Path(__file__).with_name("schema.sql").read_text())

            rows = conn.execute(
                """
                WITH ranked_metrics AS (
                    SELECT post_id, observed_at, score, num_comments,
                           ROW_NUMBER() OVER (
                               PARTITION BY post_id ORDER BY observed_at DESC
                           ) AS rn
                    FROM fact_post_metrics
                )
                SELECT p.post_id, p.subreddit_id, p.created_utc,
                       COALESCE(p.upvote_ratio, 0.75) AS upvote_ratio,
                       COALESCE(latest.score, p.score, 0) AS latest_score,
                       COALESCE(latest.num_comments, p.num_comments, 0) AS latest_comments
                FROM fact_post p
                LEFT JOIN ranked_metrics latest
                       ON latest.post_id = p.post_id AND latest.rn = 1
                WHERE p.created_utc >= ? AND COALESCE(p.over_18, 0) = 0
                """,
                (cutoff,),
            ).fetchall()

            per_sub: dict[str, list[dict]] = {}
            for row in rows:
                per_sub.setdefault(row["subreddit_id"] or "?", []).append(row)

            sub_stats: dict[str, dict] = {}
            for sub_id, posts in per_sub.items():
                scores = sorted(p["latest_score"] or 0 for p in posts)
                comments = sorted(p["latest_comments"] or 0 for p in posts)
                n = len(scores)
                sub_stats[sub_id] = {
                    "median_score": scores[n // 2],
                    "median_comments": comments[n // 2],
                    "count": n,
                }

            stored = 0
            for sub_id, posts in per_sub.items():
                stats = sub_stats[sub_id]
                scores = sorted(p["latest_score"] or 0 for p in posts)
                comments = sorted(p["latest_comments"] or 0 for p in posts)
                for post in posts:
                    latest_score = post["latest_score"] or 0
                    latest_comments = post["latest_comments"] or 0
                    score_ratio = latest_score / max(1.0, stats["median_score"])
                    comments_ratio = latest_comments / max(1.0, stats["median_comments"])
                    engagement_ratio = latest_comments / max(1.0, latest_score)
                    upvote_ratio = max(0.0, min(1.0, float(post["upvote_ratio"] or 0.75)))
                    age_hours = max((now - (post["created_utc"] or now)) / 3600, 0.0)

                    relative_heat = 0.5 * (score_ratio + comments_ratio) / (0.5 * (score_ratio + comments_ratio) + 1)
                    engagement = min(engagement_ratio / 2.0, 1.0)
                    freshness = 1.0 / (1.0 + age_hours / 12.0)
                    quality_score = 100 * (
                        0.45 * relative_heat
                        + 0.25 * engagement
                        + 0.15 * upvote_ratio
                        + 0.15 * freshness
                    )

                    score_pct = _percentile(scores, latest_score)
                    comments_pct = _percentile(comments, latest_comments)
                    # Outlier guard: bài thực sự nổi bật (>= 200 upvote) không bao
                    # giờ bị hạ hạng do baseline sub, kể cả khi sub đó nhiều bài to.
                    if latest_score >= 200:
                        score_pct = max(score_pct, 0.9)

                    conn.execute(
                        """
                        INSERT INTO mart_post_quality (
                            post_id, computed_at, window_hours, subreddit_id,
                            sub_median_score, sub_median_comments, sub_post_count,
                            score_percentile, comments_percentile,
                            score_ratio, comments_ratio, engagement_ratio,
                            upvote_ratio, quality_score
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(post_id) DO UPDATE SET
                            computed_at = excluded.computed_at,
                            window_hours = excluded.window_hours,
                            subreddit_id = excluded.subreddit_id,
                            sub_median_score = excluded.sub_median_score,
                            sub_median_comments = excluded.sub_median_comments,
                            sub_post_count = excluded.sub_post_count,
                            score_percentile = excluded.score_percentile,
                            comments_percentile = excluded.comments_percentile,
                            score_ratio = excluded.score_ratio,
                            comments_ratio = excluded.comments_ratio,
                            engagement_ratio = excluded.engagement_ratio,
                            upvote_ratio = excluded.upvote_ratio,
                            quality_score = excluded.quality_score
                        """,
                        (
                            post["post_id"], now, hours, sub_id,
                            stats["median_score"], stats["median_comments"], stats["count"],
                            round(score_pct, 4), round(comments_pct, 4),
                            round(score_ratio, 4), round(comments_ratio, 4),
                            round(engagement_ratio, 4), round(upvote_ratio, 4),
                            round(quality_score, 2),
                        ),
                    )
                    stored += 1

        return {
            "posts": stored,
            "subreddits": len(sub_stats),
            "window_hours": hours,
        }
    finally:
        conn.close()
