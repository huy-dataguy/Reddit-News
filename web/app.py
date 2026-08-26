"""Read-only FastAPI backend and dashboard for Reddit Radar."""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response
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
SUBS_FILE = ROOT.parent / "jobs" / "subs.txt"

app = FastAPI(title="Reddit Radar", version="1.1.0")
api = APIRouter()
if (DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

# ── Security constants / helpers ────────────────────────────────────────────
_POST_ID_RE = re.compile(r"^(t[13]_)?[a-z0-9]{2,12}$")
_MAX_QUERY_LEN = 200
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    ),
}
_RATE_LIMIT_ENABLED = os.environ.get("WEB_RATE_LIMIT", "on").lower() != "off"
_WRITE_TOKEN = os.environ.get("WEB_WRITE_TOKEN", "") or ""
_CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
# (rate / giây, burst) — refill liên tục, burst là "token tối đa tích được"
_RATE_RULES = {
    "GET": (2.0, 240.0),
    "POST": (0.5, 30.0),
}
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _client_ip(request: Request) -> str:
    """IP thật từ kết nối trực tiếp; không bao giờ tin X-Forwarded-For của client."""
    host = request.client.host if request.client else ""
    return host or "unknown"


def _is_loopback(ip: str) -> bool:
    return ip in _LOOPBACK_HOSTS or ip.startswith("127.")


class _RateLimiter:
    """Token bucket đơn giản theo IP; chỉ tin IP kết nối trực tiếp."""

    def __init__(self) -> None:
        self._buckets: dict[str, dict[str, tuple[float, float]]] = {}

    def allow(self, ip: str, method: str, now: float | None = None) -> tuple[bool, float]:
        now = time.time() if now is None else now
        rate, burst = _RATE_RULES.get(method, _RATE_RULES["GET"])
        buckets = self._buckets.setdefault(ip, {})
        tokens, last = buckets.get(method, (burst, now))
        tokens = min(burst, tokens + (now - last) * rate)
        if tokens >= 1.0:
            buckets[method] = (tokens - 1.0, now)
            return True, 0.0
        buckets[method] = (tokens, now)
        retry_after = (1.0 - tokens) / rate if rate > 0 else burst
        return False, retry_after


_rate_limiter = _RateLimiter()


def _connect_readonly() -> sqlite3.Connection:
    path = Path(DB_PATH).resolve()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _connect_write() -> sqlite3.Connection:
    """Connect for user-state writes; đợi lâu nếu collector đang giữ write lock."""
    path = Path(DB_PATH).resolve()
    conn = sqlite3.connect(str(path), timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


# ── Subreddit config (jobs/subs.txt) ────────────────────────────────────────
_SUB_NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,21}$")
_MAX_SUBS = 100


def _normalize_sub(name: str) -> str:
    return (name or "").strip().removeprefix("r/").removeprefix("/").strip()


def _load_subs_file(path: Path) -> list[str]:
    subs: list[str] = []
    if not path.exists():
        return subs
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        subs.append(line.removeprefix("r/").lstrip("/"))
    return subs


def _write_subs_file(path: Path, names: list[str]) -> None:
    """Ghi danh sách sub (giữ nguyên các dòng comment/trống) một cách atomic."""
    comments: list[str] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                comments.append(line)
    text = "\n".join(comments)
    if comments:
        text += "\n"
    text += "".join(f"{name}\n" for name in names)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _subs_snapshot() -> dict[str, Any]:
    subs = _load_subs_file(SUBS_FILE)
    per_sub: dict[str, dict[str, Any]] = {name: {} for name in subs}
    try:
        conn = _connect_readonly()
        try:
            if _table_exists(conn, "fact_post") and _table_exists(conn, "dim_subreddit"):
                rows = conn.execute(
                    """
                    SELECT d.display_name AS name, COUNT(p.post_id) AS post_count,
                           MAX(p.fetched_at) AS last_fetched_at
                    FROM dim_subreddit d
                    LEFT JOIN fact_post p ON p.subreddit_id = d.subreddit_id
                    GROUP BY d.subreddit_id
                    """
                ).fetchall()
                for row in rows:
                    key = row["name"]
                    if key in per_sub:
                        per_sub[key] = {
                            "post_count": int(row["post_count"] or 0),
                            "last_fetched_at": float(row["last_fetched_at"]) if row["last_fetched_at"] else None,
                        }
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        per_sub = {name: {} for name in subs}
    try:
        label = str(SUBS_FILE.relative_to(ROOT.parent))
    except ValueError:
        label = str(SUBS_FILE)
    return {
        "count": len(subs),
        "file": label,
        "subs": [{"name": name, **per_sub.get(name, {})} for name in subs],
    }


