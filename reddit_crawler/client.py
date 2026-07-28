"""HTTP client cho oauth.reddit.com — tự tôn trọng rate limit và retry.

Reddit trả các header:
  x-ratelimit-remaining : số request còn lại trong cửa sổ hiện tại
  x-ratelimit-reset     : số giây tới khi cửa sổ reset
  x-ratelimit-used      : số request đã dùng
Client sẽ chủ động ngủ khi 'remaining' xuống thấp để không bị 429.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .auth import TokenManager

OAUTH_BASE = "https://oauth.reddit.com"
log = logging.getLogger("reddit_crawler.client")


class RedditClient:
    def __init__(
        self,
        user_agent: str,
        token_manager: TokenManager | None = None,
        min_remaining: float = 3,
        max_retries: int = 5,
        timeout: int = 30,
    ) -> None:
        self.user_agent = user_agent
        self.session = requests.Session()
        self.tm = token_manager or TokenManager(user_agent=user_agent, session=self.session)
        self.min_remaining = min_remaining
        self.max_retries = max_retries
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.tm.token()}",
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }

    def _respect_ratelimit(self, resp: requests.Response) -> None:
        rem = resp.headers.get("x-ratelimit-remaining")
        reset = resp.headers.get("x-ratelimit-reset")
        if rem is None:
            return
        try:
            remaining = float(rem)
            reset_s = float(reset) if reset is not None else 1.0
        except ValueError:
            return
        if remaining <= self.min_remaining:
            wait = reset_s + 1
            log.warning("Rate limit sắp cạn (còn %s). Ngủ %.0fs...", rem, wait)
            time.sleep(wait)

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        url = OAUTH_BASE + path if path.startswith("/") else path
        params = dict(params or {})
        params.setdefault("raw_json", 1)  # tránh HTML-escape &amp; &lt; trong text

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.request(
                    method, url, headers=self._headers(),
                    params=params, data=data, timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(2 ** attempt)
                continue

            self._respect_ratelimit(resp)

            if resp.status_code == 401:            # token hết hạn/không hợp lệ
                self.tm.invalidate()
                continue
            if resp.status_code == 429:            # bị siết dù đã cẩn thận
                wait = float(resp.headers.get("x-ratelimit-reset", 2 ** attempt))
                log.warning("429 Too Many Requests. Ngủ %.0fs.", wait)
                time.sleep(wait + 1)
                continue
            if resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue

            resp.raise_for_status()
            if not resp.content:
                return None
            return resp.json()

        raise RuntimeError(f"Thất bại sau {self.max_retries} lần thử: {method} {url}") from last_exc

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params)

    def get_morechildren(self, link_id: str, children_ids: list[str], sort: str = "top") -> list[dict]:
        """Bung các comment ẩn (node 'more' có danh sách children id).

        link_id: fullname của post, dạng t3_xxxxx
        Trả về danh sách 'things' phẳng (mỗi thing kind t1 = comment, hoặc more).
        """
        data = self.request(
            "POST", "/api/morechildren",
            data={
                "api_type": "json",
                "link_id": link_id,
                "children": ",".join(children_ids),
                "sort": sort,
                "limit_children": "false",
            },
        )
        return data.get("json", {}).get("data", {}).get("things", []) if data else []
