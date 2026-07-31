#!/usr/bin/env python3
"""CLI cho bộ tool crawl Reddit.

Ví dụ:
  python cli.py probe
  python cli.py find-subs "artificial intelligence"
  python cli.py crawl-sub technology --sort top --time week --max 50 --comments
  python cli.py crawl-post https://www.reddit.com/r/codex/comments/1rfgu9a/...
  python cli.py crawl-user spez
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from reddit_crawler import RedditClient, Storage, crawl
from reddit_crawler.auth import DEFAULT_CLIENT_ID, TokenManager
from reddit_crawler.config import load_dotenv

load_dotenv()

DEFAULT_UA = os.environ.get(
    "REDDIT_USER_AGENT",
    "python:news-aggregator-research:v0.1 (by /u/your_username)",
)


def make_client() -> RedditClient:
    tm = TokenManager(
        user_agent=DEFAULT_UA,
        client_id=os.environ.get("REDDIT_CLIENT_ID", DEFAULT_CLIENT_ID),
    )
    return RedditClient(user_agent=DEFAULT_UA, token_manager=tm)


# --------------------------------------------------------------------------- commands
def cmd_probe(_: argparse.Namespace) -> None:
    client = make_client()
    print("Token OK:", client.tm.token()[:24], "...")
    posts = list(crawl.iter_listing(client, "technology", sort="hot", max_items=3))
    print(f"Lấy thử {len(posts)} post từ r/technology:")
    for p in posts:
        print(f"  • [{p['score']:>6}⬆ {p['num_comments']:>4}💬] {p['title'][:70]}")


def cmd_find_subs(args: argparse.Namespace) -> None:
    client = make_client()
    for s in crawl.search_subreddits(client, args.query, limit=args.limit):
        subs = s.get("subscribers") or 0
        print(f"  r/{s['display_name']:<28} {subs:>12,} subs  | {(s.get('public_description') or '')[:50]}")


def cmd_crawl_sub(args: argparse.Namespace) -> None:
    client = make_client()
    store = Storage(db_path=args.db, raw_dir=args.raw)

    sub_meta = crawl.fetch_subreddit(client, args.subreddit)
    store.upsert_subreddit(sub_meta)
    store.write_raw("subreddit", [sub_meta])
    sub_id = sub_meta.get("id")
    print(f"r/{args.subreddit}: {sub_meta.get('subscribers'):,} subs")

    n_posts = n_comments = 0
    for post in crawl.iter_listing(client, args.subreddit, sort=args.sort, t=args.time, max_items=args.max):
        store.upsert_post(post, subreddit_id=sub_id)
        store.write_raw("post", [post])
        n_posts += 1

        if args.comments:
            _, comments = crawl.fetch_post_with_comments(
                client, post["id"], subreddit=args.subreddit,
                sort="top", depth=args.depth, resolve_more=not args.no_more,
            )
            for c in comments:
                store.upsert_comment(c, post_id=post["id"], subreddit_id=sub_id)
            store.write_raw("comment", comments)
            n_comments += len(comments)

        if n_posts % 25 == 0:
            store.commit()
            print(f"  ... {n_posts} post, {n_comments} comment")

    store.close()
    print(f"XONG. {n_posts} post, {n_comments} comment -> {args.db}")


def cmd_crawl_post(args: argparse.Namespace) -> None:
    client = make_client()
    store = Storage(db_path=args.db, raw_dir=args.raw)
    post, comments = crawl.fetch_post_with_comments(
        client, args.url, sort="top", depth=args.depth, resolve_more=not args.no_more,
    )
    sub_id = post.get("subreddit_id", "").split("_")[-1] or None
    store.upsert_post(post, subreddit_id=sub_id)
    store.write_raw("post", [post])
    for c in comments:
        store.upsert_comment(c, post_id=post["id"], subreddit_id=sub_id)
    store.write_raw("comment", comments)
    store.close()
    print(f"'{post['title'][:60]}' -> {len(comments)} comment vào {args.db}")


def cmd_crawl_user(args: argparse.Namespace) -> None:
    client = make_client()
    store = Storage(db_path=args.db, raw_dir=args.raw)
    u = crawl.fetch_user(client, args.name)
    if not u:
        print(f"Không lấy được user {args.name} (bị xóa/suspend?)")
        return
    store.upsert_author(u)
    store.write_raw("user", [u])
    store.close()
    print(f"u/{u['name']}: karma={u.get('total_karma'):,}  tạo={u.get('created_utc')}")


def cmd_stats(args: argparse.Namespace) -> None:
    store = Storage(db_path=args.db, raw_dir=None)
    for t, n in store.counts().items():
        print(f"  {t:<16} {n:>8,}")
    store.close()


def cmd_incremental(args: argparse.Namespace) -> None:
    from jobs.incremental import load_subs, run_incremental
    subs = ([s.strip() for s in args.only.split(",") if s.strip()]
            if args.only else load_subs(args.subs_file))
    print(f"Incremental {len(subs)} sub: {', '.join(subs)}")
    client = make_client()
    store = Storage(db_path=args.db, raw_dir=args.raw)
    try:
        res = run_incremental(
            client, store, subs, sort=args.sort, comments=args.comments,
            depth=args.depth, resolve_more=not args.no_more,
            refresh_hours=args.refresh_hours, max_per_sub=args.max_per_sub)
    finally:
        store.close()
    print(f"XONG: +{res['new_posts']} post mới, {res['comments']} comment, "
          f"{res['refreshed']} refresh -> {args.db}")


def cmd_backfill(args: argparse.Namespace) -> None:
    import time as _time
    from jobs.backfill_arctic_shift import backfill_comments, backfill_posts, to_epoch, _ymd
    after = to_epoch(args.after)
    before = to_epoch(args.before) if args.before else int(_time.time())
    if after >= before:
        print("Lỗi: --after phải nhỏ hơn --before"); return
    print(f"Backfill r/{args.subreddit} [{_ymd(after)} → {_ymd(before)}] kind={args.kind}")
    store = Storage(db_path=args.db, raw_dir=args.raw)
    try:
        if args.kind in ("posts", "both"):
            n = backfill_posts(store, args.subreddit, after, before, args.sleep, args.max_pages)
            print(f"POSTS xong: {n:,} bản ghi")
        if args.kind in ("comments", "both"):
            n = backfill_comments(store, args.subreddit, after, before, args.sleep, args.max_pages)
            print(f"COMMENTS xong: {n:,} bản ghi")
    finally:
        store.close()


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn
    uvicorn.run("web.app:app", host=args.host, port=args.port, reload=args.reload)


def cmd_analyze_post(args: argparse.Namespace) -> None:
    from reddit_crawler.llm import generate_post_analysis_v2
    result = generate_post_analysis_v2(
        args.db, args.post_id, provider=args.provider, comment_limit=args.comment_limit,
    )
    print(
        f"Post analysis V2 {result['post_id']}: provider={result['provider']} "
        f"model={result['model'] or '-'} input={result['input_tokens']} "
        f"output={result['output_tokens']}"
    )


def cmd_enrich(args: argparse.Namespace) -> int:
    from jobs.enrich import run_enrichment

    result = run_enrichment(
        args.db,
        raw_dir=args.raw,
        period=args.period,
        limit=args.limit,
        depth=args.depth,
        kind=args.kind,
        retry_after_hours=args.retry_after_hours,
        backlog=getattr(args, "backlog", False),
        stream=getattr(args, "stream", "all"),
    )
    print(
        f"Enrich {result['period']}: {result['comments_fetched']} comment, "
        f"{result['resources_extracted']} resource, {result['failed']} lỗi"
    )
    return 2 if result["failed"] else 0


def cmd_analyze_top(args: argparse.Namespace) -> int:
    from jobs.pipeline import analyze_top_posts_v2

    result = analyze_top_posts_v2(
        args.db,
        args.period,
        limit=args.limit,
        provider=args.provider,
        comment_limit=args.comment_limit,
        freshness_hours=args.freshness_hours,
    )
    print(
        f"Analyze V2 {result['period']}: {result['analyzed']} mới, "
        f"{result['skipped_fresh']} còn tươi, "
        f"{result['skipped_no_comments']} thiếu comment, {result['failed']} lỗi"
    )
    return 2 if (
        result["failed"]
        or (args.provider == "auto" and result.get("provisional"))
    ) else 0


def cmd_ai_digest(args: argparse.Namespace) -> int:
    from reddit_crawler.llm import generate_digest

    result = generate_digest(
        args.db,
        period=args.period,
        provider=args.provider,
        limit=args.limit,
    )
    print(
        f"Digest {result['digest_id']}: provider={result['provider']} "
        f"model={result['model'] or '-'} input={result['input_tokens']} "
        f"output={result['output_tokens']}"
    )
    return 2 if (
        args.provider == "auto" and result.get("artifact_kind") == "provisional"
    ) else 0


def cmd_gemini_backlog(args: argparse.Namespace) -> int:
    from jobs.gemini_backlog import run_gemini_backlog_batch

    result = run_gemini_backlog_batch(
        args.db,
        limit=args.limit,
        comment_limit=args.comment_limit,
        retry_after_hours=args.retry_after_hours,
        max_attempts=args.max_attempts,
        output_dir=args.output,
    )
    queue = result["queue"]
    print(
        f"Gemini backlog: {result['succeeded']}/{result['attempted']} thành công, "
        f"{result['failed']} lỗi; hoàn tất {queue['completed']}/{queue['eligible']}, "
        f"còn {queue['remaining']}, blocked {queue['blocked']} -> "
        f"{result['report_json_path']}"
    )
    return 2 if result["failed"] or queue["blocked"] else 0


def cmd_report(args: argparse.Namespace) -> None:
    from jobs.report import create_report

    result = create_report(
        args.db,
        args.period,
        limit=args.limit,
        output_dir=args.output,
    )
    print(
        f"Report {args.period}: {len(result['report']['items'])} tín hiệu -> "
        f"{result['markdown_path']} + {result['json_path']}"
    )


def cmd_pipeline(args: argparse.Namespace) -> int:
    from jobs.pipeline import run_pipeline

    result = run_pipeline(
        db_path=args.db,
        raw_dir=args.raw,
        period=args.period,
        enrich_limit=args.enrich_limit,
        analysis_limit=args.analysis_limit,
        digest_limit=args.digest_limit,
        provider=args.provider,
        report_dir=args.output,
        depth=args.depth,
        comment_limit=args.comment_limit,
        freshness_hours=args.freshness_hours,
    )
    stages = result["stages"]
    print(
        f"Pipeline {result['status']}: comments={stages['enrich']['comments_fetched']}, "
        f"resources={stages['enrich']['resources_extracted']}, "
        f"analysis={stages['analyze']['analyzed']}, "
        f"digest={stages['digest']['digest_id']}, "
        f"report={stages['report']['markdown_path']}"
    )
    return 0 if result["status"] == "success" else 2


def cmd_story_export(args: argparse.Namespace) -> None:
    from jobs.export_story import export_story
    export_story(
        args.db,
        post_id=args.post_id,
        period=args.period,
        studio=args.studio,
        dry_check=args.dry_check,
    )


# --------------------------------------------------------------------------- ops commands
def cmd_ops(args: argparse.Namespace) -> int:
    """Dispatcher for 'ops' subcommands."""
    if not hasattr(args, "ops_cmd") or args.ops_cmd is None:
        print("Usage: cli.py ops <backup|restore-drill|check-permissions|capacity-check|verify-units>")
        return 1

    if args.ops_cmd == "backup":
        return _cmd_ops_backup(args)
    elif args.ops_cmd == "restore-drill":
        return _cmd_ops_restore_drill(args)
    elif args.ops_cmd == "check-permissions":
        return _cmd_ops_check_permissions(args)
    elif args.ops_cmd == "capacity-check":
        return _cmd_ops_capacity_check(args)
    elif args.ops_cmd == "verify-units":
        return _cmd_ops_verify_units(args)
    else:
        print(f"Unknown ops subcommand: {args.ops_cmd}")
        return 1


def _cmd_ops_backup(args: argparse.Namespace) -> int:
    from reddit_crawler.ops.backup import backup_db
    db_path = getattr(args, "db", os.environ.get("REDDIT_DB_PATH", "reddit.db"))
    backup_dir = args.backup_dir
    dry_run = args.dry_run

    if dry_run:
        print(f"[dry-run] Would backup: {db_path} → {backup_dir}/")
    else:
        print(f"Creating backup: {db_path} → {backup_dir}/")

    manifest = backup_db(db_path, backup_dir, dry_run=dry_run)
    print(f"  backup_id:    {manifest.backup_id}")
    print(f"  artifact:     {manifest.artifact}")
    print(f"  sha256:       {manifest.sha256}")
    print(f"  size_bytes:   {manifest.size_bytes:,}")
    print(f"  quick_check:  {manifest.quick_check}")
    print(f"  fk_check:     {manifest.foreign_key_check}")
    print(f"  schema_ver:   {manifest.source_schema_version}")
    if dry_run:
        print("[dry-run] No files created.")
    return 0


def _cmd_ops_restore_drill(args: argparse.Namespace) -> int:
    from reddit_crawler.ops.backup import restore_drill
    backup_dir = args.backup_dir
    print(f"Running restore drill from: {backup_dir}")
    result = restore_drill(backup_dir)
    status = result["drill_result"]
    print(f"  result:       {status}")
    print(f"  quick_check:  {result['quick_check']}")
    print(f"  fk_check:     {result['foreign_key_check']}")
    print(f"  sha256_ok:    {result['sha256_verified']}")
    print(f"  duration_s:   {result['duration_seconds']}")
    print(f"  live_db_safe: {result['live_db_untouched']}")
    if result.get("counts"):
        print(f"  counts:       {result['counts']}")
    return 0 if status == "pass" else 1


def _cmd_ops_check_permissions(args: argparse.Namespace) -> int:
    from reddit_crawler.ops.permissions import check_permissions
    result = check_permissions(".")
    print(f"Permission scan — overall: {result['overall']}")
    print(f"  violations: {result['violations']}, warnings: {result['warnings']}")
    for finding in result["findings"]:
        prefix = "  [OK]  " if finding["severity"] == "ok" else f"  [{finding['severity'].upper()}] "
        print(f"{prefix}{finding['message']}")
    print(f"\nNote: {result['note']}")
    return 0 if result["overall"] in ("ok", "warning") else 1


def _cmd_ops_capacity_check(args: argparse.Namespace) -> int:
    from reddit_crawler.ops.capacity import capacity_check
    path = getattr(args, "path", ".")
    gate = capacity_check(path)
    print(f"Capacity check for: {gate.filesystem}")
    print(f"  status:     {gate.status.value}")
    print(f"  free:       {gate.free_gib:.1f} GiB ({gate.free_pct:.1f}%)")
    print(f"  total:      {gate.total_bytes / (1024**3):.1f} GiB")
    print(f"  stop_batch: {gate.should_stop_batch}")
    print(f"  hard_stop:  {gate.should_hard_stop}")
    print(f"  message:    {gate.message}")
    return 0 if not gate.should_hard_stop else 1


def _cmd_ops_verify_units(args: argparse.Namespace) -> int:
    """Verify systemd unit files in deploy/systemd/."""
    import hashlib
    import subprocess
    from pathlib import Path

    units_dir = Path("deploy/systemd")
    if not units_dir.exists():
        print("No deploy/systemd/ directory found")
        return 1

    units = sorted(units_dir.glob("*.service")) + sorted(units_dir.glob("*.timer"))
    if not units:
        print("No unit files found in deploy/systemd/")
        return 1

    print(f"Verifying {len(units)} unit file(s):")
    for unit in units:
        digest = hashlib.sha256(unit.read_bytes()).hexdigest()[:16]
        print(f"  {unit.name:<45} sha256=...{digest}")

    if getattr(args, "require_localhost", False):
        # Check web service doesn't bind to 0.0.0.0
        web_units = [u for u in units if "web" in u.name]
        for wu in web_units:
            content = wu.read_text(encoding="utf-8")
            if "0.0.0.0" in content:
                print(f"  VIOLATION: {wu.name} contains 0.0.0.0 binding")
                return 1
        print("  localhost binding: ok (no 0.0.0.0 found in web units)")

    # Try systemd-analyze verify if available
    try:
        result = subprocess.run(
            ["systemd-analyze", "verify"] + [str(u) for u in units],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            print("  systemd-analyze verify: ok")
        else:
            print(f"  systemd-analyze verify: warnings (exit {result.returncode})")
            if result.stderr:
                print(result.stderr[:500])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("  systemd-analyze: not available (skipped)")

    return 0


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Reddit OAuth crawler")
    p.add_argument(
        "--db", default=os.environ.get("REDDIT_DB_PATH", "reddit.db"),
        help="đường dẫn SQLite (mặc định REDDIT_DB_PATH hoặc reddit.db)",
    )
    p.add_argument("--raw", default="raw", help="thư mục JSONL raw (đặt '' để tắt)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("probe", help="kiểm tra token + lấy thử vài post").set_defaults(func=cmd_probe)

    sp = sub.add_parser("find-subs", help="tìm subreddit theo chủ đề")
    sp.add_argument("query"); sp.add_argument("--limit", type=int, default=25)
    sp.set_defaults(func=cmd_find_subs)

    sp = sub.add_parser("crawl-sub", help="crawl post (và comment) trong 1 subreddit")
    sp.add_argument("subreddit")
    sp.add_argument("--sort", default="new", choices=["new", "hot", "top", "rising", "controversial"])
    sp.add_argument("--time", default=None, choices=["hour", "day", "week", "month", "year", "all"])
    sp.add_argument("--max", type=int, default=100)
    sp.add_argument("--comments", action="store_true", help="lấy luôn cây comment mỗi post")
    sp.add_argument("--depth", type=int, default=None)
    sp.add_argument("--no-more", action="store_true", help="không bung comment ẩn (nhanh hơn)")
    sp.set_defaults(func=cmd_crawl_sub)

    sp = sub.add_parser("crawl-post", help="crawl 1 post + toàn bộ comment")
    sp.add_argument("url", help="URL hoặc id của post")
    sp.add_argument("--depth", type=int, default=None)
    sp.add_argument("--no-more", action="store_true")
    sp.set_defaults(func=cmd_crawl_post)

    sp = sub.add_parser("crawl-user", help="crawl thông tin 1 user")
    sp.add_argument("name"); sp.set_defaults(func=cmd_crawl_user)

    sp = sub.add_parser("incremental", help="cron: lấy post MỚI cho danh sách sub + snapshot metrics")
    sp.add_argument("--subs-file", default="jobs/subs.txt")
    sp.add_argument("--only", help="ghi đè danh sách sub (ngăn cách bằng dấu phẩy)")
    sp.add_argument("--sort", default="both", choices=["new", "hot", "both"],
                    help="thứ tự crawl: new (24h), hot (báo sức nóng), hoặc both")
    sp.add_argument("--comments", action="store_true", help="lấy luôn cây comment (nặng)")
    sp.add_argument("--depth", type=int, default=None)
    sp.add_argument("--no-more", action="store_true")
    sp.add_argument("--refresh-hours", type=float, default=48,
                    help="refresh metrics post trong N giờ qua (0 = tắt)")
    sp.add_argument("--max-per-sub", type=int, default=None,
                    help="giới hạn post/sub mỗi lần (hữu ích cho lần chạy đầu)")
    sp.set_defaults(func=cmd_incremental)

    sp = sub.add_parser("backfill", help="nạp lịch sử qua Arctic Shift (vượt trần 1000)")
    sp.add_argument("subreddit")
    sp.add_argument("--after", required=True, help="mốc bắt đầu YYYY-MM-DD hoặc epoch")
    sp.add_argument("--before", default=None, help="mốc kết thúc (mặc định: bây giờ)")
    sp.add_argument("--kind", default="posts", choices=["posts", "comments", "both"])
    sp.add_argument("--sleep", type=float, default=1.0)
    sp.add_argument("--max-pages", type=int, default=100_000)
    sp.set_defaults(func=cmd_backfill)

    sp = sub.add_parser("serve", help="chạy API và website")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8080)
    sp.add_argument("--reload", action="store_true")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("enrich", help="lấy comment còn thiếu và bóc tách resource cho top signal")
    sp.add_argument("--period", default="day", choices=["3h", "day", "week", "month", "year"])
    sp.add_argument("--kind", default="both", choices=["comments", "resources", "both"])
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--depth", type=int, default=4)
    sp.add_argument("--retry-after-hours", type=float, default=6)
    sp.add_argument(
        "--backlog",
        action="store_true",
        help="duyệt toàn bộ post còn thiếu enrichment thay vì chỉ top signal",
    )
    sp.add_argument("--stream", default="all", choices=["all", "new", "hot"],
                    help="lọc luồng post để enrich: all, new (24h), hoặc hot (nổi bật)")
    sp.set_defaults(func=cmd_enrich)

    sp = sub.add_parser("analyze-post", help="đúc kết một discussion theo PostAnalysis V2")
    sp.add_argument("post_id")
    sp.add_argument("--provider", default="auto", choices=["auto", "gemini", "openai", "local"])
    sp.add_argument("--comment-limit", type=int, default=120)
    sp.set_defaults(func=cmd_analyze_post)

    sp = sub.add_parser("analyze-top", help="phân tích V2 theo batch các discussion nổi bật")
    sp.add_argument("--period", default="day", choices=["3h", "day", "week", "month", "year"])
    sp.add_argument("--limit", type=int, default=5)
    sp.add_argument("--provider", default="auto", choices=["auto", "gemini", "openai", "local"])
    sp.add_argument("--comment-limit", type=int, default=120)
    sp.add_argument("--freshness-hours", type=float, default=24)
    sp.set_defaults(func=cmd_analyze_top)

    sp = sub.add_parser("ai-digest", help="tạo briefing có nguồn cho một cửa sổ thời gian")
    sp.add_argument("--period", default="3h", choices=["3h", "day", "week", "month", "year"])
    sp.add_argument("--provider", default="auto", choices=["auto", "gemini", "openai", "local"])
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_ai_digest)

    sp = sub.add_parser(
        "gemini-backlog",
        help="xử lý một batch toàn bộ discussion còn thiếu Gemini V2",
    )
    sp.add_argument("--limit", type=int, default=5, help="trần Gemini calls trong run")
    sp.add_argument("--comment-limit", type=int, default=120)
    sp.add_argument("--retry-after-hours", type=float, default=6)
    sp.add_argument("--max-attempts", type=int, default=3)
    sp.add_argument("--output", default="reports/operations")
    sp.set_defaults(func=cmd_gemini_backlog)

    sp = sub.add_parser("report", help="ghi briefing và top signal ra Markdown + JSON")
    sp.add_argument("--period", default="day", choices=["3h", "day", "week", "month", "year"])
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--output", default=os.environ.get("REPORT_DIR", "reports"))
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("pipeline", help="chạy enrich → V2 analysis → digest → report")
    sp.add_argument("--period", default="day", choices=["3h", "day", "week", "month", "year"])
    sp.add_argument("--enrich-limit", type=int, default=5)
    sp.add_argument("--analysis-limit", type=int, default=3)
    sp.add_argument("--digest-limit", type=int, default=20)
    sp.add_argument("--provider", default="auto", choices=["auto", "gemini", "openai", "local"])
    sp.add_argument("--output", default=os.environ.get("REPORT_DIR", "reports"))
    sp.add_argument("--depth", type=int, default=4)
    sp.add_argument("--comment-limit", type=int, default=120)
    sp.add_argument("--freshness-hours", type=float, default=24)
    sp.set_defaults(func=cmd_pipeline)

    sp = sub.add_parser(
        "story-export",
        help="xuất tin giá trị cao thành story JSON cho MediaWorkflow (make-video.sh)",
    )
    sp.add_argument("--post-id", help="post cụ thể; bỏ trống = --top theo trend_score")
    sp.add_argument("--top", action="store_true", help="tự chọn post trend cao nhất có phân tích AI")
    sp.add_argument("--period", default="week", help="cửa sổ chọn --top (3h/day/week/month/year)")
    sp.add_argument("--studio", help="đường dẫn repo-review-studio (mặc định: MediaWorkflow cạnh Startup)")
    sp.add_argument("--dry-check", action="store_true", help="bỏ qua tải ảnh (dùng fallback), vẫn ghi story")
    sp.set_defaults(func=cmd_story_export)

    sub.add_parser("stats", help="đếm số dòng trong DB").set_defaults(func=cmd_stats)

    # ── ops subcommand group ──────────────────────────────────────────────
    ops_p = sub.add_parser("ops", help="runtime operations: backup, restore-drill, permissions, capacity")
    ops_sub = ops_p.add_subparsers(dest="ops_cmd", metavar="OPS_CMD")
    ops_p.set_defaults(func=cmd_ops)

    sp = ops_sub.add_parser("backup", help="tạo SQLite online backup với manifest và checksum")
    sp.add_argument("--db", default=os.environ.get("REDDIT_DB_PATH", "reddit.db"))
    sp.add_argument("--backup-dir", default="reports/operations/backups")
    sp.add_argument("--dry-run", action="store_true", help="xác nhận mà không ghi file")

    sp = ops_sub.add_parser("restore-drill", help="khôi phục backup mới nhất vào temp path và verify")
    sp.add_argument("--backup-dir", default="reports/operations/backups")
    sp.add_argument("--latest", action="store_true", default=True, help="dùng backup mới nhất")
    sp.add_argument("--temporary", action="store_true", default=True, help="restore vào temp dir")
    sp.add_argument("--verify", action="store_true", default=True, help="chạy quick_check/FK sau restore")

    sp = ops_sub.add_parser("check-permissions", help="quét permission file nhạy cảm (không in giá trị)")
    sp.add_argument("--redact-values", action="store_true", default=True)

    sp = ops_sub.add_parser("capacity-check", help="kiểm tra disk capacity với warning/critical/halt thresholds")
    sp.add_argument("--path", default=".")

    sp = ops_sub.add_parser("verify-units", help="kiểm tra systemd units trong repo")
    sp.add_argument("--installed", action="store_true", help="so sánh digest với unit đã cài")
    sp.add_argument("--require-localhost", action="store_true", help="đảm bảo web bind localhost")

    # WP8 New Commands
    sp = sub.add_parser("layer-status", help="shows Bronze/Silver/Gold/Serving layer health")
    sp.set_defaults(func=cmd_layer_status)

    sp = sub.add_parser("migrate", help="runs additive schema migration")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_migrate)

    sp = sub.add_parser("data-run", help="documented stub for bounded pipeline run")
    sp.set_defaults(func=cmd_data_run)

    return p

def cmd_layer_status(args):
    from reddit_crawler.serving import ServingRepository
    print(ServingRepository(args.db).health())
    return 0

def cmd_migrate(args):
    if not args.apply:
        print("Dry run migration")
        return 0
    import sqlite3
    conn = sqlite3.connect(args.db)
    with open("reddit_crawler/schema.sql") as f:
        conn.executescript(f.read())
    conn.close()
    print("Migration applied")
    return 0

def cmd_data_run(args):
    print("Bounded pipeline run (stub)")
    return 0

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args()
    if args.raw == "":
        args.raw = None
    result = args.func(args)
    return int(result or 0)


if __name__ == "__main__":
    sys.exit(main())