@app.middleware("http")
async def security_middleware(request: Request, call_next: Any) -> Response:
    ip = _client_ip(request)
    method = request.method.upper()
    path = request.url.path

    # CORS preflight
    if method == "OPTIONS":
        origin = request.headers.get("origin", "")
        if _CORS_ORIGINS and origin in _CORS_ORIGINS:
            return Response(status_code=204, headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "X-API-Token, Content-Type",
                "Access-Control-Max-Age": "3600",
            })
        return Response(status_code=204)

    # Rate limit (trước khi chạm DB)
    if _RATE_LIMIT_ENABLED and path.startswith("/api/"):
        allowed, retry_after = _rate_limiter.allow(ip, method)
        if not allowed:
            return Response(
                status_code=429,
                headers={"Retry-After": str(int(retry_after) + 1)},
                content=json.dumps({
                    "error": "rate_limited",
                    "retry_after": int(retry_after) + 1,
                }).encode("utf-8"),
                media_type="application/json",
            )

    # Write guard: mọi POST /api đều cần token khi không đến từ loopback
    if method == "POST" and path.startswith("/api/"):
        if not _is_loopback(ip):
            provided = request.headers.get("X-API-Token", "")
            if not _WRITE_TOKEN or provided != _WRITE_TOKEN:
                return Response(
                    status_code=401,
                    content=json.dumps({"error": "unauthorized"}).encode("utf-8"),
                    media_type="application/json",
                )

    response = await call_next(request)

    for header, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    if _CORS_ORIGINS:
        origin = request.headers.get("origin", "")
        if origin in _CORS_ORIGINS:
            response.headers.setdefault("Access-Control-Allow-Origin", origin)
    return response


def _require_valid_post_id(post_id: str) -> str:
    if not _POST_ID_RE.match(post_id or ""):
        raise HTTPException(status_code=400, detail="post_id không hợp lệ")
    return post_id


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
    ).fetchone() is not None


def _table_count(conn: sqlite3.Connection, table: str, where: str = "") -> int:
    if not _table_exists(conn, table):
        return 0
    row = conn.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()
    return int(row[0]) if row else 0


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
    *, domain: str | None, query: str, limit: int, offset: int, sub: str | None = None,
) -> dict[str, Any]:
    conn = _connect_readonly()
    try:
        items = _unified_knowledge_items(conn)
        # Gather all subreddits that have posts (not just analyzed ones)
        all_sub_rows = conn.execute(
            "SELECT d.display_name, COUNT(f.post_id) AS cnt "
            "FROM fact_post f JOIN dim_subreddit d ON f.subreddit_id = d.subreddit_id "
            "GROUP BY d.display_name"
        ).fetchall() if _table_exists(conn, "fact_post") else []
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

    # Build subreddit chips from ALL subs in dim_subreddit, merge with analysis counts
    sub_counts: dict[str, int] = {}
    for item in items:
        name = str(item.get("subreddit") or "").strip()
        if name:
            sub_counts[name] = sub_counts.get(name, 0) + 1
    # Include all subreddits that have posts (even if not yet analyzed)
    for row in all_sub_rows:
        name = str(row["display_name"] or "").strip()
        if name and name not in sub_counts:
            sub_counts[name] = 0  # has posts but no analysis yet
    subreddits = [
        {"name": name, "count": count}
        for name, count in sorted(sub_counts.items(), key=lambda pair: (-pair[1], pair[0].casefold()))
    ]

    if domain and domain != "all":
        items = [item for item in items if item.get("domain_id") == domain]
    if sub and sub != "all":
        items = [item for item in items if str(item.get("subreddit") or "").casefold() == sub.casefold()]
    total = len(items)
    page = items[offset:offset + limit]
    return {
        "count": len(page),
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(page) < total,
        "domains": domains,
        "subreddits": subreddits,
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
    result["summary"] = result.get("executive_summary")
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
        "api_version": "v1",
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


@api.get("/health")
def health() -> dict[str, Any]:
    try:
        return _health_snapshot()
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail=f"Database chưa sẵn sàng: {exc}") from exc


