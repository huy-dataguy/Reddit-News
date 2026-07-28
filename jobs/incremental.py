"""Incremental crawl cho cron — chỉ lấy post MỚI hơn lần chạy trước.

Ý tưởng (tránh trần 1000 item của Reddit):
  1. Với mỗi sub, duyệt /new (mới -> cũ).
  2. DỪNG ngay khi gặp post có created_utc <= mốc (high-water mark) đã lưu ở
     bảng crawl_state — nghĩa là đã tới vùng "đã thấy".
  3. Lưu lại mốc = created_utc mới nhất của lần chạy này.
  4. (tùy chọn) Refresh score của các post gần đây qua /api/info theo lô 100
     -> ghi fact_post_metrics để tính velocity = Δscore/Δt (phát hiện tin nóng).

Chạy cứ 15-30 phút một lần bằng cron là gom dần được dòng tin mới, đều đặn,
không bao giờ phải cào lại từ đầu.

Dùng trực tiếp:
    python -m jobs.incremental                # đọc jobs/subs.txt
    python -m jobs.incremental --only technology,programming
    python cli.py incremental --comments      # (đã wrap trong cli.py)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from reddit_crawler import RedditClient, Storage, crawl
from reddit_crawler.auth import DEFAULT_CLIENT_ID, TokenManager
from reddit_crawler.config import load_dotenv
from reddit_crawler.storage import _short_id

log = logging.getLogger("incremental")

DEFAULT_SUBS = Path(__file__).with_name("subs.txt")

load_dotenv()


def load_subs(path: str | os.PathLike) -> list[str]:
    subs: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        subs.append(line.removeprefix("r/").lstrip("/"))
    return subs


def _make_client() -> RedditClient:
    ua = os.environ.get("REDDIT_USER_AGENT",
                        "python:news-aggregator-research:v0.1 (by /u/your_username)")
    tm = TokenManager(user_agent=ua,
                      client_id=os.environ.get("REDDIT_CLIENT_ID", DEFAULT_CLIENT_ID))
    return RedditClient(user_agent=ua, token_manager=tm)


def run_incremental(
    client: RedditClient,
    store: Storage,
    subs: list[str],
    sort: str = "new",
    comments: bool = False,
    depth: int | None = None,
    resolve_more: bool = False,
    refresh_hours: float = 48,
    max_per_sub: int | None = None,
) -> dict[str, int]:
    """Trả về {'new_posts', 'comments', 'refreshed'}."""
    total_new = total_cmt = 0

    for sub in subs:
        scope = f"sub:{sub}:{sort}"
        state = store.get_state(scope) or {}
        bootstrap_scope = f"bootstrap:{scope}"
        bootstrap = store.get_state(bootstrap_scope)
        hwm = state.get("last_utc")                 # mốc lần trước
        newest_utc = hwm
        newest_name = state.get("last_fullname")

        try:
            sub_meta = crawl.fetch_subreddit(client, sub)
            store.upsert_subreddit(sub_meta)
            sub_id = sub_meta.get("id")
        except Exception as e:                       # sub riêng tư/cấm/gõ sai
            log.warning("bỏ qua r/%s: %s", sub, e)
            continue

        n_new = 0
        # --max-per-sub chỉ chia nhỏ lần bootstrap đầu. Mốc chính chỉ được lưu
        # sau khi bootstrap hoàn tất, nên các trang cũ không bị bỏ vĩnh viễn.
        start_after = bootstrap.get("last_fullname") if bootstrap else None
        bootstrap_hwm = bootstrap.get("last_utc") if bootstrap else None
        listing_limit = (max_per_sub + 1) if hwm is None and max_per_sub else None
        listing = crawl.iter_listing(
            client, sub, sort=sort, max_items=listing_limit, start_after=start_after,
        )
        has_more_bootstrap = False
        last_processed_name = start_after
        for post in listing:
            if hwm is None and max_per_sub and n_new >= max_per_sub:
                has_more_bootstrap = True
                break
            cu = post.get("created_utc") or 0
            if hwm is not None and cu <= hwm:
                break                                # tới vùng cũ -> dừng sub này
            store.upsert_post(post, subreddit_id=sub_id)
            store.snapshot_metrics(post)
            store.write_raw("post", [post])
            n_new += 1
            last_processed_name = post.get("name")
            if newest_utc is None or cu > newest_utc:
                newest_utc, newest_name = cu, post.get("name")
            if bootstrap_hwm is None or cu > bootstrap_hwm:
                bootstrap_hwm = cu

            if comments:
                try:
                    _, cmts = crawl.fetch_post_with_comments(
                        client, post["id"], subreddit=sub,
                        sort="top", depth=depth, resolve_more=resolve_more)
                    for c in cmts:
                        store.upsert_comment(c, post_id=post["id"], subreddit_id=sub_id)
                    store.write_raw("comment", cmts)
                    total_cmt += len(cmts)
                except Exception as e:
                    log.warning("comment lỗi ở post %s: %s", post.get("id"), e)

        if hwm is None and max_per_sub and has_more_bootstrap:
            store.set_state(bootstrap_scope, bootstrap_hwm, last_processed_name)
            log.info("r/%s bootstrap tạm dừng tại %s; lần sau sẽ nối tiếp", sub,
                     last_processed_name)
        else:
            final_hwm = bootstrap_hwm if hwm is None and max_per_sub else newest_utc
            store.set_state(scope, final_hwm, newest_name)
            store.delete_state(bootstrap_scope)
        store.commit()
        total_new += n_new
        log.info("r/%-20s +%d post mới", sub, n_new)

    # ---- refresh metrics cho post gần đây (velocity) ----
    refreshed = 0
    if refresh_hours > 0:
        recent = store.recent_post_fullnames(hours=refresh_hours)
        if recent:
            for thing in crawl.info_by_ids(client, recent):
                store.upsert_post(thing, subreddit_id=_short_id(thing.get("subreddit_id")))
                store.snapshot_metrics(thing)
                refreshed += 1
            store.commit()
            log.info("refresh metrics: %d post gần đây (<= %gh)", refreshed, refresh_hours)

    return {"new_posts": total_new, "comments": total_cmt, "refreshed": refreshed}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Incremental crawl (cron) cho danh sách sub")
    ap.add_argument("--subs-file", default=str(DEFAULT_SUBS), help="file danh sách sub")
    ap.add_argument("--only", help="ghi đè: danh sách sub ngăn cách bằng dấu phẩy")
    ap.add_argument("--sort", default="new", choices=["new"])
    ap.add_argument("--comments", action="store_true", help="lấy luôn cây comment (nặng)")
    ap.add_argument("--depth", type=int, default=None)
    ap.add_argument("--no-more", action="store_true", help="không bung comment ẩn")
    ap.add_argument("--refresh-hours", type=float, default=48,
                    help="refresh metrics post trong N giờ qua (0 = tắt)")
    ap.add_argument("--max-per-sub", type=int, default=None,
                    help="giới hạn post/sub mỗi lần (bỏ trống = tới mốc; hữu ích cho lần đầu)")
    ap.add_argument("--db", default="reddit.db")
    ap.add_argument("--raw", default="raw")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    subs = ([s.strip() for s in args.only.split(",") if s.strip()]
            if args.only else load_subs(args.subs_file))
    log.info("Incremental %d sub: %s", len(subs), ", ".join(subs))

    client = _make_client()
    store = Storage(db_path=args.db, raw_dir=args.raw or None)
    try:
        res = run_incremental(
            client, store, subs, sort=args.sort, comments=args.comments,
            depth=args.depth, resolve_more=not args.no_more,
            refresh_hours=args.refresh_hours, max_per_sub=args.max_per_sub)
    finally:
        store.close()
    log.info("XONG: +%d post mới, %d comment, %d refresh",
             res["new_posts"], res["comments"], res["refreshed"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
