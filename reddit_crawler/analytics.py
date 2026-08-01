"""Truy vấn phân tích phục vụ dashboard và báo cáo.

Điểm ``trend_score`` là baseline minh bạch, chưa dùng ML: kết hợp độ mới,
score/comment hiện tại và vận tốc thay đổi giữa hai snapshot gần nhất.
"""

from __future__ import annotations

import math
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

PERIOD_SECONDS = {
    "3h": 3 * 3600,
    "day": 24 * 3600,
    "week": 7 * 24 * 3600,
    "month": 30 * 24 * 3600,
    "year": 365 * 24 * 3600,
}

DOMAIN_META = {
    "ai_ml": {"name": "AI & Machine Learning", "description": "Model mới, LLM, agent, benchmark, training, inference, AI workflow."},
    "devtools": {"name": "DevTools & Software", "description": "IDE, framework, CLI, library, coding workflow, open-source tooling."},
    "security": {"name": "Security & Privacy", "description": "Lỗ hổng, CVE, exploit, privacy, mã độc, phòng thủ mạng."},
    "infra": {"name": "Infrastructure & Cloud", "description": "Cloud, kubernetes, docker, server, networking, chip, GPU, homelab."},
    "science": {"name": "Science & Research", "description": "Nghiên cứu khoa học, paper, space, biology, physics, breakthrough."},
    "business": {"name": "Business & Policy", "description": "Startup, funding, M&A, regulation, hiring, market trend, IPO."},
    "other": {"name": "Other", "description": "Tín hiệu công nghệ liên ngành khác."},
}

DOMAIN_ALIASES = {
    "ai_models": "ai_ml",
    "data_science": "ai_ml",
    "software_dev": "devtools",
    "cybersecurity": "security",
    "infrastructure": "infra",
    "products": "business",
    "policy_business": "business",
}

LLM_PROVIDERS = {"gemini", "openai"}


def classify_domain(item: dict[str, Any]) -> str:
    text = " ".join(str(item.get(k) or "") for k in ("subreddit", "title", "domain")).lower()
    rules = (
        ("security", r"security|cyber|vulnerab|malware|ransom|cve|privacy|hack|exploit|breach|zero.?day"),
        ("ai_ml", r"\bai\b|llm|model|gpt|claude|gemini|llama|deepseek|qwen|mistral|openai|anthropic|agent|benchmark|training|inference|finetun|rag|embedding|transformer|diffusion|reasoning|codex"),
        ("devtools", r"programming|webdev|developer|github|python|javascript|typescript|rust|software|code|ide|editor|vim|neovim|vscode|package|npm|pip|cargo|cli|api|sdk|framework|react|vue|svelte|nextjs|deno|bun"),
        ("infra", r"homelab|sysadmin|linux|server|cloud|kubernetes|docker|amd|nvidia|gpu|chip|aws|gcp|azure|terraform|ci.?cd|deploy|monitoring|database|postgres|redis|kafka"),
        ("science", r"science|physics|biology|space|research|paper|arxiv|quantum|astronomy|medicine|genomic|crispr"),
        ("business", r"policy|regulat|government|business|funding|acqui|billion|market|company|startup|ipo|hiring|layoff|remote|salary"),
    )
    return next((domain for domain, pattern in rules if re.search(pattern, text)), "other")


def canonical_domain_id(value: Any, item: dict[str, Any]) -> str:
    candidate = DOMAIN_ALIASES.get(str(value or ""), str(value or ""))
    return candidate if candidate in DOMAIN_META else classify_domain(item)


