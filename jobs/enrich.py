"""Bounded evidence enrichment for high-value Reddit signals.

The job deliberately limits external I/O to Reddit's authenticated API.  The
old article fetcher was removed together with its SSRF, redirect, robots and
size guards; rebuilding a generic URL fetch inline would make this operational
job unsafe.  Article text already present in SQLite remains available to the
analysis layer.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from reddit_crawler import RedditClient, crawl
from reddit_crawler.analytics import PERIOD_SECONDS, trending_posts
from reddit_crawler.auth import DEFAULT_CLIENT_ID, TokenManager
from reddit_crawler.resources import extract_resources_from_text
from reddit_crawler.storage import Storage

log = logging.getLogger("jobs.enrich")

DEFAULT_USER_AGENT = os.environ.get(
    "REDDIT_USER_AGENT",
    "python:news-aggregator-research:v0.1 (by /u/your_username)",
)

_RESOURCE_COLUMNS = {
    "resource_id", "post_id", "comment_id", "platform_id", "url", "domain",
    "resource_type", "title", "description", "context_snippet", "author_name",
    "score", "extracted_at",
}


def make_reddit_client() -> RedditClient:
    token_manager = TokenManager(
        user_agent=DEFAULT_USER_AGENT,
        client_id=os.environ.get("REDDIT_CLIENT_ID", DEFAULT_CLIENT_ID),
    )
    return RedditClient(user_agent=DEFAULT_USER_AGENT, token_manager=token_manager)


def _short_id(value: str | None) -> str | None:
    if not value:
        return None
    return value.split("_", 1)[-1]


def _comment_state(store: Storage, post_id: str) -> tuple[int, str | None, float | None, int]:
    """Returns (db_comment_count, status, attempted_at, reddit_num_comments)."""
    row = store.conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM fact_comment WHERE post_id=?) AS comment_count,
            es.status,
            es.attempted_at,
            COALESCE(p.num_comments, 0) AS reddit_num_comments
        FROM (SELECT 1)
        LEFT JOIN enrichment_state es ON es.post_id=? AND es.kind='comments'
        LEFT JOIN fact_post p ON p.post_id=?
        """,
        (post_id, post_id, post_id),
    ).fetchone()
    return int(row[0] or 0), row[1], row[2], int(row[3] or 0)


_STALE_COMMENT_MULTIPLIER = 3.0   # re-enrich nếu Reddit có gấp 3x comment trong DB
_STALE_COMMENT_MIN_GAP   = 10    # hoặc nếu tuyệt đối hơn 10 comment mới


def _should_fetch_comments(
    comment_count: int,
    status: str | None,
    attempted_at: float | None,
    *,
    retry_after_hours: float,
    reddit_num_comments: int = 0,
) -> tuple[bool, str]:
    if status == "success":
        # Re-enrich nếu bài đã viral sau khi crawl lần đầu:
        # Reddit báo có nhiều comment hơn đáng kể so với DB.
        gap = reddit_num_comments - comment_count
        if gap >= _STALE_COMMENT_MIN_GAP and (
            comment_count == 0 or reddit_num_comments >= comment_count * _STALE_COMMENT_MULTIPLIER
        ):
            return True, "stale-comments"
        return False, "already-complete"
    if status in {"empty", "error"} and attempted_at:
        retry_after = max(0.0, retry_after_hours) * 3600
        if time.time() - attempted_at < retry_after:
            return False, "retry-deferred"
        return True, "retry"
    if comment_count > 0:
        return False, "already-present"
    return True, "missing"


def _store_resources_for_post(store: Storage, post_id: str) -> int:
    post = store.conn.execute(
        "SELECT post_id, author_name, selftext, score FROM fact_post WHERE post_id=?",
        (post_id,),
    ).fetchone()
    if not post:
        raise RuntimeError(f"Không tìm thấy post {post_id} khi extract resource")

    sources: list[tuple[str, str | None, str | None, int]] = []
    if post[2]:
        sources.append((post[2], None, post[1], int(post[3] or 0)))
    sources.extend(
        (row[1], row[0], row[2], int(row[3] or 0))
        for row in store.conn.execute(
            """
            SELECT comment_id, body, author_name, score
            FROM fact_comment
            WHERE post_id=? AND body IS NOT NULL AND body LIKE '%http%'
            """,
            (post_id,),
        ).fetchall()
    )

    extracted = 0
    for text, comment_id, author_name, score in sources:
        for resource in extract_resources_from_text(
            text,
            post_id=post_id,
            comment_id=comment_id,
            author_name=author_name,
            score=score,
        ):
            # The current star schema intentionally has no confidence column.
            # Keep only persisted fields so extraction remains schema-compatible.
            store.upsert_extracted_resource({
                key: value for key, value in resource.items() if key in _RESOURCE_COLUMNS
            })
            extracted += 1
    return extracted