@api.get("/stats")
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


@api.get("/subs")
def subs_list() -> dict[str, Any]:
    """Danh sách subreddit nguồn crawl hiện tại (đọc từ jobs/subs.txt)."""
    try:
        return _subs_snapshot()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Không đọc được tệp subreddit: {exc}") from exc


@api.post("/subs")
def subs_update(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ghi đè danh sách subreddit nguồn crawl vào jobs/subs.txt (atomic).

    Collector (systemd timer) đọc lại file ở mỗi lần chạy nên thay đổi này
    được áp dụng tự động cho các lần crawl tiếp theo.
    """
    if payload is None or "subs" not in payload or not isinstance(payload["subs"], list):
        raise HTTPException(status_code=400, detail="payload phải là {\"subs\": [\"sub1\", ...]}")
    if len(payload["subs"]) > _MAX_SUBS:
        raise HTTPException(status_code=400, detail=f"Tối đa {_MAX_SUBS} subreddit")

    seen: set[str] = set()
    normalized: list[str] = []
    for raw in payload["subs"]:
        name = _normalize_sub(str(raw))
        if not name:
            continue
        if not _SUB_NAME_RE.match(name):
            raise HTTPException(
                status_code=400,
                detail=f"Tên subreddit không hợp lệ: {raw!r} (3-21 ký tự, chỉ chữ/số/dấu gạch dưới)",
            )
        if name.casefold() not in seen:
            seen.add(name.casefold())
            normalized.append(name)
    try:
        _write_subs_file(SUBS_FILE, normalized)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Không ghi được tệp subreddit: {exc}") from exc
    return _subs_snapshot()


@api.get("/trending")
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


@api.get("/digests/latest")
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


@api.get("/today")
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


@api.get("/knowledge/feed")
def knowledge_feed(
    domain: str | None = Query(None),
    sub: str | None = Query(None),
    q: str = Query("", max_length=200),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return _knowledge_feed_data(domain=domain, query=q, limit=limit, offset=offset, sub=sub)


@api.get("/knowledge/feed/v2")
def knowledge_feed_v2(
    domain: str | None = Query(None),
    sub: str | None = Query(None),
    q: str = Query("", max_length=200),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Compatibility alias; the canonical feed already merges V2 and V1."""
    return _knowledge_feed_data(domain=domain, query=q, limit=limit, offset=offset, sub=sub)


@api.get("/knowledge/{post_id}")
def knowledge_detail(post_id: str) -> dict[str, Any]:
    _require_valid_post_id(post_id)
    conn = _connect_readonly()
    try:
        items = _unified_knowledge_items(conn, post_id)
    finally:
        conn.close()
    if not items:
        raise HTTPException(status_code=404, detail="Chưa có bản đúc kết cho bài viết này")
    return items[0]


@api.get("/knowledge/{post_id}/v2")
def knowledge_detail_v2(post_id: str) -> dict[str, Any]:
    """Compatibility alias for clients that previously requested the V2 route."""
    return knowledge_detail(post_id)


@api.get("/posts/{post_id}")
def detail(post_id: str) -> dict[str, Any]:
    _require_valid_post_id(post_id)
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


@api.get("/posts/{post_id}/export")
def export_post(post_id: str, format: str = Query("markdown")) -> dict[str, str]:
    _require_valid_post_id(post_id)
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


@api.get("/buzz")
def ai_buzz_endpoint(period: str = Query("month", pattern="^(week|month)$")) -> dict[str, Any]:
    """Đọc Bản tin AI Buzz đã sinh sẵn (bởi cli generate-buzz / systemd timer).

    Web KHÔNG gọi LLM; nếu chưa có bulletin cho period, fallback về dựng nhanh
    từ trending_posts với cờ cached=False.
    """
    from reddit_crawler.analytics import latest_ai_buzz_bulletin

    bulletin = latest_ai_buzz_bulletin(DB_PATH, period=period)
    if bulletin:
        return {
            "period": period,
            "title": bulletin["title"],
            "full_bulletin_text": bulletin["full_bulletin_text"],
            "stories": bulletin["payload"].get("stories", []),
            "provider": bulletin["provider"],
            "model": bulletin["model"],
            "source_count": bulletin["source_count"],
            "generated_at": bulletin["generated_at"],
            "window_start": bulletin["window_start"],
            "window_end": bulletin["window_end"],
            "cached": True,
        }

    items = trending_posts(DB_PATH, period=period, limit=10)
    now = dt.datetime.now(dt.timezone.utc)
    period_label = (
        f"THÁNG {now.month}/{now.year}" if period == "month"
        else f"TUẦN {now.isocalendar().week}/{now.year}"
    )
    stories = []
    bulletin_lines = [
        f"★ BẢN TIN CÔNG NGHỆ {period_label} | AI BUZZ {period_label}\n",
        "Cùng Reddit Radar điểm qua một số bản tin công nghệ nổi bật về Trí tuệ nhân tạo (AI) và Lập trình:\n",
    ]

    badges = ["🔹", "🔥", "⚡", "🚀", "💡", "🛡️", "🤖"]
    for i, item in enumerate(items[:6]):
        badge = badges[i % len(badges)]
        analysis = item.get("analysis") or {}
        title = analysis.get("topic") or item.get("title") or "Hot Tech Story"
        summary = analysis.get("verdict") or analysis.get("summary") or "Thảo luận nổi bật với lượng tương tác lớn từ cộng đồng."
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
        "cached": False,
    }


