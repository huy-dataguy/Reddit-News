"""Backfill LỊCH SỬ qua Arctic Shift — vượt trần ~1000 item của Reddit API.

Vì sao cần: listing của Reddit (hot/new/top) tối đa ~1000 post, nên KHÔNG cào
ngược lâu được. Arctic Shift lưu bản sao lịch sử của Reddit và mở API tìm kiếm
theo cửa sổ thời gian, không cần đăng nhập.

  API (public):  https://arctic-shift.photon-reddit.com/api
    /posts/search     ?subreddit=<sub>&after=<epoch>&before=<epoch>&limit=100&sort=asc
    /comments/search  (tham số tương tự)
  Trả JSON: {"data": [ <post/comment giống schema Reddit>, ... ]}

Cách phân trang: sort=asc, mỗi trang đẩy con trỏ `after` = created_utc lớn nhất
vừa nhận (nhích +1 nếu không tiến để tránh kẹt). Dừng khi hết dữ liệu hoặc chạm
`before`. Dữ liệu map thẳng vào cùng star schema qua Storage.upsert_* (upsert nên
chạy lại an toàn, trùng id sẽ ghi đè).

⚠️ Lịch sự: đây là dịch vụ cộng đồng miễn phí — để --sleep hợp lý, đừng dội.

Dùng:
    python -m jobs.backfill_arctic_shift technology --after 2025-01-01 --before 2025-04-01
    python -m jobs.backfill_arctic_shift technology --after 2025-01-01 --kind both
    python cli.py backfill technology --after 2025-01-01     # (wrap trong cli.py)
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time
from typing import Callable

import requests

from reddit_crawler import Storage
from reddit_crawler.storage import _short_id

log = logging.getLogger("backfill")

BASE = "https://arctic-shift.photon-reddit.com/api"
UA = "python:news-aggregator-research:v0.1 (personal backfill; polite)"
PAGE_LIMIT = 100                     # Arctic Shift trả tối đa 100/lần


# ------------------------------------------------------------------ tiện ích thời gian
def to_epoch(s: str) -> int:
    """Nhận 'YYYY-MM-DD', 'YYYY-MM-DDTHH:MM:SS', hoặc epoch dạng số."""
    s = s.strip()
    if s.isdigit():
        return int(s)
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d"):
        try:
            return int(dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc).timestamp())
        except ValueError:
            continue
    raise ValueError(f"Không hiểu mốc thời gian: {s!r} (dùng YYYY-MM-DD hoặc epoch)")


def _ymd(epoch: float) -> str:
    return dt.datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%d")


# ------------------------------------------------------------------ chuẩn hóa bản ghi
def _norm_post(it: dict) -> dict:
    it = dict(it)
    if not it.get("id") and it.get("name"):
        it["id"] = it["name"].split("_", 1)[-1]
    if not it.get("name") and it.get("id"):
        it["name"] = f"t3_{it['id']}"
    return it


def _norm_comment(it: dict) -> dict:
    it = dict(it)
    if not it.get("id") and it.get("name"):
        it["id"] = it["name"].split("_", 1)[-1]
    if not it.get("name") and it.get("id"):
        it["name"] = f"t1_{it['id']}"
    it["_parent_id"] = it.get("parent_id")
    it["_depth"] = None                 # dump không cho biết depth
    return it


def _stub_sub(store: Storage, item: dict, subreddit: str) -> str | None:
    sid = _short_id(item.get("subreddit_id"))
    store.upsert_subreddit({
        "name": item.get("subreddit_id"),
        "id": sid,
        "display_name": item.get("subreddit") or subreddit,
    })
    return sid


# ------------------------------------------------------------------ vòng phân trang chung
def _paginate(
    session: requests.Session,
    endpoint: str,
    subreddit: str,
    after: int,
    before: int,
    handle_page: Callable[[list[dict]], None],
    sleep: float,
    max_pages: int,
    label: str,
) -> int:
    url = f"{BASE}/{endpoint}"
    cur_after = after
    total = 0
    pages = 0
    while cur_after < before and pages < max_pages:
        params = {
            "subreddit": subreddit,
            "after": int(cur_after),
            "before": int(before),
            "limit": PAGE_LIMIT,
            "sort": "asc",
        }
        try:
            r = session.get(url, params=params, headers={"user-agent": UA}, timeout=45)
        except requests.RequestException as e:
            log.warning("lỗi mạng (%s), thử lại sau 5s: %s", label, e)
            time.sleep(5)
            continue
        if r.status_code == 429:
            log.info("bị giới hạn (429), nghỉ 10s...")
            time.sleep(10)
            continue
        r.raise_for_status()
        items = r.json().get("data", [])
        if not items:
            break

        handle_page(items)
        total += len(items)
        pages += 1

        last_utc = max(int(i.get("created_utc") or 0) for i in items)
        cur_after = max(last_utc, cur_after + 1)
        log.info("%s r/%s: +%d (tới %s) — tổng %d",
                 label, subreddit, len(items), _ymd(cur_after), total)

        if len(items) < PAGE_LIMIT:      # trang lẻ cuối cùng
            break
        time.sleep(sleep)
    return total


# ------------------------------------------------------------------ backfill post / comment
def backfill_posts(store: Storage, subreddit: str, after: int, before: int,
                   sleep: float = 1.0, max_pages: int = 100_000) -> int:
    session = requests.Session()

    def handle(items: list[dict]) -> None:
        _stub_sub(store, items[0], subreddit)
        for it in items:
            sid = _short_id(it.get("subreddit_id"))
            store.upsert_post(_norm_post(it), subreddit_id=sid)
        store.commit()

    return _paginate(session, "posts/search", subreddit, after, before,
                     handle, sleep, max_pages, "posts")


def backfill_comments(store: Storage, subreddit: str, after: int, before: int,
                      sleep: float = 1.0, max_pages: int = 100_000) -> int:
    session = requests.Session()

    def handle(items: list[dict]) -> None:
        for it in items:
            post_id = _short_id(it.get("link_id"))
            if not post_id:
                continue
            sid = _short_id(it.get("subreddit_id"))
            store.upsert_comment(_norm_comment(it), post_id=post_id, subreddit_id=sid)
        store.commit()

    return _paginate(session, "comments/search", subreddit, after, before,
                     handle, sleep, max_pages, "comments")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill lịch sử Reddit qua Arctic Shift")
    ap.add_argument("subreddit")
    ap.add_argument("--after", required=True, help="mốc bắt đầu (YYYY-MM-DD hoặc epoch)")
    ap.add_argument("--before", default=None, help="mốc kết thúc (mặc định: bây giờ)")
    ap.add_argument("--kind", default="posts", choices=["posts", "comments", "both"])
    ap.add_argument("--sleep", type=float, default=1.0, help="giây nghỉ giữa các trang")
    ap.add_argument("--max-pages", type=int, default=100_000)
    ap.add_argument("--db", default="reddit.db")
    ap.add_argument("--raw", default="raw")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    after = to_epoch(args.after)
    before = to_epoch(args.before) if args.before else int(time.time())
    if after >= before:
        ap.error("--after phải nhỏ hơn --before")

    log.info("Backfill r/%s [%s → %s] kind=%s",
             args.subreddit, _ymd(after), _ymd(before), args.kind)

    store = Storage(db_path=args.db, raw_dir=args.raw or None)
    try:
        if args.kind in ("posts", "both"):
            n = backfill_posts(store, args.subreddit, after, before, args.sleep, args.max_pages)
            log.info("POSTS xong: %d bản ghi", n)
        if args.kind in ("comments", "both"):
            n = backfill_comments(store, args.subreddit, after, before, args.sleep, args.max_pages)
            log.info("COMMENTS xong: %d bản ghi", n)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