def _backlog_candidates(
    store: Storage,
    *,
    kind: str,
    limit: int,
    retry_after_hours: float,
    stream: str = "all",
) -> list[dict[str, Any]]:
    """Return due work across the safe post corpus with optional stream filter."""
    retry_cutoff = time.time() - max(0.0, retry_after_hours) * 3600
    now = time.time()
    stream_filter = ""
    if stream == "new":
        stream_filter = f"AND COALESCE(p.created_utc, 0) >= {now - 86400}"
    elif stream == "hot":
        stream_filter = f"AND (COALESCE(p.num_comments, 0) >= 10 OR COALESCE(p.score, 0) >= 20)"

    comment_due = """
        COALESCE(p.num_comments, 0) > 0
        AND (
            comments.status IS NULL
            OR (
                comments.status IN ('error', 'running')
                AND COALESCE(comments.attempted_at, 0) <= ?
            )
        )
    """
    resource_due = """
        resources.status IS NULL
        OR (
            resources.status IN ('error', 'running')
            AND COALESCE(resources.attempted_at, 0) <= ?
        )
    """
    # Bài đã success nhưng Reddit báo có nhiều comment hơn đáng kể → stale
    stale_comment_due = f"""
        comments.status = 'success'
        AND COALESCE(p.num_comments, 0) >= (
            SELECT COUNT(*) FROM fact_comment fc WHERE fc.post_id = p.post_id
        ) * {_STALE_COMMENT_MULTIPLIER}
        AND COALESCE(p.num_comments, 0) - (
            SELECT COUNT(*) FROM fact_comment fc WHERE fc.post_id = p.post_id
        ) >= {_STALE_COMMENT_MIN_GAP}
    """
    due_parts: list[str] = []
    due_parameters: list[float] = []
    if kind in {"comments", "both"}:
        due_parts.append(f"({comment_due})")
        due_parameters.append(retry_cutoff)
        due_parts.append(f"({stale_comment_due})")
    if kind in {"resources", "both"}:
        due_parts.append(f"({resource_due})")
        due_parameters.append(retry_cutoff)

    rows = store.conn.execute(
        f"""
        SELECT
            p.post_id,
            COALESCE(s.display_name, '') AS subreddit,
            CASE WHEN ({comment_due}) OR ({stale_comment_due}) THEN 1 ELSE 0 END AS comment_due,
            CASE WHEN {resource_due} THEN 1 ELSE 0 END AS resource_due
        FROM fact_post p
        LEFT JOIN dim_subreddit s ON s.subreddit_id = p.subreddit_id
        LEFT JOIN enrichment_state comments
          ON comments.post_id = p.post_id AND comments.kind = 'comments'
        LEFT JOIN enrichment_state resources
          ON resources.post_id = p.post_id AND resources.kind = 'resources'
        WHERE COALESCE(p.over_18, 0) = 0
          {stream_filter}
          AND ({' OR '.join(due_parts)})
        ORDER BY
          CASE WHEN EXISTS (
              SELECT 1 FROM fact_comment c
              WHERE c.post_id = p.post_id
                AND c.body IS NOT NULL
                AND TRIM(c.body) NOT IN ('', '[deleted]', '[removed]')
          ) THEN 0 WHEN COALESCE(p.num_comments, 0) > 0 THEN 1 ELSE 2 END,
          COALESCE(p.num_comments, 0) DESC,
          COALESCE(p.created_utc, 0) DESC,
          p.post_id
        LIMIT ?
        """,
        (
            retry_cutoff,
            retry_cutoff,
            *due_parameters,
            max(1, min(int(limit), 200)),
        ),
    ).fetchall()
    return [
        {
            "post_id": row[0],
            "subreddit": row[1] or None,
            "_comment_due": bool(row[2]),
            "_resource_due": bool(row[3]),
        }
        for row in rows
    ]