@api.get("/social/roundup")
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


@api.get("/resources")
def resources(
    kind: str = Query("all"),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    items = get_top_extracted_resources(DB_PATH, resource_type=kind, limit=limit)
    return {"kind": kind, "count": len(items), "items": items}


@api.get("/user/bookmarks")
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


@api.get("/user/bookmarks/details")
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



@api.post("/user/bookmarks/{post_id}")
def toggle_user_bookmark(post_id: str) -> dict[str, Any]:
    """Save or toggle bookmark in SQLite database."""
    _require_valid_post_id(post_id)
    conn = _connect_write()
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


@api.get("/user/read")
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


@api.post("/user/read/{post_id}")
def mark_user_read_post(post_id: str) -> dict[str, Any]:
    """Mark a post as read in SQLite database."""
    _require_valid_post_id(post_id)
    conn = _connect_write()
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


# ── API versioning: /api/v1/* (chính thức), /api/* (legacy alias) ──────────
app.include_router(api, prefix="/api/v1")
app.include_router(api, prefix="/api")


@app.get("/{full_path:path}", include_in_schema=False, response_model=None)
def spa_fallback(full_path: str) -> FileResponse | Response:
    """Serve the SPA for client-side routes; keep unknown /api paths as 404 JSON."""
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(DIST / "index.html")