def _decode_analysis(
    payload_json: Any,
    *,
    provider: Any,
    model: Any,
    generated_at: Any,
    version: int,
    item: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        payload = json.loads(payload_json) if payload_json else None
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    meaningful_fields = (
        "topic", "author_summary", "author_goal", "context", "problem_context",
        "verdict", "community_consensus", "key_points", "opinion_groups",
        "action_items", "learning_points",
    )
    if not any(payload.get(field) for field in meaningful_fields):
        return None
    payload.update({
        "domain": canonical_domain_id(payload.get("domain"), item),
        "provider": provider,
        "model": model,
        "generated_at": generated_at,
        "version": version,
    })
    return payload


def _preferred_analysis(item: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[tuple[int, float, dict[str, Any]]] = []
    for version, prefix in ((2, "analysis_v2"), (1, "analysis_v1")):
        payload_json = item.pop(f"{prefix}_json", None)
        provider = item.pop(f"{prefix}_provider", None)
        model = item.pop(f"{prefix}_model", None)
        generated_at = item.pop(f"{prefix}_generated_at", None)
        analysis = _decode_analysis(
            payload_json, provider=provider, model=model, generated_at=generated_at,
            version=version, item=item,
        )
        if analysis:
            is_llm = provider in LLM_PROVIDERS
            priority = (
                40 if version == 2 and is_llm else
                30 if version == 1 and is_llm else
                20 if version == 2 else 10
            )
            candidates.append((priority, float(generated_at or 0), analysis))
    return max(candidates, default=(0, 0.0, None), key=lambda value: value[:2])[2]


def _connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path).resolve()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def trending_posts(
    db_path: str | Path = "reddit.db",
    *,
    period: str = "day",
    limit: int = 30,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Trả các post nổi bật trong cửa sổ thời gian, điểm cao trước."""
    if period not in PERIOD_SECONDS:
        raise ValueError(f"period phải là một trong: {', '.join(PERIOD_SECONDS)}")
    limit = max(1, min(int(limit), 200))
    now = now or time.time()
    cutoff = now - PERIOD_SECONDS[period]
    conn = _connect_readonly(db_path)
    try:
        rows = conn.execute(
            """
            WITH ranked_metrics AS (
                SELECT post_id, observed_at, score, num_comments,
                       ROW_NUMBER() OVER (
                           PARTITION BY post_id ORDER BY observed_at DESC
                       ) AS rn
                FROM fact_post_metrics
            ),
            resource_counts AS (
                SELECT post_id, COUNT(*) AS res_count
                FROM fact_extracted_resource
                GROUP BY post_id
            )
            SELECT p.post_id, p.title, p.selftext, p.url, p.domain, p.permalink, p.is_self,
                   p.created_utc, p.score, p.num_comments, p.upvote_ratio,
                   p.over_18, s.display_name AS subreddit,
                   latest.observed_at AS latest_at,
                   COALESCE(latest.score, p.score, 0) AS latest_score,
                   COALESCE(latest.num_comments, p.num_comments, 0) AS latest_comments,
                   previous.observed_at AS previous_at,
                   previous.score AS previous_score,
                   previous.num_comments AS previous_comments,
                   a.status AS article_status,
                   SUBSTR(a.body_text, 1, 281) AS article_excerpt,
                   m.image_url, m.thumbnail_url,
                   pa2.payload_json AS analysis_v2_json,
                   pa2.provider AS analysis_v2_provider,
                   pa2.model AS analysis_v2_model,
                   pa2.generated_at AS analysis_v2_generated_at,
                   pa1.payload_json AS analysis_v1_json,
                   pa1.provider AS analysis_v1_provider,
                   pa1.model AS analysis_v1_model,
                   pa1.generated_at AS analysis_v1_generated_at,
                   COALESCE(rc.res_count, 0) AS resource_count
            FROM fact_post p
            LEFT JOIN dim_subreddit s ON s.subreddit_id = p.subreddit_id
            LEFT JOIN fact_article_content a ON a.post_id = p.post_id
            LEFT JOIN fact_post_media m ON m.post_id = p.post_id
            LEFT JOIN ai_post_analysis_v2 pa2 ON pa2.post_id = p.post_id AND pa2.status='success'
            LEFT JOIN ai_post_analysis pa1 ON pa1.post_id = p.post_id AND pa1.status='success'
            LEFT JOIN resource_counts rc ON rc.post_id = p.post_id
            LEFT JOIN ranked_metrics latest
                   ON latest.post_id = p.post_id AND latest.rn = 1
            LEFT JOIN ranked_metrics previous
                   ON previous.post_id = p.post_id AND previous.rn = 2
            WHERE p.created_utc >= ? AND COALESCE(p.over_18, 0) = 0
            """,
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    results: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        age_hours = max((now - (item["created_utc"] or now)) / 3600, 0.0)
        metric_hours = 0.0
        score_velocity = comment_velocity = 0.0
        if item["latest_at"] and item["previous_at"]:
            metric_hours = (item["latest_at"] - item["previous_at"]) / 3600
            if metric_hours >= 1 / 60:
                score_velocity = max(
                    ((item["latest_score"] or 0) - (item["previous_score"] or 0))
                    / metric_hours,
                    0.0,
                )
                comment_velocity = max(
                    ((item["latest_comments"] or 0) - (item["previous_comments"] or 0))
                    / metric_hours,
                    0.0,
                )
        recency = 4.0 / (1.0 + age_hours / 6.0)
        trend_score = (
            0.55 * math.log1p(max(item["latest_score"] or 0, 0))
            + 0.8 * math.log1p(max(item["latest_comments"] or 0, 0))
            + 1.5 * math.log1p(score_velocity)
            + 2.2 * math.log1p(comment_velocity)
            + recency
        )
        text = (item.get("selftext") or item.get("article_excerpt") or "").strip()
        analysis = _preferred_analysis(item)
        
        res_count = item.get("resource_count", 0)
        composite_value = (
            trend_score
            + 1.2 * math.log1p(res_count)
            + (1.5 if analysis and analysis.get("provider") in LLM_PROVIDERS else 0.0)
        )
        
        item.update({
            "age_hours": round(age_hours, 2),
            "score_velocity": round(score_velocity, 2),
            "comment_velocity": round(comment_velocity, 2),
            "trend_score": round(trend_score, 3),
            "composite_value_score": round(composite_value, 3),
            "resource_count": res_count,
            "summary": (text[:280] + "…") if len(text) > 280 else text,
            "reddit_url": (
                f"https://www.reddit.com{item['permalink']}" if item.get("permalink") else None
            ),
            "has_internal_content": bool(text),
            "domain_id": canonical_domain_id((analysis or {}).get("domain"), item),
            "domain_name": DOMAIN_META[
                canonical_domain_id((analysis or {}).get("domain"), item)
            ]["name"],
            "analysis_provider": (analysis or {}).get("provider"),
            "analysis_model": (analysis or {}).get("model"),
            "analysis_version": (analysis or {}).get("version"),
            "analysis_generated_at": (analysis or {}).get("generated_at"),
            "analysis": analysis,
        })

        results.append(item)
    results.sort(key=lambda item: (item["composite_value_score"], item["created_utc"] or 0), reverse=True)
    return results[:limit]


_VI_TOPIC_STOPWORDS = frozenset({
    "bài", "về", "của", "trong", "một", "cho", "các", "và", "không", "với",
    "người", "cộng", "đồng", "thảo", "luận", "mới", "từ", "được", "này",
    "đó", "khi", "ai", "công", "nghệ", "dữ", "liệu", "phần", "mềm", "hệ",
    "thống", "ứng", "dụng", "bản", "tin", "điểm", "mô", "hình", "hỗ", "trợ",
    "the", "and", "for", "with", "from", "this", "that", "are", "was", "new",
    "after", "before", "over", "under", "does", "their", "there", "which",
})


def _topic_tokens(topic: str) -> list[str]:
    """Tách topic tiếng Việt thành các token đặc trưng (bỏ stopword, giữ dài ≥ 3)."""
    tokens = re.findall(r"[a-zA-Z0-9]+", (topic or "").lower())
    return list(dict.fromkeys(
        t for t in tokens if len(t) >= 3 and t not in _VI_TOPIC_STOPWORDS
    ))


def roundup_social_posts(
    db_path: str | Path = "reddit.db",
    *,
    hours: float = 3,
    top: int = 3,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Gom các post đã enrich (V2) trong cửa sổ rolling ``hours`` thành cụm chủ đề.

    Heuristic clustering: cùng ``domain_id`` và chia sẻ ≥ 2 token đặc trưng
    trong ``topic`` (V2 analysis) thì nhập một cụm. Mỗi cụm cộng dồn
    score/comments của toàn bộ thành viên — tương tác của các bài trùng chủ đề
    được gộp lại thay vì xét từng bài.
    """
    hours = max(0.5, min(float(hours), 24 * 7))
    top = max(1, min(int(top), 10))
    now = now if now is not None else time.time()
    cutoff = now - hours * 3600
    hour_start = math.floor((now - hours * 3600) / 3600) * 3600

    conn = _connect_readonly(db_path)
    try:
        rows = conn.execute(
            """
            WITH ranked_metrics AS (
                SELECT post_id, observed_at, score, num_comments,
                       ROW_NUMBER() OVER (
                           PARTITION BY post_id ORDER BY observed_at DESC
                       ) AS rn
                FROM fact_post_metrics
            )
            SELECT p.post_id, p.title, p.created_utc, p.permalink,
                   COALESCE(latest.score, p.score, 0) AS latest_score,
                   COALESCE(latest.num_comments, p.num_comments, 0) AS latest_comments,
                   pa2.payload_json AS analysis_v2_json,
                   pa2.provider AS analysis_v2_provider
            FROM fact_post p
            LEFT JOIN ranked_metrics latest
                   ON latest.post_id = p.post_id AND latest.rn = 1
            JOIN ai_post_analysis_v2 pa2
              ON pa2.post_id = p.post_id AND pa2.status = 'success'
            WHERE p.created_utc >= ? AND COALESCE(p.over_18, 0) = 0
            """,
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    scored: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["analysis_v2_json"] or "{}") or {}
        except ValueError:
            payload = {}
        topic = payload.get("topic") or row["title"] or ""
        domain_id = canonical_domain_id(payload.get("domain"), dict(row))
        tokens = _topic_tokens(topic)
        if not tokens:
            tokens = _topic_tokens(row["title"] or "")
        scored.append({
            "post_id": row["post_id"],
            "title": row["title"] or "",
            "topic": topic,
            "domain_id": domain_id,
            "tokens": tokens,
            "latest_score": int(row["latest_score"] or 0),
            "latest_comments": int(row["latest_comments"] or 0),
            "permalink": row["permalink"],
        })

    if not scored:
        return []

    # Token xuất hiện ở nhiều post trong cửa sổ chính là "chữ ký" chủ đề:
    # cùng domain + cùng ≥2 token phổ biến → cùng cụm.
    freq: dict[str, int] = {}
    for item in scored:
        for token in item["tokens"]:
            freq[token] = freq.get(token, 0) + 1
    for item in scored:
        item["signature"] = tuple(
            token for token, _ in sorted(
                ((t, freq[t]) for t in item["tokens"]),
                key=lambda pair: (-pair[1], pair[0]),
            )[:3]
        )

    scored.sort(
        key=lambda item: (
            item["latest_score"] + 5 * item["latest_comments"], item["post_id"],
        ),
        reverse=True,
    )

    # Token hiếm (xuất hiện ≤ 3 post trong cửa sổ) là "chữ ký" chủ đề: cùng
    # domain + chung 1 token hiếm là đủ để gộp (vd: "reset" trong các post
    # về reset quota Codex dù từ vựng xung quanh khác nhau).
    rare_threshold = 3
    clusters: list[dict[str, Any]] = []
    for item in scored:
        chosen = None
        for cluster in clusters:
            if cluster["domain_id"] != item["domain_id"]:
                continue
            if len(set(cluster["signature"]) & set(item["signature"])) >= 2:
                chosen = cluster
                break
            rare_shared = {
                t for t in (cluster["all_tokens"] & set(item["tokens"]))
                if freq.get(t, 999) <= rare_threshold
            }
            if rare_shared:
                chosen = cluster
                break
        if chosen is None:
            clusters.append({
                "cluster_id": "",
                "hour_start": hour_start,
                "topic_vi": item["topic"],
                "domain_id": item["domain_id"],
                "source_post_ids": [],
                "total_score": 0,
                "total_comments": 0,
                "n_posts": 0,
                "top_post_id": item["post_id"],
                "signature": set(item["signature"]),
                "all_tokens": set(item["tokens"]),
            })
            chosen = clusters[-1]
        chosen["source_post_ids"].append(item["post_id"])
        chosen["total_score"] += item["latest_score"]
        chosen["total_comments"] += item["latest_comments"]
        chosen["n_posts"] += 1
        chosen["signature"] &= set(item["signature"])
        chosen["all_tokens"] |= set(item["tokens"])
        member_engagement = chosen["total_score"] + 5 * chosen["total_comments"]
        item_engagement = item["latest_score"] + 5 * item["latest_comments"]
        if item_engagement > member_engagement or (
            item_engagement == member_engagement and item["post_id"] < chosen["top_post_id"]
        ):
            chosen["top_post_id"] = item["post_id"]
            chosen["topic_vi"] = item["topic"]

    results: list[dict[str, Any]] = []
    clusters.sort(
        key=lambda c: (c["total_score"] + 5 * c["total_comments"], c["n_posts"]),
        reverse=True,
    )
    for i, cluster in enumerate(clusters[:top]):
        results.append({
            "cluster_id": f"clu-{int(hour_start)}-{i:02d}-{cluster['domain_id']}",
            "hour_start": hour_start,
            "topic_vi": cluster["topic_vi"],
            "domain_id": cluster["domain_id"],
            "source_post_ids": cluster["source_post_ids"],
            "total_score": cluster["total_score"],
            "total_comments": cluster["total_comments"],
            "n_posts": cluster["n_posts"],
            "top_post_id": cluster["top_post_id"],
        })
    return results


def post_detail(db_path: str | Path, post_id: str, comment_limit: int = 200) -> dict[str, Any] | None:
    """Nội dung một post, bài báo đã extract và discussion để đọc nội bộ."""
    conn = _connect_readonly(db_path)
    try:
        row = conn.execute(
            """
            SELECT p.*, s.display_name AS subreddit,
                   a.source_url AS article_source_url, a.final_url AS article_final_url,
                   a.title AS article_title, a.author AS article_author,
                   a.published_at AS article_published_at, a.language AS article_language,
                   a.body_text AS article_body, a.word_count AS article_word_count,
                   a.status AS article_status, a.error AS article_error,
                   m.image_url, m.thumbnail_url
            FROM fact_post p
            LEFT JOIN dim_subreddit s ON s.subreddit_id=p.subreddit_id
            LEFT JOIN fact_article_content a ON a.post_id=p.post_id
            LEFT JOIN fact_post_media m ON m.post_id=p.post_id
            WHERE p.post_id=?
            """,
            (post_id,),
        ).fetchone()
        if not row:
            return None
        comments = conn.execute(
            """
            SELECT comment_id, author_name, parent_fullname, created_utc, depth, body,
                   score, controversiality, is_submitter
            FROM fact_comment WHERE post_id=?
            ORDER BY score DESC, created_utc ASC LIMIT ?
            """,
            (post_id, max(1, min(comment_limit, 500))),
        ).fetchall()
        analysis_v2_row = conn.execute(
            "SELECT provider, model, payload_json, generated_at FROM ai_post_analysis_v2 "
            "WHERE post_id=? AND status='success'",
            (post_id,),
        ).fetchone()
        analysis_v1_row = conn.execute(
            "SELECT provider, model, payload_json, generated_at FROM ai_post_analysis "
            "WHERE post_id=? AND status='success'",
            (post_id,),
        ).fetchone()
        resources = conn.execute(
            "SELECT resource_id, url, domain, resource_type, title, description, "
            "context_snippet, author_name, score FROM fact_extracted_resource "
            "WHERE post_id=? ORDER BY score DESC",
            (post_id,),
        ).fetchall()
    finally:
        conn.close()
    result = dict(row)
    result["comments"] = [dict(comment) for comment in comments]
    result["extracted_resources"] = [dict(r) for r in resources]
    analysis_item = dict(result)
    for version, analysis_row in ((2, analysis_v2_row), (1, analysis_v1_row)):
        if analysis_row:
            prefix = f"analysis_v{version}"
            analysis_item[f"{prefix}_json"] = analysis_row["payload_json"]
            analysis_item[f"{prefix}_provider"] = analysis_row["provider"]
            analysis_item[f"{prefix}_model"] = analysis_row["model"]
            analysis_item[f"{prefix}_generated_at"] = analysis_row["generated_at"]
    result["analysis"] = _preferred_analysis(analysis_item)
    result["analysis_provider"] = (result["analysis"] or {}).get("provider")
    result["analysis_model"] = (result["analysis"] or {}).get("model")
    result["analysis_version"] = (result["analysis"] or {}).get("version")
    result["analysis_generated_at"] = (result["analysis"] or {}).get("generated_at")
    result["domain_id"] = canonical_domain_id(
        (result.get("analysis") or {}).get("domain"), result,
    )
    result["domain_name"] = DOMAIN_META.get(result["domain_id"], DOMAIN_META["other"])["name"]
    result["reddit_url"] = (
        f"https://www.reddit.com{result['permalink']}" if result.get("permalink") else None
    )
    return result


def latest_ai_digest(
    db_path: str | Path,
    period: str = "3h",
    *,
    allow_fallback: bool = True,
) -> dict[str, Any] | None:
    conn = _connect_readonly(db_path)
    try:
        row = conn.execute(
            """
            SELECT digest_id, period, window_start, window_end, provider, model, status,
                   title, executive_summary, payload_json, source_count, input_tokens,
                   output_tokens, generated_at, error
            FROM ai_digest WHERE period=? AND status='success'
            ORDER BY generated_at DESC LIMIT 1
            """,
            (period,),
        ).fetchone()
        if not row and allow_fallback:
            row = conn.execute(
                """
                SELECT digest_id, period, window_start, window_end, provider, model, status,
                       title, executive_summary, payload_json, source_count, input_tokens,
                       output_tokens, generated_at, error
                FROM ai_digest WHERE status='success'
                ORDER BY generated_at DESC LIMIT 1
                """
            ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    result = dict(row)
    try:
        payload = json.loads(result.pop("payload_json") or "{}")
    except json.JSONDecodeError:
        payload = {}
    result["payload"] = payload
    return result


def database_stats(db_path: str | Path = "reddit.db") -> dict[str, Any]:
    conn = _connect_readonly(db_path)
    try:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "dim_subreddit", "dim_author", "fact_post", "fact_comment",
                "fact_post_metrics", "crawl_state", "fact_article_content",
                "enrichment_state", "fact_post_media", "ai_digest",
                "ai_post_analysis", "ai_post_analysis_v2", "fact_extracted_resource",
            )
        }
        latest = conn.execute(
            "SELECT MAX(fetched_at) AS fetched_at, MAX(created_utc) AS created_utc FROM fact_post"
        ).fetchone()
    finally:
        conn.close()
    return {**counts, "last_fetched_at": latest[0], "latest_post_at": latest[1]}