def run_enrichment(
    db_path: str = "reddit.db",
    *,
    raw_dir: str | None = "raw",
    period: str = "day",
    limit: int = 20,
    depth: int | None = 4,
    kind: str = "both",
    retry_after_hours: float = 6,
    client: RedditClient | None = None,
    backlog: bool = False,
    stream: str = "all",
) -> dict[str, Any]:
    """Enrich a bounded set of trending posts with comments and resources."""
    if period not in PERIOD_SECONDS:
        raise ValueError(f"period không hợp lệ: {period}")
    if kind not in {"comments", "resources", "both"}:
        raise ValueError("kind phải là comments, resources hoặc both")

    bounded_limit = max(0, min(int(limit), 200))
    stats: dict[str, Any] = {
        "period": period,
        "scope": f"backlog-{stream}" if backlog else "trending",
        "candidates": 0,
        "comment_posts_attempted": 0,
        "comment_posts_enriched": 0,
        "comment_posts_skipped": 0,
        "retry_deferred": 0,
        "comments_fetched": 0,
        "resource_posts_scanned": 0,
        "resource_posts_skipped": 0,
        "resources_extracted": 0,
        "failed": 0,
        "errors": [],
    }
    if bounded_limit == 0:
        return stats
    store = Storage(db_path, raw_dir)
    reddit_client = client
    try:
        candidates = (
            _backlog_candidates(
                store,
                kind=kind,
                limit=bounded_limit,
                retry_after_hours=retry_after_hours,
                stream=stream,
            )
            if backlog
            else trending_posts(
                db_path,
                period=period,
                limit=max(20, min(bounded_limit * 4, 200)),
            )[:bounded_limit]
        )
        stats["candidates"] = len(candidates)
        for item in candidates:
            post_id = item["post_id"]
            if kind in {"comments", "both"}:
                if backlog and not item.get("_comment_due"):
                    stats["comment_posts_skipped"] += 1
                else:
                    count, status, attempted_at, reddit_num = _comment_state(store, post_id)
                    should_fetch, reason = _should_fetch_comments(
                        count,
                        status,
                        attempted_at,
                        retry_after_hours=retry_after_hours,
                        reddit_num_comments=reddit_num,
                    )
                    if not should_fetch:
                        stats["comment_posts_skipped"] += 1
                        if reason == "retry-deferred":
                            stats["retry_deferred"] += 1
                            log.warning("Trì hoãn retry comments cho post %s", post_id)
                        elif count > 0 and status != "success":
                            store.set_enrichment_state(post_id, "comments", "success")
                            store.commit()
                    else:
                        stats["comment_posts_attempted"] += 1
                        try:
                            if reddit_client is None:
                                reddit_client = make_reddit_client()
                            post, comments = crawl.fetch_post_with_comments(
                                reddit_client,
                                post_id,
                                subreddit=item.get("subreddit"),
                                sort="top",
                                depth=depth,
                                limit=200,
                                resolve_more=False,
                            )
                            subreddit_id = _short_id(post.get("subreddit_id"))
                            store.upsert_post(post, subreddit_id=subreddit_id)
                            for comment in comments:
                                store.upsert_comment(
                                    comment,
                                    post_id=post_id,
                                    subreddit_id=subreddit_id,
                                )
                            store.set_enrichment_state(
                                post_id,
                                "comments",
                                "success" if comments else "empty",
                            )
                            store.commit()
                            stats["comment_posts_enriched"] += 1
                            stats["comments_fetched"] += len(comments)
                        except Exception as exc:
                            message = f"{type(exc).__name__}: {str(exc)[:300]}"
                            # A post is one persistence unit.  Never keep a prefix of
                            # its comment tree and later mistake that partial write
                            # for a completed enrichment.
                            store.conn.rollback()
                            store.set_enrichment_state(post_id, "comments", "error", message)
                            store.commit()
                            stats["failed"] += 1
                            stats["errors"].append(f"{post_id}: {message}")
                            log.warning("Comments lỗi ở post %s: %s", post_id, message)

            if kind in {"resources", "both"} and (
                not backlog or item.get("_resource_due")
            ):
                try:
                    extracted = _store_resources_for_post(store, post_id)
                    store.set_enrichment_state(
                        post_id,
                        "resources",
                        "success" if extracted else "empty",
                    )
                    store.commit()
                    stats["resource_posts_scanned"] += 1
                    stats["resources_extracted"] += extracted
                except Exception as exc:
                    message = f"{type(exc).__name__}: {str(exc)[:300]}"
                    store.set_enrichment_state(post_id, "resources", "error", message)
                    store.commit()
                    stats["failed"] += 1
                    stats["errors"].append(f"{post_id}/resources: {message}")
                    log.warning("Resource extraction lỗi ở post %s: %s", post_id, message)
            elif kind in {"resources", "both"}:
                stats["resource_posts_skipped"] += 1
    finally:
        store.close()
    return stats
