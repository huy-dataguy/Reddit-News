"""Read-only FastAPI backend and dashboard for Reddit Radar."""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from reddit_crawler.analytics import (
    DOMAIN_META,
    PERIOD_SECONDS,
    canonical_domain_id,
    database_stats,
    post_detail,
    trending_posts,
)
from reddit_crawler.config import load_dotenv
from reddit_crawler.resources import get_top_extracted_resources

load_dotenv()

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
DB_PATH = os.environ.get("REDDIT_DB_PATH", "reddit.db")

app = FastAPI(title="Reddit Radar", version="1.0.0")
if (DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


def _connect_readonly() -> sqlite3.Connection:
    path = Path(DB_PATH).resolve()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
    ).fetchone() is not None


def _table_count(conn: sqlite3.Connection, table: str, where: str = "") -> int:
    if not _table_exists(conn, table):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0])


def _parse_payload(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _is_ai_provider(provider: str | None) -> bool:
    value = (provider or "").lower()
    return bool(value) and not value.startswith("local") and value not in {"none", "unknown"}


def _provider_label(provider: str | None, model: str | None) -> str:
    if not provider or provider == "none":
        return "Chưa có briefing"
    if (provider or "").lower().startswith("local"):
        return "Trích xuất local · chưa xác minh"
    return f"{provider}{f' · {model}' if model else ''}"


def _analysis_select(table: str, version: str) -> str:
    return f"""
        SELECT pa.post_id, pa.provider, pa.model, pa.payload_json,
               pa.generated_at, pa.comment_count, '{version}' AS analysis_version,
               p.title, p.url, p.domain AS source_domain, p.permalink,
               p.created_utc, p.score, p.num_comments,
               s.display_name AS subreddit
        FROM {table} pa
        JOIN fact_post p ON p.post_id = pa.post_id
        LEFT JOIN dim_subreddit s ON s.subreddit_id = p.subreddit_id
        WHERE pa.status = 'success' AND pa.payload_json IS NOT NULL
    """


def _unified_knowledge_items(
    conn: sqlite3.Connection, post_id: str | None = None,
) -> list[dict[str, Any]]:
    selects = []
    if _table_exists(conn, "ai_post_analysis_v2"):
        selects.append(_analysis_select("ai_post_analysis_v2", "v2"))
    if _table_exists(conn, "ai_post_analysis"):
        selects.append(_analysis_select("ai_post_analysis", "v1"))
    if not selects:
        return []

    rows = conn.execute(" UNION ALL ".join(selects)).fetchall()
    selected: dict[str, tuple[int, float, dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        if post_id and item["post_id"] != post_id:
            continue
        payload = _parse_payload(item.pop("payload_json", None))
        if payload is None or not any(payload.get(field) for field in (
            "topic", "author_summary", "author_goal", "context", "problem_context",
            "verdict", "community_consensus", "key_points", "opinion_groups",
            "action_items", "learning_points",
        )):
            continue
        is_ai = _is_ai_provider(item.get("provider"))
        version = item["analysis_version"]
        priority = (
            40 if version == "v2" and is_ai else
            30 if version == "v1" and is_ai else
            20 if version == "v2" else 10
        )
        generated_at = float(item.get("generated_at") or 0)
        current = selected.get(item["post_id"])
        if current and (current[0], current[1]) >= (priority, generated_at):
            continue

        domain_id = canonical_domain_id(payload.get("domain"), item)
        domain_meta = DOMAIN_META.get(domain_id, DOMAIN_META.get("other", {}))
        payload = {
            **payload,
            "provider": item.get("provider"),
            "model": item.get("model"),
            "generated_at": payload.get("generated_at") or generated_at,
        }
        item.update({
            "analysis": payload,
            "domain_id": domain_id,
            "domain_name": domain_meta.get("name", domain_id),
            "is_ai": is_ai,
            "provider_label": _provider_label(item.get("provider"), item.get("model")),
            "reddit_url": (
                f"https://www.reddit.com{item['permalink']}" if item.get("permalink") else None
            ),
        })
        selected[item["post_id"]] = (priority, generated_at, item)

    items = [value[2] for value in selected.values()]
    items.sort(
        key=lambda value: (
            bool(value.get("is_ai")),
            float(value.get("generated_at") or 0),
            float(value.get("created_utc") or 0),
        ),
        reverse=True,
    )
    return items


def _knowledge_feed_data(
    *, domain: str | None, query: str, limit: int, offset: int,
) -> dict[str, Any]:
    conn = _connect_readonly()
    try:
        items = _unified_knowledge_items(conn)
    finally:
        conn.close()

    normalized_query = query.strip().casefold()
    if normalized_query:
        items = [
            item for item in items
            if normalized_query in " ".join((
                str(item.get("title") or ""),
                str(item.get("subreddit") or ""),
                str(item.get("domain_name") or ""),
                json.dumps(item.get("analysis") or {}, ensure_ascii=False),
            )).casefold()
        ]

    domain_counts: dict[str, int] = {}
    for item in items:
        key = item.get("domain_id") or "other"
        domain_counts[key] = domain_counts.get(key, 0) + 1
    domains = [
        {"id": key, **DOMAIN_META.get(key, {"name": key, "description": ""}), "count": count}
        for key, count in sorted(domain_counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]

    if domain and domain != "all":
        items = [item for item in items if item.get("domain_id") == domain]
    total = len(items)
    page = items[offset:offset + limit]
    return {
        "count": len(page),
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(page) < total,
        "domains": domains,
        "items": page,
    }


def _latest_digest(conn: sqlite3.Connection, period: str) -> dict[str, Any] | None:
    if not _table_exists(conn, "ai_digest"):
        return None
    row = conn.execute(
        """
        SELECT digest_id, period, window_start, window_end, provider, model, status,
               title, executive_summary, payload_json, source_count, input_tokens,
               output_tokens, generated_at, error
        FROM ai_digest
        WHERE status='success'
        ORDER BY CASE WHEN period=? THEN 0 ELSE 1 END, generated_at DESC
        LIMIT 1
        """,
        (period,),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["payload"] = _parse_payload(result.pop("payload_json", None)) or {}
    result["is_ai"] = _is_ai_provider(result.get("provider"))
    result["provider_label"] = _provider_label(result.get("provider"), result.get("model"))
    result["requested_period_match"] = result.get("period") == period
    return result


def _latest_number(conn: sqlite3.Connection, queries: list[str]) -> float | None:
    values = []
    for query in queries:
        row = conn.execute(query).fetchone()
        if row and row[0] is not None:
            values.append(float(row[0]))
    return max(values) if values else None


def _stage(
    *, count: int, latest_at: float | None, threshold: float,
    provider: str | None = None, model: str | None = None,
    version: str | None = None, force_degraded: bool = False,
) -> dict[str, Any]:
    age = max(0.0, time.time() - latest_at) if latest_at else None
    freshness = "missing" if latest_at is None else "fresh" if age <= threshold else "stale"
    status = "healthy" if freshness == "fresh" and count > 0 and not force_degraded else "degraded"
    result: dict[str, Any] = {
        "status": status,
        "freshness": freshness,
        "count": count,
        "last_success_at": latest_at,
        "age_seconds": round(age, 1) if age is not None else None,
    }
    if provider is not None:
        result.update({
            "provider": provider,
            "model": model,
            "version": version,
            "is_ai": _is_ai_provider(provider),
            "provider_label": _provider_label(provider, model),
        })
    return result


def _health_snapshot() -> dict[str, Any]:
    conn = _connect_readonly()
    try:
        counts = {
            "posts": _table_count(conn, "fact_post"),
            "comments": _table_count(conn, "fact_comment"),
            "commented_posts": _table_count(conn, "fact_comment", "GROUP BY post_id"),
            "articles": _table_count(conn, "fact_article_content", "WHERE status='success'"),
            "resources": _table_count(conn, "fact_extracted_resource"),
            "analyses_v1": _table_count(conn, "ai_post_analysis", "WHERE status='success'"),
            "analyses_v2": _table_count(conn, "ai_post_analysis_v2", "WHERE status='success'"),
            "digests": _table_count(conn, "ai_digest", "WHERE status='success'"),
        }
        if _table_exists(conn, "fact_comment"):
            counts["commented_posts"] = int(conn.execute(
                "SELECT COUNT(DISTINCT post_id) FROM fact_comment",
            ).fetchone()[0])

        analysis_items = _unified_knowledge_items(conn)
        counts["analyses"] = len(analysis_items)
        counts["analyses_ai"] = sum(bool(item.get("is_ai")) for item in analysis_items)
        counts["analyses_local"] = counts["analyses"] - counts["analyses_ai"]

        collector_latest = _latest_number(conn, [
            "SELECT MAX(fetched_at) FROM fact_post",
            "SELECT MAX(observed_at) FROM fact_post_metrics",
            "SELECT MAX(updated_at) FROM crawl_state",
        ])
        enrichment_latest = _latest_number(conn, [
            "SELECT MAX(fetched_at) FROM fact_comment",
            "SELECT MAX(fetched_at) FROM fact_article_content WHERE status='success'",
            "SELECT MAX(extracted_at) FROM fact_extracted_resource",
            "SELECT MAX(completed_at) FROM enrichment_state WHERE status='success'",
        ])

        latest_analysis = analysis_items[0] if analysis_items else None
        latest_digest = _latest_digest(conn, "3h")

        evidence_count = counts["comments"] + counts["articles"] + counts["resources"]
        stages = {
            "collector": _stage(
                count=counts["posts"], latest_at=collector_latest, threshold=2 * 3600,
            ),
            "enrichment": _stage(
                count=evidence_count, latest_at=enrichment_latest, threshold=24 * 3600,
            ),
            "analysis": _stage(
                count=counts["analyses"],
                latest_at=float(latest_analysis["generated_at"]) if latest_analysis else None,
                threshold=24 * 3600,
                provider=latest_analysis["provider"] if latest_analysis else None,
                model=latest_analysis["model"] if latest_analysis else None,
                version=latest_analysis["analysis_version"] if latest_analysis else None,
                force_degraded=bool(
                    latest_analysis and not _is_ai_provider(latest_analysis["provider"])
                ),
            ),
            "digest": _stage(
                count=counts["digests"],
                latest_at=float(latest_digest["generated_at"]) if latest_digest else None,
                threshold=8 * 3600,
                provider=latest_digest["provider"] if latest_digest else None,
                model=latest_digest["model"] if latest_digest else None,
                force_degraded=bool(latest_digest and not latest_digest["is_ai"]),
            ),
        }
    finally:
        conn.close()
    status = "healthy" if all(stage["status"] == "healthy" for stage in stages.values()) else "degraded"
    return {
        "status": status,
        "database": DB_PATH,
        "checked_at": time.time(),
        "counts": counts,
        "stages": stages,
    }


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(DIST / "index.html")


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    return FileResponse(DIST / "sw.js", media_type="application/javascript")


@app.get("/api/health")
def health() -> dict[str, Any]:
    try:
        return _health_snapshot()
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail=f"Database chưa sẵn sàng: {exc}") from exc


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    values = database_stats(DB_PATH)
    snapshot = _health_snapshot()
    values.update({
        "ai_post_analysis_v2": snapshot["counts"]["analyses_v2"],
        "ai_analysis_total": snapshot["counts"]["analyses"],
        "ai_analysis_llm": snapshot["counts"]["analyses_ai"],
        "ai_analysis_local": snapshot["counts"]["analyses_local"],
        "pipeline_status": snapshot["status"],
    })
    return values


@app.get("/api/trending")
def trending(
    period: str = Query("day"),
    limit: int = Query(30, ge=1, le=100),
) -> dict[str, Any]:
    if period not in PERIOD_SECONDS:
        raise HTTPException(status_code=400, detail=f"period hợp lệ: {', '.join(PERIOD_SECONDS)}")
    items = trending_posts(DB_PATH, period=period, limit=limit)
    conn = _connect_readonly()
    try:
        knowledge = {item["post_id"]: item for item in _unified_knowledge_items(conn)}
    finally:
        conn.close()
    for item in items:
        analysis = knowledge.get(item["post_id"])
        if analysis:
            item.update({key: analysis[key] for key in (
                "analysis", "analysis_version", "provider", "model", "is_ai",
                "provider_label", "domain_id", "domain_name",
            )})
    domain_counts: dict[str, int] = {}
    for item in items:
        key = item.get("domain_id") or "other"
        domain_counts[key] = domain_counts.get(key, 0) + 1
    return {
        "period": period,
        "window_seconds": PERIOD_SECONDS[period],
        "generated_at": time.time(),
        "domains": [
            {"id": key, **meta, "count": domain_counts.get(key, 0)}
            for key, meta in DOMAIN_META.items() if domain_counts.get(key, 0)
        ],
        "items": items,
    }


@app.get("/api/digests/latest")
def digest(period: str = Query("3h")) -> dict[str, Any]:
    if period not in PERIOD_SECONDS:
        raise HTTPException(status_code=400, detail=f"period hợp lệ: {', '.join(PERIOD_SECONDS)}")
    conn = _connect_readonly()
    try:
        item = _latest_digest(conn, period)
    finally:
        conn.close()
    if not item:
        raise HTTPException(status_code=404, detail="Chưa có briefing cho chu kỳ này")
    return item


@app.get("/api/today")
def today(
    period: str = Query("day"),
    limit: int = Query(8, ge=1, le=100),
) -> dict[str, Any]:
    if period not in PERIOD_SECONDS:
        raise HTTPException(status_code=400, detail=f"period hợp lệ: {', '.join(PERIOD_SECONDS)}")
    signals = trending_posts(DB_PATH, period=period, limit=max(20, limit * 3))
    conn = _connect_readonly()
    try:
        knowledge = _unified_knowledge_items(conn)
        digest_item = _latest_digest(conn, period)
    finally:
        conn.close()
    knowledge_by_id = {item["post_id"]: item for item in knowledge}

    highlights = []
    seen = set()
    for signal in signals:
        analysis = knowledge_by_id.get(signal["post_id"])
        if not analysis:
            continue
        highlights.append({
            **analysis,
            "trend_score": signal.get("trend_score"),
            "composite_value_score": signal.get("composite_value_score"),
            "score_velocity": signal.get("score_velocity"),
            "quality_score": signal.get("quality_score"),
            "score_ratio": signal.get("score_ratio"),
            "comments_ratio": signal.get("comments_ratio"),
            "engagement_ratio": signal.get("engagement_ratio"),
            "upvote_ratio": signal.get("upvote_ratio"),
            "score_percentile": signal.get("score_percentile"),
            "comments_percentile": signal.get("comments_percentile"),
        })
        seen.add(signal["post_id"])
    highlights.sort(
        key=lambda item: (
            bool(item.get("is_ai")),
            float(item.get("composite_value_score") or 0),
            float(item.get("generated_at") or 0),
        ),
        reverse=True,
    )
    for item in knowledge:
        if item["post_id"] not in seen:
            highlights.append(item)
    highlights = highlights[:limit]

    for signal in signals:
        analysis = knowledge_by_id.get(signal["post_id"])
        if analysis:
            signal.update({key: analysis[key] for key in (
                "analysis", "analysis_version", "provider", "model", "is_ai",
                "provider_label", "domain_id", "domain_name",
            )})
    provider = {
        "name": digest_item["provider"] if digest_item else "none",
        "model": digest_item["model"] if digest_item else None,
        "is_ai": bool(digest_item and digest_item["is_ai"]),
        "label": digest_item["provider_label"] if digest_item else "Chưa có briefing",
    }
    provisional = bool(
        not digest_item
        or not digest_item["is_ai"]
        or not digest_item["requested_period_match"]
    )
    if not digest_item:
        message = "Briefing LLM chưa sẵn sàng; đang hiển thị các phân tích và tín hiệu tốt nhất."
    elif not digest_item["requested_period_match"]:
        message = (
            f"Chưa có briefing {period}; đang hiển thị briefing {digest_item['period']} gần nhất."
        )
    elif not digest_item["is_ai"]:
        message = "Briefing hiện tại là bản local chưa được LLM xác minh."
    else:
        message = None
    return {
        "period": period,
        "generated_at": time.time(),
        "status": "provisional" if provisional else "ready",
        "provisional": provisional,
        "provider": provider,
        "message": message,
        "digest": digest_item,
        "highlights": highlights,
        "signals": signals[:limit],
    }


@app.get("/api/knowledge/feed")
def knowledge_feed(
    domain: str | None = Query(None),
    q: str = Query("", max_length=200),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return _knowledge_feed_data(domain=domain, query=q, limit=limit, offset=offset)


@app.get("/api/knowledge/feed/v2")
def knowledge_feed_v2(
    domain: str | None = Query(None),
    q: str = Query("", max_length=200),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Compatibility alias; the canonical feed already merges V2 and V1."""
    return _knowledge_feed_data(domain=domain, query=q, limit=limit, offset=offset)


@app.get("/api/knowledge/{post_id}")
def knowledge_detail(post_id: str) -> dict[str, Any]:
    conn = _connect_readonly()
    try:
        items = _unified_knowledge_items(conn, post_id)
    finally:
        conn.close()
    if not items:
        raise HTTPException(status_code=404, detail="Chưa có bản đúc kết cho bài viết này")
    return items[0]


@app.get("/api/knowledge/{post_id}/v2")
def knowledge_detail_v2(post_id: str) -> dict[str, Any]:
    """Compatibility alias for clients that previously requested the V2 route."""
    return knowledge_detail(post_id)


@app.get("/api/posts/{post_id}")
def detail(post_id: str) -> dict[str, Any]:
    item = post_detail(DB_PATH, post_id)
    if not item:
        raise HTTPException(status_code=404, detail="Không tìm thấy post")
    conn = _connect_readonly()
    try:
        analyses = _unified_knowledge_items(conn, post_id)
    finally:
        conn.close()
    if analyses:
        analysis = analyses[0]
        item.update({key: analysis[key] for key in (
            "analysis", "analysis_version", "provider", "model", "is_ai",
            "provider_label", "domain_id", "domain_name",
        )})
    return item


@app.get("/api/posts/{post_id}/export")
def export_post(post_id: str, format: str = Query("markdown")) -> dict[str, str]:
    if format != "markdown":
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ format=markdown")
    detail_data = post_detail(DB_PATH, post_id, comment_limit=0) or {}
    if not detail_data:
        raise HTTPException(status_code=404, detail="Không tìm thấy post")
    created = detail_data.get("created_utc") or 0
    date_str = (
        dt.datetime.fromtimestamp(created, dt.timezone.utc).strftime("%Y-%m-%d")
        if created else ""
    )
    markdown = "\n\n".join(filter(None, [
        f"# {detail_data.get('title') or 'Untitled'}\n",
        detail_data.get("selftext"),
        f"---\nNguồn: {detail_data.get('reddit_url') or ''}",
        f"Điểm: {detail_data.get('score') or 0} | "
        f"Bình luận: {detail_data.get('num_comments') or 0} | Ngày: {date_str}",
    ]))
    return {"post_id": post_id, "format": format, "markdown": markdown}


@app.get("/api/buzz")
def ai_buzz_endpoint(period: str = Query("month")) -> dict[str, Any]:
    items = trending_posts(DB_PATH, period=period, limit=10)

    period_label = "THÁNG 6/2026" if period == "month" else "TUẦN NÀY"
    stories = []
    bulletin_lines = [
        f"★ BẢN TIN CÔNG NGHỆ {period_label} | AI BUZZ {period_label}\n",
        "Cùng Reddit Radar điểm qua một số bản tin công nghệ nổi bật về Trí tuệ nhân tạo (AI) và Lập trình:\n",
    ]

    badges = ["🔹", "🔥", "⚡", "🚀", "💡", "🛡️", "🤖"]
    for i, item in enumerate(items[:6]):
        badge = badges[i % len(badges)]
        title = item.get("analysis", {}).get("topic") or item.get("title") or "Hot Tech Story"
        summary = item.get("analysis", {}).get("verdict") or item.get("analysis", {}).get("summary") or "Thảo luận nổi bật với lượng tương tác lớn từ cộng đồng."
        sub = item.get("subreddit") or "tech"
        score = item.get("latest_score") or item.get("score") or 0

        stories.append({
            "badge": badge, "headline": title, "snippet": summary[:220],
            "post_id": item.get("post_id"), "subreddit": sub, "score": score,
        })
        bulletin_lines.append(f"{badge} **{title}**\n   {summary[:240]}\n")

    bulletin_lines.append("\n👉 Anh em ấn tượng nhất với tin tức nào? Để lại ý kiến bàn luận bên dưới nhé!\n#AIBuzz #RedditRadar #TechNews")

    return {
        "period": period,
        "title": f"★ BẢN TIN CÔNG NGHỆ {period_label} | AI BUZZ",
        "full_bulletin_text": "\n".join(bulletin_lines),
        "stories": stories,
    }


@app.get("/api/social/roundup")
def social_roundup_endpoint(
    hours: float = Query(3, ge=0.5, le=24 * 7),
    top: int = Query(3, ge=1, le=10),
    refresh: bool = Query(False),
) -> dict[str, Any]:
    """Đọc roundup đã tạo sẵn — KHÔNG gọi LLM từ web request.

    Nếu chưa có cụm khớp giờ hiện tại, trả về các cụm mới nhất làm dữ liệu
    gần nhất (timer roundup chạy mỗi giờ sẽ tạo cụm mới).
    """
    import json as _json

    conn = _connect_readonly()
    try:
        if _table_exists(conn, "ai_social_roundup"):
            if refresh:
                rows = []
            else:
                rows = conn.execute(
                    """
                    SELECT cluster_id, hour_start, source_post_ids, total_score,
                           total_comments, n_posts, topic_vi, domain_id, provider,
                           model, title, full_post_text, generated_at
                    FROM ai_social_roundup
                    WHERE status = 'success'
                    ORDER BY hour_start DESC, total_score + 5 * total_comments DESC
                    LIMIT ?
                    """,
                    (top,),
                ).fetchall()
            if rows:
                all_post_ids = sorted({pid for row in rows for pid in _json.loads(row["source_post_ids"] or "[]")})
                if all_post_ids:
                    post_rows = conn.execute(
                        "SELECT p.post_id, d.display_name AS subreddit "
                        "FROM fact_post p LEFT JOIN dim_subreddit d ON d.subreddit_id = p.subreddit_id "
                        "WHERE p.post_id IN (%s)" % ",".join("?" * len(all_post_ids)),
                        tuple(all_post_ids),
                    ).fetchall()
                else:
                    post_rows = []
                subreddit_by_id = {r["post_id"]: r["subreddit"] for r in post_rows}
                return {
                    "period": f"{int(hours)}h",
                    "count": len(rows),
                    "hour_start": float(rows[0]["hour_start"]),
                    "cached": True,
                    "stale": rows[0]["hour_start"] != math.floor((time.time() - hours * 3600) / 3600) * 3600,
                    "items": [
                        {
                            **dict(row),
                            "source_post_ids": _json.loads(row["source_post_ids"] or "[]"),
                            "source_links": [
                                {"post_id": pid, "subreddit": subreddit_by_id.get(pid)}
                                for pid in _json.loads(row["source_post_ids"] or "[]")
                            ],
                        }
                        for row in rows
                    ],
                }
    finally:
        conn.close()

    return {"period": f"{int(hours)}h", "count": 0, "hour_start": 0, "cached": True, "stale": True, "items": []}


@app.get("/api/resources")
def resources(
    kind: str = Query("all"),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    items = get_top_extracted_resources(DB_PATH, resource_type=kind, limit=limit)
    return {"kind": kind, "count": len(items), "items": items}


@app.get("/api/user/bookmarks")
def get_user_bookmarks() -> dict[str, Any]:
    """Get list of bookmarked post IDs from SQLite database."""
    path = Path(DB_PATH).resolve()
    if not path.exists():
        return {"bookmarks": []}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        if not _table_exists(conn, "user_bookmark"):
            return {"bookmarks": []}
        rows = conn.execute("SELECT post_id FROM user_bookmark ORDER BY saved_at DESC").fetchall()
        return {"bookmarks": [r[0] for r in rows]}
    finally:
        conn.close()


@app.get("/api/user/bookmarks/details")
def get_user_bookmark_details() -> dict[str, Any]:
    """Get detailed post objects for all bookmarked posts."""
    conn = _connect_readonly()
    try:
        if not _table_exists(conn, "user_bookmark"):
            return {"bookmarks": [], "count": 0, "items": []}
        rows = conn.execute("SELECT post_id FROM user_bookmark ORDER BY saved_at DESC").fetchall()
        post_ids = [r[0] for r in rows]
        if not post_ids:
            return {"bookmarks": [], "count": 0, "items": []}

        knowledge_map = {item["post_id"]: item for item in _unified_knowledge_items(conn)}
        items = []
        for pid in post_ids:
            if pid in knowledge_map:
                items.append(knowledge_map[pid])
            else:
                row = conn.execute("""
                    SELECT p.post_id, p.title, p.url, p.domain AS source_domain, p.permalink,
                           p.created_utc, p.score, p.num_comments,
                           s.display_name AS subreddit
                    FROM fact_post p
                    LEFT JOIN dim_subreddit s ON s.subreddit_id = p.subreddit_id
                    WHERE p.post_id = ?
                """, (pid,)).fetchone()
                if row:
                    item = dict(row)
                    domain_id = canonical_domain_id(None, item)
                    domain_meta = DOMAIN_META.get(domain_id, DOMAIN_META.get("other", {}))
                    item.update({
                        "analysis": None,
                        "analysis_version": None,
                        "provider": None,
                        "model": None,
                        "is_ai": False,
                        "provider_label": "Chưa có briefing",
                        "domain_id": domain_id,
                        "domain_name": domain_meta.get("name", domain_id),
                        "reddit_url": (
                            f"https://www.reddit.com{item['permalink']}" if item.get("permalink") else None
                        ),
                    })
                    items.append(item)
        return {"bookmarks": post_ids, "count": len(items), "items": items}
    finally:
        conn.close()



@app.post("/api/user/bookmarks/{post_id}")
def toggle_user_bookmark(post_id: str) -> dict[str, Any]:
    """Save or toggle bookmark in SQLite database."""
    path = Path(DB_PATH).resolve()
    conn = sqlite3.connect(str(path), timeout=10)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_bookmark (
                post_id TEXT PRIMARY KEY,
                saved_at REAL NOT NULL,
                notes TEXT
            )
        """)
        existing = conn.execute("SELECT 1 FROM user_bookmark WHERE post_id=?", (post_id,)).fetchone()
        if existing:
            conn.execute("DELETE FROM user_bookmark WHERE post_id=?", (post_id,))
            saved = False
        else:
            conn.execute("INSERT INTO user_bookmark (post_id, saved_at) VALUES (?, ?)", (post_id, time.time()))
            saved = True
        conn.commit()
        return {"post_id": post_id, "saved": saved}
    finally:
        conn.close()


@app.get("/api/user/read")
def get_user_read_posts() -> dict[str, Any]:
    """Get list of read post IDs from SQLite database."""
    path = Path(DB_PATH).resolve()
    if not path.exists():
        return {"read": []}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        if not _table_exists(conn, "user_read_state"):
            return {"read": []}
        rows = conn.execute("SELECT post_id FROM user_read_state ORDER BY read_at DESC").fetchall()
        return {"read": [r[0] for r in rows]}
    finally:
        conn.close()


@app.post("/api/user/read/{post_id}")
def mark_user_read_post(post_id: str) -> dict[str, Any]:
    """Mark a post as read in SQLite database."""
    path = Path(DB_PATH).resolve()
    conn = sqlite3.connect(str(path), timeout=10)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_read_state (
                post_id TEXT PRIMARY KEY,
                read_at REAL NOT NULL,
                read_count INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            INSERT INTO user_read_state (post_id, read_at, read_count)
            VALUES (?, ?, 1)
            ON CONFLICT(post_id) DO UPDATE SET
                read_at=excluded.read_at,
                read_count=user_read_state.read_count + 1
        """, (post_id, time.time()))
        conn.commit()
        return {"post_id": post_id, "status": "read"}
    finally:
        conn.close()


@app.get("/{full_path:path}", include_in_schema=False, response_model=None)
def spa_fallback(full_path: str) -> FileResponse | Response:
    """Serve the SPA for client-side routes; keep unknown /api paths as 404 JSON."""
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(DIST / "index.html")

