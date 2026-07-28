"""Logic crawl: listing (post), cây comment (kèm children), user, subreddit.

Lưu ý giới hạn của Reddit:
- Mỗi 'listing' (hot/new/top/...) chỉ trả tối đa ~1000 item dù phân trang.
  Để lấy nhiều hơn: chia nhỏ theo time-window + search, hoặc dùng nguồn dump
  lịch sử (Arctic Shift / PullPush) — xem docs/api_findings.md.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

from .client import RedditClient

LISTING_HARD_CAP = 1000
# Ưu tiên bắt id đứng ngay sau '/comments/'; nếu không có thì coi cả chuỗi là id trần.
_PERMALINK_RE = re.compile(r"/comments/([a-z0-9]+)", re.I)
_BARE_ID_RE = re.compile(r"^(?:t3_)?([a-z0-9]+)$", re.I)


# ----------------------------------------------------------------------------- listings
def iter_listing(
    client: RedditClient,
    subreddit: str,
    sort: str = "new",
    t: str | None = None,          # hour|day|week|month|year|all (chỉ dùng cho sort=top/controversial)
    max_items: int | None = None,
    start_after: str | None = None,
) -> Iterator[dict]:
    """Duyệt post trong một subreddit, tự phân trang qua con trỏ `after`."""
    after: str | None = start_after
    fetched = 0
    while True:
        params: dict[str, Any] = {"limit": 100}
        if t:
            params["t"] = t
        if after:
            params["after"] = after
        data = client.get(f"/r/{subreddit}/{sort}", **params).get("data", {})
        children = data.get("children", [])
        if not children:
            return
        for child in children:
            yield child["data"]
            fetched += 1
            if max_items and fetched >= max_items:
                return
        after = data.get("after")
        if not after or fetched >= LISTING_HARD_CAP:
            return


def search_subreddit(
    client: RedditClient, subreddit: str, query: str,
    sort: str = "new", t: str = "all", max_items: int | None = None,
) -> Iterator[dict]:
    """Search trong 1 sub — hữu ích để 'lách' giới hạn 1000 bằng nhiều query hẹp."""
    after: str | None = None
    fetched = 0
    while True:
        params = {"q": query, "sort": sort, "t": t, "restrict_sr": 1, "limit": 100}
        if after:
            params["after"] = after
        data = client.get(f"/r/{subreddit}/search", **params).get("data", {})
        children = data.get("children", [])
        if not children:
            return
        for child in children:
            yield child["data"]
            fetched += 1
            if max_items and fetched >= max_items:
                return
        after = data.get("after")
        if not after:
            return


# ----------------------------------------------------------------------------- comments
def _norm_post_id(post_id_or_url: str) -> str:
    s = post_id_or_url.strip()
    m = _PERMALINK_RE.search(s)          # URL đầy đủ: .../comments/<id>/<slug>/...
    if m:
        return m.group(1)
    m = _BARE_ID_RE.match(s)             # id trần: '1rfgu9a' hoặc 't3_1rfgu9a'
    if m:
        return m.group(1)
    raise ValueError(f"Không nhận diện được post id từ: {post_id_or_url!r}")


def fetch_post_with_comments(
    client: RedditClient,
    post_id_or_url: str,
    subreddit: str | None = None,
    sort: str = "top",
    depth: int | None = None,
    limit: int = 500,
    resolve_more: bool = True,
) -> tuple[dict, list[dict]]:
    """Lấy 1 post + toàn bộ cây comment (đã làm phẳng).

    Trả (post_dict, [comment_dict, ...]) với mỗi comment có thêm:
      _depth      : độ sâu trong cây (0 = comment gốc)
      _parent_id  : fullname cha (t3_ = post, t1_ = comment)
    """
    post_id = _norm_post_id(post_id_or_url)
    path = f"/r/{subreddit}/comments/{post_id}" if subreddit else f"/comments/{post_id}"
    params: dict[str, Any] = {"sort": sort, "limit": limit}
    if depth is not None:
        params["depth"] = depth
    payload = client.get(path, **params)

    post = payload[0]["data"]["children"][0]["data"]
    tree = payload[1]["data"]["children"]
    comments = flatten_comments(client, post["name"], tree, resolve_more=resolve_more)
    return post, comments


def flatten_comments(
    client: RedditClient, link_id: str, children: list[dict], resolve_more: bool = True,
) -> list[dict]:
    """Duyệt cây comment lồng nhau -> danh sách phẳng; bung cả node 'more'."""
    out: list[dict] = []
    more_batches: list[list[str]] = []

    def walk(nodes: list[dict], depth: int) -> None:
        for node in nodes:
            if node["kind"] == "more":
                ids = node["data"].get("children") or []
                if ids:
                    more_batches.append(ids)
                # node 'more' không có children (kiểu "continue this thread") là deep-link
                # phải re-fetch bằng ?comment=<id>; để lại cho bản sau.
                continue
            cd = node["data"]
            cd["_depth"] = depth
            cd["_parent_id"] = cd.get("parent_id")
            out.append(cd)
            replies = cd.get("replies")
            if isinstance(replies, dict):
                walk(replies["data"]["children"], depth + 1)

    walk(children, 0)

    if resolve_more:
        while more_batches:
            ids = more_batches.pop()
            for i in range(0, len(ids), 100):           # morechildren giới hạn 100 id/lần
                things = client.get_morechildren(link_id, ids[i : i + 100])
                for th in things:
                    if th["kind"] == "more":
                        extra = th["data"].get("children") or []
                        if extra:
                            more_batches.append(extra)
                    elif th["kind"] == "t1":
                        cd = th["data"]
                        cd["_depth"] = None             # morechildren không cho biết depth chính xác
                        cd["_parent_id"] = cd.get("parent_id")
                        out.append(cd)
    return out


# ----------------------------------------------------------------------------- entities
def fetch_user(client: RedditClient, username: str) -> dict | None:
    try:
        return client.get(f"/user/{username}/about")["data"]
    except Exception:
        return None  # user bị xóa/suspend/shadowban -> bỏ qua


def fetch_subreddit(client: RedditClient, subreddit: str) -> dict:
    return client.get(f"/r/{subreddit}/about")["data"]


def search_subreddits(client: RedditClient, query: str, limit: int = 25) -> list[dict]:
    data = client.get("/subreddits/search", q=query, limit=limit).get("data", {})
    return [c["data"] for c in data.get("children", [])]


def info_by_ids(client: RedditClient, fullnames: list[str]) -> Iterator[dict]:
    """Lấy metadata HIỆN TẠI của nhiều 'thing' qua /api/info (tối đa 100 id/lần).

    Rẻ hơn nhiều so với mở lại từng post: 1 request lấy tối đa 100 bản ghi.
    Dùng để refresh score theo thời gian -> tính velocity (tin nóng)."""
    for i in range(0, len(fullnames), 100):
        batch = ",".join(fullnames[i : i + 100])
        data = client.get("/api/info", id=batch).get("data", {})
        for child in data.get("children", []):
            yield child["data"]
