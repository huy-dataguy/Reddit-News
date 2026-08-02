"""Evidence-first orchestration for Reddit Radar."""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from jobs.enrich import run_enrichment
from jobs.report import create_report
from reddit_crawler.analytics import PERIOD_SECONDS, trending_posts
from reddit_crawler.llm import generate_digest, generate_post_analysis_v2
from reddit_crawler.storage import Storage

log = logging.getLogger("jobs.pipeline")


def _analysis_state(db_path: str, post_id: str) -> dict[str, Any]:
    path = Path(db_path).resolve()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        row = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM fact_comment WHERE post_id=?) AS comment_count,
                provider,
                status,
                generated_at
            FROM (SELECT 1)
            LEFT JOIN ai_post_analysis_v2 ON post_id=?
            """,
            (post_id, post_id),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def _fresh_provider_is_usable(existing: str | None, requested: str) -> bool:
    if not existing:
        return False
    if requested == "gemini" or requested == "openai":
        return existing == requested
    if requested == "local":
        return existing in {"local", "local-fallback"}
    llm_available = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    if llm_available:
        return existing in {"gemini", "openai", "opencode"}
    return existing in {"gemini", "openai", "opencode", "local", "local-fallback"}


def analyze_top_posts_v2(
    db_path: str = "reddit.db",
    period: str = "day",
    *,
    limit: int = 5,
    provider: str = "auto",
    comment_limit: int = 120,
    freshness_hours: float = 24,
) -> dict[str, Any]:
    """Analyze high-value discussions into the V2 contract.

    Fresh LLM output is reused.  A fresh local extract is deliberately replaced
    when ``auto`` has an LLM credential available, preventing a local fallback
    from permanently blocking grounded analysis.
    """
    if period not in PERIOD_SECONDS:
        raise ValueError(f"period không hợp lệ: {period}")
    if provider not in {"auto", "gemini", "openai", "local"}:
        raise ValueError(f"provider không hợp lệ: {provider}")
    bounded_limit = max(0, min(int(limit), 50))
    result: dict[str, Any] = {
        "period": period,
        "requested": bounded_limit,
        "attempted": 0,
        "analyzed": 0,
        "llm_analyzed": 0,
        "provisional": 0,
        "skipped_fresh": 0,
        "skipped_no_comments": 0,
        "failed": 0,
        "results": [],
        "errors": [],
    }
    if bounded_limit == 0:
        return result

    # Ensure V2 schema exists before opening read-only candidate queries.
    store = Storage(db_path, None)
    store.close()
    candidates = trending_posts(
        db_path,
        period=period,
        limit=max(20, min(bounded_limit * 5, 200)),
    )
    freshness_seconds = max(0.0, freshness_hours) * 3600
    now = time.time()
    for item in candidates:
        # ``limit`` is a cost ceiling, not a success target.  Provider errors
        # must never expand a requested batch into dozens of paid attempts.
        if result["attempted"] >= bounded_limit:
            break
        post_id = item["post_id"]
        state = _analysis_state(db_path, post_id)
        if int(state.get("comment_count") or 0) == 0:
            result["skipped_no_comments"] += 1
            continue
        generated_at = float(state.get("generated_at") or 0)
        is_fresh = (
            state.get("status") == "success"
            and now - generated_at < freshness_seconds
            and _fresh_provider_is_usable(state.get("provider"), provider)
        )
        if is_fresh:
            result["skipped_fresh"] += 1
            continue

        result["attempted"] += 1
        try:
            analysis = generate_post_analysis_v2(
                db_path,
                post_id,
                provider=provider,
                comment_limit=comment_limit,
            )
            result["results"].append({
                "post_id": post_id,
                "provider": analysis.get("provider"),
                "model": analysis.get("model"),
                "artifact_kind": analysis.get("artifact_kind"),
            })
            result["analyzed"] += 1
            artifact_kind = analysis.get("artifact_kind") or (
                "llm" if analysis.get("provider") in {"gemini", "openai"}
                else "provisional"
            )
            if artifact_kind == "llm":
                result["llm_analyzed"] += 1
            else:
                result["provisional"] += 1
        except Exception as exc:
            message = f"{type(exc).__name__}: {str(exc)[:300]}"
            result["failed"] += 1
            result["errors"].append(f"{post_id}: {message}")
            log.warning("V2 analysis lỗi ở post %s: %s", post_id, message)
    return result


def run_pipeline(
    db_path: str = "reddit.db",
    *,
    raw_dir: str | None = "raw",
    period: str = "day",
    enrich_limit: int = 5,
    analysis_limit: int = 3,
    digest_limit: int = 20,
    provider: str = "auto",
    report_dir: str | os.PathLike[str] = "reports",
    depth: int | None = 4,
    comment_limit: int = 120,
    freshness_hours: float = 24,
) -> dict[str, Any]:
    """Run enrichment, V2 analysis, digest and report in dependency order."""
    started_at = time.time()
    stages: dict[str, Any] = {}
    stages["enrich"] = run_enrichment(
        db_path,
        raw_dir=raw_dir,
        period=period,
        limit=enrich_limit,
        depth=depth,
        kind="both",
    )
    stages["analyze"] = analyze_top_posts_v2(
        db_path,
        period,
        limit=analysis_limit,
        provider=provider,
        comment_limit=comment_limit,
        freshness_hours=freshness_hours,
    )
    stages["digest"] = generate_digest(
        db_path,
        period=period,
        provider=provider,
        limit=max(1, min(int(digest_limit), 200)),
    )
    stages["report"] = create_report(
        db_path,
        period,
        limit=max(1, min(int(digest_limit), 200)),
        output_dir=report_dir,
    )
    analyze = stages["analyze"]
    requested_analysis_missing = bool(
        int(analysis_limit) > 0
        and not analyze.get("analyzed")
        and not analyze.get("skipped_fresh")
    )
    provisional_auto = bool(provider == "auto" and analyze.get("provisional"))
    digest_provisional = bool(
        provider == "auto" and stages["digest"].get("artifact_kind") == "provisional"
    )
    partial = bool(
        stages["enrich"].get("failed")
        or analyze.get("failed")
        or requested_analysis_missing
        or provisional_auto
        or digest_provisional
    )
    return {
        "status": "partial" if partial else "success",
        "period": period,
        "started_at": started_at,
        "completed_at": time.time(),
        "stages": stages,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chạy Evidence-to-Intelligence pipeline")
    parser.add_argument("--db", default=os.environ.get("REDDIT_DB_PATH", "reddit.db"))
    parser.add_argument("--raw", default="raw")
    parser.add_argument("--period", default="day", choices=PERIOD_SECONDS)
    parser.add_argument("--enrich-limit", type=int, default=5)
    parser.add_argument("--analysis-limit", type=int, default=3)
    parser.add_argument("--digest-limit", type=int, default=20)
    parser.add_argument("--provider", default="auto", choices=["auto", "gemini", "openai", "local"])
    parser.add_argument("--report-dir", default=os.environ.get("REPORT_DIR", "reports"))
    parser.add_argument("--depth", type=int, default=4)
    parser.add_argument("--comment-limit", type=int, default=120)
    parser.add_argument("--freshness-hours", type=float, default=24)
    args = parser.parse_args(argv)
    result = run_pipeline(
        db_path=args.db,
        raw_dir=args.raw or None,
        period=args.period,
        enrich_limit=args.enrich_limit,
        analysis_limit=args.analysis_limit,
        digest_limit=args.digest_limit,
        provider=args.provider,
        report_dir=args.report_dir,
        depth=args.depth,
        comment_limit=args.comment_limit,
        freshness_hours=args.freshness_hours,
    )
    print(
        f"Pipeline {result['status']}: "
        f"comments={result['stages']['enrich']['comments_fetched']}, "
        f"analysis={result['stages']['analyze']['analyzed']}, "
        f"digest={result['stages']['digest']['digest_id']}, "
        f"report={result['stages']['report']['markdown_path']}"
    )
    return 0 if result["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
