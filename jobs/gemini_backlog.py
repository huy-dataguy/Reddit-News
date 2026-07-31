"""Durable, bounded Gemini analysis backlog.

The backlog is intentionally independent from ``trending_posts``.  Completion
means a persisted, successful Gemini V2 analysis; local and OpenAI artifacts
remain useful provisional data but never remove a post from this queue.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any

from reddit_crawler.llm import generate_post_analysis_v2
from reddit_crawler.storage import Storage


log = logging.getLogger("jobs.gemini_backlog")

LEDGER_KIND = "analysis_v2_gemini"
MAX_BATCH_SIZE = 500
_LEDGER_SCHEMA_VERSION = 1
_USABLE_COMMENT = """
    EXISTS (
        SELECT 1
        FROM fact_comment c
        WHERE c.post_id=p.post_id
          AND c.body IS NOT NULL
          AND TRIM(c.body) <> ''
          AND LOWER(TRIM(c.body)) NOT IN ('[deleted]', '[removed]')
    )
"""


def _connect_readonly(db_path: str) -> sqlite3.Connection:
    path = Path(db_path).resolve()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _parse_ledger(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _attempt_count(value: Any) -> int:
    raw = _parse_ledger(value).get("attempt_count", 0)
    try:
        return max(0, min(int(raw), 1_000_000))
    except (TypeError, ValueError):
        return 0


def _ledger_json(attempt_count: int, **fields: Any) -> str:
    payload = {
        "schema_version": _LEDGER_SCHEMA_VERSION,
        "provider": "gemini",
        "attempt_count": max(0, int(attempt_count)),
        **fields,
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _failure_metadata(exc: Exception, attempt_count: int) -> dict[str, Any]:
    """Return useful but non-sensitive failure metadata.

    Provider exceptions can contain request payloads, API responses or tokens.
    The raw message is therefore used only for classification and a one-way
    fingerprint; it is never persisted, logged or written to the report.
    """
    raw = str(exc)
    normalized = raw.casefold()
    if "429" in normalized or "rate limit" in normalized:
        error_code = "rate_limited"
    elif "quota" in normalized or "resource_exhausted" in normalized:
        error_code = "quota_exhausted"
    elif any(marker in normalized for marker in ("401", "403", "api key", "permission")):
        error_code = "authentication"
    elif "timeout" in normalized or "timed out" in normalized:
        error_code = "timeout"
    elif "quality gate" in normalized:
        error_code = "quality_gate"
    elif any(marker in normalized for marker in ("schema", "validation", "json")):
        error_code = "invalid_output"
    else:
        error_code = "provider_error"
    error_type = type(exc).__name__
    fingerprint = hashlib.sha256(f"{error_type}\0{raw}".encode("utf-8")).hexdigest()[:16]
    return {
        "attempt_count": attempt_count,
        "error_type": error_type,
        "error_code": error_code,
        "error_fingerprint": fingerprint,
    }


def _is_due(attempted_at: Any, now: float, retry_seconds: float) -> bool:
    try:
        attempted = float(attempted_at or 0)
    except (TypeError, ValueError):
        attempted = 0
    return attempted <= 0 or now - attempted >= retry_seconds


def _candidate_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        f"""
        SELECT p.post_id, p.score, p.created_utc,
               es.post_id AS ledger_post_id, es.status AS ledger_status,
               es.attempted_at, es.error AS ledger_error
        FROM fact_post p
        LEFT JOIN enrichment_state es
          ON es.post_id=p.post_id AND es.kind=?
        WHERE COALESCE(p.over_18, 0)=0
          AND {_USABLE_COMMENT}
          AND NOT EXISTS (
              SELECT 1 FROM ai_post_analysis_v2 a
              WHERE a.post_id=p.post_id
                AND a.provider='gemini' AND a.status='success'
          )
        """,
        (LEDGER_KIND,),
    ).fetchall()


def _claim_candidates(
    db_path: str,
    *,
    limit: int,
    retry_seconds: float,
    max_attempts: int,
    now: float,
) -> tuple[list[dict[str, Any]], int]:
    """Atomically claim a bounded batch and return newly blocked count."""
    store = Storage(db_path, None)
    claims: list[dict[str, Any]] = []
    newly_blocked = 0
    try:
        store.conn.row_factory = sqlite3.Row
        store.conn.execute("BEGIN IMMEDIATE")
        rows = _candidate_rows(store.conn)
        available: list[tuple[tuple[Any, ...], sqlite3.Row, int]] = []
        for row in rows:
            status = row["ledger_status"]
            attempts = _attempt_count(row["ledger_error"])
            has_ledger = row["ledger_post_id"] is not None

            if status == "blocked":
                continue
            if status in {"error", "running"} and not _is_due(
                row["attempted_at"], now, retry_seconds,
            ):
                continue
            if attempts >= max_attempts:
                prior = _parse_ledger(row["ledger_error"])
                safe_fields = {
                    key: prior[key]
                    for key in ("error_type", "error_code", "error_fingerprint")
                    if isinstance(prior.get(key), str)
                }
                store.set_enrichment_state(
                    row["post_id"], LEDGER_KIND, "blocked",
                    _ledger_json(
                        attempts,
                        disposition="manual_review",
                        blocked_reason="max_attempts_exhausted",
                        **safe_fields,
                    ),
                )
                newly_blocked += 1
                continue

            # New work always precedes inconsistent completions and retries.
            priority = (
                0 if not has_ledger or status in {None, "pending"} else
                1 if status in {"success", "empty"} else
                2
            )
            sort_key = (
                priority,
                -float(row["score"] or 0),
                -float(row["created_utc"] or 0),
                str(row["post_id"]),
            )
            available.append((sort_key, row, attempts + 1))

        available.sort(key=lambda item: item[0])

        # --- Fairness scheduler: prevent starvation of older articles --------
        # Split batch into "recent" (<=24h) and "backlog" (>24h) slots so that
        # even when many new articles arrive daily, old articles always get
        # some processing capacity every cycle.
        # recent_quota = ceil(60% of limit), backlog_quota = remaining slots.
        import math
        recent_cutoff = now - 86_400  # 24 hours ago
        recent_quota = max(1, math.ceil(limit * 0.6))
        backlog_quota = max(1, limit - recent_quota)

        recent_pool = [item for item in available if float(item[1]["created_utc"] or 0) >= recent_cutoff]
        backlog_pool = [item for item in available if float(item[1]["created_utc"] or 0) < recent_cutoff]

        # Pick from each pool up to its quota; if one pool is exhausted,
        # give leftover slots to the other pool.
        chosen_recent = recent_pool[:recent_quota]
        chosen_backlog = backlog_pool[:backlog_quota]
        leftover_recent = recent_quota - len(chosen_recent)
        leftover_backlog = backlog_quota - len(chosen_backlog)
        if leftover_recent > 0:
            chosen_backlog += backlog_pool[backlog_quota:backlog_quota + leftover_recent]
        if leftover_backlog > 0:
            chosen_recent += recent_pool[recent_quota:recent_quota + leftover_backlog]

        selected = chosen_recent + chosen_backlog
        selected = selected[:limit]  # never exceed the requested batch limit
        # ---------------------------------------------------------------------

        for _, row, attempt_count in selected:
            store.set_enrichment_state(
                row["post_id"], LEDGER_KIND, "running",
                _ledger_json(attempt_count, disposition="in_progress"),
            )
            claims.append({
                "post_id": row["post_id"],
                "attempt_count": attempt_count,
            })
        store.commit()
    except Exception:
        store.conn.rollback()
        raise
    finally:
        store.close()
    return claims, newly_blocked


def _eligible_rows(db_path: str) -> list[sqlite3.Row]:
    conn = _connect_readonly(db_path)
    try:
        return conn.execute(
            f"""
            SELECT p.post_id,
                   CASE WHEN a.post_id IS NULL THEN 0 ELSE 1 END AS completed,
                   es.status AS ledger_status, es.attempted_at,
                   es.error AS ledger_error
            FROM fact_post p
            LEFT JOIN ai_post_analysis_v2 a
              ON a.post_id=p.post_id
             AND a.provider='gemini' AND a.status='success'
            LEFT JOIN enrichment_state es
              ON es.post_id=p.post_id AND es.kind=?
            WHERE COALESCE(p.over_18, 0)=0
              AND {_USABLE_COMMENT}
            ORDER BY p.post_id
            """,
            (LEDGER_KIND,),
        ).fetchall()
    finally:
        conn.close()


def _queue_snapshot(
    db_path: str, *, now: float, retry_seconds: float, max_attempts: int,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    counts = {
        "eligible": 0,
        "completed": 0,
        "remaining": 0,
        "pending": 0,
        "running": 0,
        "retry_waiting": 0,
        "retry_due": 0,
        "blocked": 0,
        "inconsistent_success": 0,
    }
    manual_review: list[dict[str, Any]] = []
    for row in _eligible_rows(db_path):
        counts["eligible"] += 1
        if row["completed"]:
            counts["completed"] += 1
            continue
        counts["remaining"] += 1
        status = row["ledger_status"]
        attempts = _attempt_count(row["ledger_error"])
        metadata = _parse_ledger(row["ledger_error"])
        due = _is_due(row["attempted_at"], now, retry_seconds)

        if status == "blocked" or (attempts >= max_attempts and due):
            effective_status = "blocked"
            counts["blocked"] += 1
        elif status == "error":
            effective_status = "error"
            counts["retry_due" if due else "retry_waiting"] += 1
        elif status == "running":
            effective_status = "stale_running" if due else "running"
            counts["retry_due" if due else "running"] += 1
        elif status == "success":
            effective_status = "inconsistent_success"
            counts["inconsistent_success"] += 1
        else:
            effective_status = "pending"
            counts["pending"] += 1

        if effective_status in {"blocked", "error", "stale_running"}:
            manual_review.append({
                "post_id": row["post_id"],
                "status": effective_status,
                "attempt_count": attempts,
                "attempted_at": row["attempted_at"],
                "error_type": metadata.get("error_type"),
                "error_code": metadata.get("error_code"),
                "error_fingerprint": metadata.get("error_fingerprint"),
            })
    manual_review.sort(key=lambda item: (item["status"] != "blocked", item["post_id"]))
    return counts, manual_review


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _render_markdown(report: dict[str, Any]) -> str:
    queue = report["queue"]
    batch = report["batch"]
    lines = [
        "# Gemini analysis backlog",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Batch",
        "",
        f"- Attempted: {batch['attempted']}",
        f"- Succeeded: {batch['succeeded']}",
        f"- Failed: {batch['failed']}",
        f"- Newly blocked: {batch['newly_blocked']}",
        "",
        "## Queue",
        "",
        f"- Eligible: {queue['eligible']}",
        f"- Gemini completed: {queue['completed']}",
        f"- Remaining: {queue['remaining']}",
        f"- Pending: {queue['pending']}",
        f"- Retry due / waiting: {queue['retry_due']} / {queue['retry_waiting']}",
        f"- Running: {queue['running']}",
        f"- Blocked: {queue['blocked']}",
        "",
        "## Manual review",
        "",
    ]
    review = report["manual_review"]
    if not review:
        lines.append("No queued errors or blocked posts.")
    else:
        lines.extend([
            "| post_id | status | attempts | code | type | fingerprint |",
            "|---|---:|---:|---|---|---|",
        ])
        for item in review:
            lines.append(
                f"| {item['post_id']} | {item['status']} | {item['attempt_count']} | "
                f"{item.get('error_code') or '-'} | {item.get('error_type') or '-'} | "
                f"{item.get('error_fingerprint') or '-'} |"
            )
    return "\n".join(lines).rstrip() + "\n"


def _write_report(report: dict[str, Any], output_dir: str | os.PathLike[str]) -> tuple[str, str]:
    root = Path(output_dir)
    json_path = root / "gemini-backlog.latest.json"
    markdown_path = root / "gemini-backlog.latest.md"
    _atomic_write(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(markdown_path, _render_markdown(report))
    return str(json_path), str(markdown_path)


def run_gemini_backlog_batch(
    db_path: str,
    limit: int = 5,
    comment_limit: int = 120,
    retry_after_hours: float = 6,
    max_attempts: int = 3,
    output_dir: str | os.PathLike[str] = "reports/operations",
) -> dict[str, Any]:
    """Run one bounded Gemini-only batch and materialize an operations report."""
    if max_attempts < 1:
        raise ValueError("max_attempts phải >= 1")
    if comment_limit < 1:
        raise ValueError("comment_limit phải >= 1")
    requested_limit = max(0, int(limit))
    attempt_limit = min(requested_limit, MAX_BATCH_SIZE)
    retry_seconds = max(0.0, float(retry_after_hours)) * 3600
    started_at = time.time()
    claims, preblocked = _claim_candidates(
        db_path,
        limit=attempt_limit,
        retry_seconds=retry_seconds,
        max_attempts=max_attempts,
        now=started_at,
    )

    results: list[dict[str, Any]] = []
    succeeded = failed = newly_blocked = 0
    quota_stopped = False
    processed_ids: set[str] = set()
    ledger_store = Storage(db_path, None)
    try:
        for claim in claims:
            post_id = claim["post_id"]
            attempt_count = claim["attempt_count"]
            try:
                analysis = generate_post_analysis_v2(
                    db_path,
                    post_id,
                    provider="gemini",
                    comment_limit=comment_limit,
                )
                if analysis.get("provider") != "gemini":
                    raise RuntimeError("strict provider contract returned a non-Gemini artifact")
            except Exception as exc:
                metadata = _failure_metadata(exc, attempt_count)

                # Quota/rate-limit: release this article back to pending and
                # stop the batch immediately — no point hammering a dry quota.
                if metadata["error_code"] in {"rate_limited", "quota_exhausted"}:
                    ledger_store.set_enrichment_state(
                        post_id, LEDGER_KIND, "pending",
                        _ledger_json(max(0, attempt_count - 1), disposition="quota_released"),
                    )
                    ledger_store.commit()
                    # Release all remaining unclaimed articles back to pending.
                    for remaining_claim in claims:
                        if remaining_claim["post_id"] not in processed_ids and remaining_claim["post_id"] != post_id:
                            ledger_store.set_enrichment_state(
                                remaining_claim["post_id"], LEDGER_KIND, "pending",
                                _ledger_json(0, disposition="quota_released"),
                            )
                    ledger_store.commit()
                    quota_stopped = True
                    log.warning(
                        "Gemini quota/rate-limit hit on post %s (code=%s) — stopping batch, %d articles released.",
                        post_id, metadata["error_code"], len(claims) - len(processed_ids),
                    )
                    break

                terminal = attempt_count >= max_attempts
                status = "blocked" if terminal else "error"
                ledger_store.set_enrichment_state(
                    post_id,
                    LEDGER_KIND,
                    status,
                    _ledger_json(
                        attempt_count,
                        disposition="manual_review" if terminal else "retry",
                        **{key: value for key, value in metadata.items() if key != "attempt_count"},
                    ),
                )
                ledger_store.commit()
                failed += 1
                newly_blocked += int(terminal)
                safe_result = {"post_id": post_id, "status": status, **metadata}
                results.append(safe_result)
                log.warning(
                    "Gemini backlog post %s failed: code=%s fingerprint=%s attempt=%s",
                    post_id, metadata["error_code"], metadata["error_fingerprint"],
                    attempt_count,
                )
                continue

            ledger_store.set_enrichment_state(
                post_id,
                LEDGER_KIND,
                "success",
                _ledger_json(attempt_count, disposition="complete"),
            )
            ledger_store.commit()
            succeeded += 1
            processed_ids.add(post_id)
            results.append({
                "post_id": post_id,
                "status": "success",
                "attempt_count": attempt_count,
            })
    finally:
        ledger_store.close()

    completed_at = time.time()
    queue, manual_review = _queue_snapshot(
        db_path,
        now=completed_at,
        retry_seconds=retry_seconds,
        max_attempts=max_attempts,
    )
    report = {
        "schema_version": 1,
        "provider": "gemini",
        "generated_at": dt.datetime.fromtimestamp(
            completed_at, dt.timezone.utc,
        ).isoformat(),
        "batch": {
            "requested_limit": requested_limit,
            "attempt_limit": attempt_limit,
            "attempted": len(claims),
            "succeeded": succeeded,
            "failed": failed,
            "newly_blocked": preblocked + newly_blocked,
            "quota_stopped": quota_stopped,
            "started_at": started_at,
            "completed_at": completed_at,
            "results": results,
        },
        "queue": queue,
        "manual_review": manual_review,
    }
    json_path, markdown_path = _write_report(report, output_dir)
    return {
        **report["batch"],
        "provider": "gemini",
        "queue": queue,
        "manual_review": manual_review,
        "report_json_path": json_path,
        "report_markdown_path": markdown_path,
    }
