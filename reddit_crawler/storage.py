"""Lưu trữ 2 tầng:

  BRONZE (raw): mỗi post/comment/user gốc -> JSONL append-only để tái xử lý sau.
  GOLD (modeled): nạp vào SQLite theo star schema (schema.sql).

Giữ raw giúp bạn đổi mô hình sau này mà không phải crawl lại.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_REQUIRED_SCHEMA_VERSION = 9
_REQUIRED_TABLES = {
    "schema_migration", "fact_post", "fact_comment", "fact_post_metrics",
    "enrichment_state", "ai_digest", "ai_post_analysis", "ai_post_analysis_v2",
    "fact_extracted_resource",
}

_PRIMARY_KEYS = {
    "dim_subreddit": ("subreddit_id",),
    "dim_author": ("author_name",),
    "dim_date": ("date_key",),
    "fact_post": ("post_id",),
    "fact_comment": ("comment_id",),
    "crawl_state": ("scope",),
    "fact_article_content": ("post_id",),
    "enrichment_state": ("post_id", "kind"),
    "fact_post_media": ("post_id",),
    "ai_digest": ("digest_id",),
    "ai_post_analysis": ("post_id",),
    "ai_post_analysis_v2": ("post_id",),
    "fact_extracted_resource": ("resource_id",),
}

_EXTRACTED_RESOURCE_COLUMNS = (
    "resource_id", "post_id", "comment_id", "platform_id", "url", "domain",
    "resource_type", "title", "description", "context_snippet", "author_name",
    "score", "extracted_at",
)

_ANALYSIS_COLUMNS = (
    "post_id", "provider", "model", "status", "payload_json", "comment_count",
    "input_tokens", "output_tokens", "generated_at", "error",
)


def _date_key(created_utc: float | None) -> tuple[int, dict] | tuple[None, None]:
    if not created_utc:
        return None, None
    d = dt.datetime.fromtimestamp(created_utc, dt.timezone.utc)
    key = d.year * 10000 + d.month * 100 + d.day
    row = {
        "date_key": key,
        "full_date": d.strftime("%Y-%m-%d"),
        "year": d.year,
        "quarter": (d.month - 1) // 3 + 1,
        "month": d.month,
        "day": d.day,
        "weekday": d.weekday(),
    }
    return key, row


def _short_id(fullname: str | None, fallback: str | None = None) -> str | None:
    if fullname and "_" in fullname:
        return fullname.split("_", 1)[1]
    return fallback


def _safe_image_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = html.unescape(value).strip()
    return value if value.startswith(("https://", "http://")) else None


class Storage:
    def __init__(self, db_path: str = "reddit.db", raw_dir: str | None = "raw") -> None:
        self.conn = sqlite3.connect(db_path, timeout=30)
        has_schema = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fact_post'"
        ).fetchone()
        if has_schema:
            try:
                self._validate_existing_schema()
            except Exception:
                self.conn.close()
                raise
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=30000")
        if not has_schema:
            self.conn.executescript(_SCHEMA_PATH.read_text())
            self._repair_legacy_references()
            self.conn.commit()
        self.raw_dir = Path(raw_dir) if raw_dir else None
        if self.raw_dir:
            self.raw_dir.mkdir(parents=True, exist_ok=True)

    def _validate_existing_schema(self) -> None:
        tables = {
            row[0] for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing = sorted(_REQUIRED_TABLES - tables)
        version = 0
        if "schema_migration" in tables:
            row = self.conn.execute("SELECT MAX(version) FROM schema_migration").fetchone()
            version = int(row[0] or 0)
        if version < _REQUIRED_SCHEMA_VERSION or missing:
            detail = f"; missing tables: {', '.join(missing)}" if missing else ""
            raise RuntimeError(
                "SQLite schema incomplete: requires migration 9 or newer"
                f"{detail}. Upgrade and verify a database copy; do not migrate the live DB in place."
            )

    # ---------------- BRONZE ----------------
    def write_raw(self, kind: str, records: Iterable[dict]) -> None:
        if not self.raw_dir:
            return
        path = self.raw_dir / f"{kind}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---------------- helpers ----------------
    def _upsert(
        self, table: str, row: dict[str, Any], *, preserve_existing_on_null: bool = False,
    ) -> None:
        """Upsert mà không dùng ``INSERT OR REPLACE``.

        REPLACE thực chất xóa rồi chèn lại, có thể làm đứt foreign key và khiến
        một bản ghi stub ghi đè metadata tốt bằng NULL. Với dữ liệu đến từ nguồn
        không đầy đủ (API lịch sử), COALESCE giữ lại giá trị tốt đã có.
        """
        keys = _PRIMARY_KEYS[table]
        cols = ",".join(row)
        ph = ",".join("?" * len(row))
        updates = []
        for col in row:
            if col in keys:
                continue
            value = f"excluded.{col}"
            if preserve_existing_on_null:
                value = f"COALESCE(excluded.{col}, {table}.{col})"
            updates.append(f"{col}={value}")
        conflict = ",".join(keys)
        self.conn.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({ph}) "
            f"ON CONFLICT ({conflict}) DO UPDATE SET {','.join(updates)}",
            list(row.values()),
        )

    def _repair_legacy_references(self) -> None:
        """Bổ sung dimension/post stub cho DB cũ được tạo khi FK còn tắt."""
        self.conn.executescript(
            """
            INSERT OR IGNORE INTO dim_subreddit (subreddit_id, display_name)
            SELECT DISTINCT subreddit_id, subreddit_id FROM fact_post
            WHERE subreddit_id IS NOT NULL;
            INSERT OR IGNORE INTO dim_subreddit (subreddit_id, display_name)
            SELECT DISTINCT subreddit_id, subreddit_id FROM fact_comment
            WHERE subreddit_id IS NOT NULL;
            INSERT OR IGNORE INTO dim_author (author_name)
            SELECT DISTINCT author_name FROM fact_post
            WHERE author_name IS NOT NULL AND author_name <> '[deleted]';
            INSERT OR IGNORE INTO dim_author (author_name)
            SELECT DISTINCT author_name FROM fact_comment
            WHERE author_name IS NOT NULL AND author_name <> '[deleted]';
            INSERT OR IGNORE INTO fact_post (post_id, fullname)
            SELECT DISTINCT post_id, 't3_' || post_id FROM fact_comment
            WHERE post_id IS NOT NULL;
            """
        )

    def _ensure_date(self, created_utc: float | None) -> int | None:
        key, row = _date_key(created_utc)
        if key is not None:
            self._upsert("dim_date", row)
        return key

    # ---------------- GOLD: dimensions ----------------
    def upsert_subreddit(self, s: dict) -> None:
        subreddit_id = _short_id(s.get("name"), s.get("id"))
        if not subreddit_id:
            return
        self._upsert("dim_subreddit", {
            "subreddit_id": subreddit_id,
            "display_name": s.get("display_name"),
            "title": s.get("title"),
            "subscribers": s.get("subscribers"),
            "created_utc": s.get("created_utc"),
            "over18": int(bool(s.get("over18"))),
            "public_description": s.get("public_description"),
            "lang": s.get("lang"),
            "fetched_at": time.time(),
        }, preserve_existing_on_null=True)

    def upsert_author(self, u: dict) -> None:
        if not u or u.get("name") in (None, "[deleted]"):
            return
        self._upsert("dim_author", {
            "author_name": u.get("name"),
            "author_id": u.get("id"),
            "created_utc": u.get("created_utc"),
            "total_karma": u.get("total_karma"),
            "link_karma": u.get("link_karma"),
            "comment_karma": u.get("comment_karma"),
            "is_mod": int(bool(u.get("is_mod"))),
            "is_gold": int(bool(u.get("is_gold"))),
            "verified": int(bool(u.get("verified"))),
            "has_verified_email": int(bool(u.get("has_verified_email"))),
            "fetched_at": time.time(),
        }, preserve_existing_on_null=True)

    def _stub_author(self, name: str | None) -> None:
        """Ghi tối thiểu tên tác giả để giữ toàn vẹn khóa ngoại khi chưa crawl /about."""
        if not name or name == "[deleted]":
            return
        self.conn.execute(
            "INSERT OR IGNORE INTO dim_author (author_name) VALUES (?)", (name,)
        )

    def _stub_subreddit(self, subreddit_id: str | None, display_name: str | None = None) -> None:
        if not subreddit_id:
            return
        self.conn.execute(
            "INSERT INTO dim_subreddit (subreddit_id, display_name) VALUES (?,?) "
            "ON CONFLICT(subreddit_id) DO UPDATE SET display_name=CASE "
            "WHEN dim_subreddit.display_name IS NULL "
            "OR dim_subreddit.display_name=dim_subreddit.subreddit_id "
            "THEN COALESCE(excluded.display_name, dim_subreddit.display_name) "
            "ELSE dim_subreddit.display_name END",
            (subreddit_id, display_name),
        )

    def _stub_post(self, post_id: str | None) -> None:
        if not post_id:
            return
        self.conn.execute(
            "INSERT OR IGNORE INTO fact_post (post_id, fullname) VALUES (?,?)",
            (post_id, f"t3_{post_id}"),
        )

    # ---------------- GOLD: facts ----------------
    def upsert_post(self, p: dict, subreddit_id: str | None = None) -> None:
        post_id = p.get("id") or _short_id(p.get("name"))
        if not post_id:
            return
        sid = subreddit_id or _short_id(p.get("subreddit_id"))
        author = p.get("author")
        if author == "[deleted]":
            author = None
        self._stub_subreddit(sid, p.get("subreddit"))
        self._stub_author(author)
        self._upsert("fact_post", {
            "post_id": post_id,
            "fullname": p.get("name") or f"t3_{post_id}",
            "subreddit_id": sid,
            "author_name": author,
            "created_date_key": self._ensure_date(p.get("created_utc")),
            "created_utc": p.get("created_utc"),
            "title": p.get("title"),
            "selftext": p.get("selftext"),
            "url": p.get("url"),
            "domain": p.get("domain"),
            "permalink": p.get("permalink"),
            "is_self": int(bool(p.get("is_self"))),
            "over_18": int(bool(p.get("over_18"))),
            "is_video": int(bool(p.get("is_video"))),
            "link_flair_text": p.get("link_flair_text"),
            "score": p.get("score"),
            "ups": p.get("ups"),
            "upvote_ratio": p.get("upvote_ratio"),
            "num_comments": p.get("num_comments"),
            "num_crossposts": p.get("num_crossposts"),
            "total_awards": p.get("total_awards_received"),
            "fetched_at": time.time(),
        }, preserve_existing_on_null=True)
        self.upsert_post_media(p, post_id=post_id)

    def upsert_comment(self, c: dict, post_id: str, subreddit_id: str | None = None) -> None:
        comment_id = c.get("id") or _short_id(c.get("name"))
        if not comment_id:
            return
        sid = subreddit_id or _short_id(c.get("subreddit_id"))
        author = c.get("author")
        if author == "[deleted]":
            author = None
        self._stub_post(post_id)
        self._stub_subreddit(sid, c.get("subreddit"))
        self._stub_author(author)
        self._upsert("fact_comment", {
            "comment_id": comment_id,
            "fullname": c.get("name") or f"t1_{comment_id}",
            "post_id": post_id,
            "subreddit_id": sid,
            "author_name": author,
            "parent_fullname": c.get("_parent_id") or c.get("parent_id"),
            "created_date_key": self._ensure_date(c.get("created_utc")),
            "created_utc": c.get("created_utc"),
            "depth": c.get("_depth"),
            "body": c.get("body"),
            "score": c.get("score"),
            "ups": c.get("ups"),
            "controversiality": c.get("controversiality"),
            "total_awards": c.get("total_awards_received"),
            "is_submitter": int(bool(c.get("is_submitter"))),
            "fetched_at": time.time(),
        }, preserve_existing_on_null=True)

    def snapshot_metrics(self, p: dict) -> None:
        """Ghi 1 quan sát score/comment tại thời điểm hiện tại (phát hiện tin nóng)."""
        post_id = p.get("id") or _short_id(p.get("name"))
        if not post_id:
            return
        self._stub_post(post_id)
        self.conn.execute(
            "INSERT OR IGNORE INTO fact_post_metrics "
            "(post_id, observed_at, score, ups, upvote_ratio, num_comments) "
            "VALUES (?,?,?,?,?,?)",
            (post_id, round(time.time()), p.get("score"), p.get("ups"),
             p.get("upvote_ratio"), p.get("num_comments")),
        )

    def upsert_article_content(self, row: dict[str, Any]) -> None:
        post_id = row.get("post_id")
        if not post_id:
            return
        self._stub_post(post_id)
        self._upsert("fact_article_content", {
            "post_id": post_id,
            "source_url": row.get("source_url"),
            "final_url": row.get("final_url"),
            "title": row.get("title"),
            "author": row.get("author"),
            "published_at": row.get("published_at"),
            "language": row.get("language"),
            "body_text": row.get("body_text"),
            "word_count": row.get("word_count"),
            "status": row.get("status") or "error",
            "http_status": row.get("http_status"),
            "fetched_at": row.get("fetched_at") or time.time(),
            "error": row.get("error"),
        }, preserve_existing_on_null=True)

    def upsert_post_media(
        self, p: dict, *, post_id: str | None = None, article_image: str | None = None,
    ) -> None:
        post_id = post_id or p.get("id") or _short_id(p.get("name"))
        if not post_id:
            return
        preview = p.get("preview") if isinstance(p.get("preview"), dict) else {}
        images = preview.get("images") if isinstance(preview, dict) else []
        source = {}
        if isinstance(images, list) and images and isinstance(images[0], dict):
            source = images[0].get("source") or {}
        image_url = _safe_image_url(article_image) or _safe_image_url(source.get("url"))
        direct_url = p.get("url") or ""
        if not image_url and isinstance(direct_url, str) and direct_url.lower().split("?")[0].endswith(
            (".jpg", ".jpeg", ".png", ".webp", ".gif")
        ):
            image_url = _safe_image_url(direct_url)
        thumbnail = p.get("thumbnail")
        if thumbnail in {None, "", "self", "default", "nsfw", "spoiler", "image"}:
            thumbnail = None
        thumbnail = _safe_image_url(thumbnail)
        if not image_url and not thumbnail:
            return
        self._stub_post(post_id)
        self._upsert("fact_post_media", {
            "post_id": post_id,
            "image_url": image_url,
            "thumbnail_url": thumbnail,
            "media_type": "image" if image_url else "thumbnail",
            "width": source.get("width") if isinstance(source, dict) else None,
            "height": source.get("height") if isinstance(source, dict) else None,
            "fetched_at": time.time(),
        }, preserve_existing_on_null=True)

    def upsert_ai_digest(self, row: dict[str, Any]) -> None:
        self._upsert("ai_digest", row, preserve_existing_on_null=True)

    def upsert_ai_post_analysis(self, row: dict[str, Any]) -> None:
        post_id = row.get("post_id")
        if not post_id:
            return
        self._stub_post(post_id)
        persisted = {column: row.get(column) for column in _ANALYSIS_COLUMNS}
        self._upsert("ai_post_analysis", persisted)

    def upsert_ai_post_analysis_v2(self, row: dict[str, Any]) -> None:
        post_id = row.get("post_id")
        if not post_id:
            return
        self._stub_post(post_id)
        persisted = {column: row.get(column) for column in _ANALYSIS_COLUMNS}
        self._upsert("ai_post_analysis_v2", persisted)

    def upsert_extracted_resource(self, row: dict[str, Any]) -> None:
        missing = [key for key in ("resource_id", "url", "resource_type") if not row.get(key)]
        if missing:
            raise ValueError("extracted resource thiếu field bắt buộc: " + ", ".join(missing))
        post_id = row.get("post_id")
        if post_id:
            self._stub_post(post_id)
        persisted = {key: row.get(key) for key in _EXTRACTED_RESOURCE_COLUMNS}
        self._upsert(
            "fact_extracted_resource", persisted, preserve_existing_on_null=True,
        )

    def set_enrichment_state(
        self, post_id: str, kind: str, status: str, error: str | None = None,
    ) -> None:
        now = time.time()
        self._stub_post(post_id)
        self._upsert("enrichment_state", {
            "post_id": post_id,
            "kind": kind,
            "status": status,
            "attempted_at": now,
            "completed_at": now if status in {"success", "blocked", "empty"} else None,
            "error": error,
        })

    # ---------------- crawl state (incremental) ----------------
    def get_state(self, scope: str) -> dict | None:
        cur = self.conn.execute(
            "SELECT scope, last_utc, last_fullname, updated_at FROM crawl_state WHERE scope=?",
            (scope,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"scope": row[0], "last_utc": row[1], "last_fullname": row[2], "updated_at": row[3]}

    def recent_post_fullnames(self, hours: float = 48) -> list[str]:
        """Fullname các post tạo trong `hours` giờ gần đây (để refresh metrics)."""
        cutoff = time.time() - hours * 3600
        cur = self.conn.execute(
            "SELECT fullname FROM fact_post "
            "WHERE created_utc >= ? AND fullname IS NOT NULL "
            "ORDER BY created_utc DESC",
            (cutoff,),
        )
        return [r[0] for r in cur.fetchall()]

    def set_state(self, scope: str, last_utc: float | None, last_fullname: str | None) -> None:
        self._upsert("crawl_state", {
            "scope": scope,
            "last_utc": last_utc,
            "last_fullname": last_fullname,
            "updated_at": time.time(),
        })

    def delete_state(self, scope: str) -> None:
        self.conn.execute("DELETE FROM crawl_state WHERE scope=?", (scope,))

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def counts(self) -> dict[str, int]:
        cur = self.conn.cursor()
        out = {}
        for t in (
            "dim_subreddit", "dim_author", "dim_date", "fact_post", "fact_comment",
            "fact_post_metrics", "crawl_state",
            "fact_article_content", "enrichment_state",
            "fact_post_media", "ai_digest",
            "ai_post_analysis", "ai_post_analysis_v2", "fact_extracted_resource",
        ):
            try:
                out[t] = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception:
                out[t] = 0
        return out
