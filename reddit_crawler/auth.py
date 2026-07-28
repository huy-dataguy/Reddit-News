"""Quản lý OAuth token cho Reddit.

Cơ chế (reverse-engineer từ app chính thức của Reddit):
- POST tới https://www.reddit.com/api/v1/access_token
- HTTP Basic auth = base64("<client_id>:")  (mật khẩu rỗng)
- grant_type = installed_client + một device_id ngẫu nhiên
=> nhận về access_token dùng cho oauth.reddit.com, KHÔNG cần đăng nhập, KHÔNG cần secret.

Khuyến nghị production: đăng ký app riêng tại https://www.reddit.com/prefs/apps
(loại "script" hoặc "installed app") để có client_id của bạn, đúng Reddit API Terms
và có hạn mức 100 request/phút ổn định.
"""

from __future__ import annotations

import base64
import threading
import time
import uuid

import requests

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
INSTALLED_GRANT = "https://oauth.reddit.com/grants/installed_client"

# Public client-id mà client cài đặt của Reddit dùng. Có thể ghi đè bằng client_id
# app riêng của bạn (khuyến nghị cho production).
DEFAULT_CLIENT_ID = "ohXpoqrZYub1kg"


class TokenManager:
    """Lấy và tự làm mới access_token, thread-safe."""

    def __init__(
        self,
        user_agent: str,
        client_id: str = DEFAULT_CLIENT_ID,
        device_id: str | None = None,
        session: requests.Session | None = None,
        refresh_margin: int = 60,
    ) -> None:
        self.user_agent = user_agent
        self.client_id = client_id
        self.device_id = device_id or uuid.uuid4().hex
        self.session = session or requests.Session()
        self.refresh_margin = refresh_margin
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    def _fetch(self) -> None:
        basic = base64.b64encode(f"{self.client_id}:".encode()).decode()
        resp = self.session.post(
            TOKEN_URL,
            headers={
                "Authorization": f"Basic {basic}",
                "User-Agent": self.user_agent,
            },
            data={"grant_type": INSTALLED_GRANT, "device_id": self.device_id},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        if "access_token" not in payload:
            raise RuntimeError(f"Không lấy được token: {payload}")
        self._token = payload["access_token"]
        self._expires_at = time.time() + payload.get("expires_in", 3600) - self.refresh_margin

    def token(self) -> str:
        with self._lock:
            if self._token is None or time.time() >= self._expires_at:
                self._fetch()
            return self._token  # type: ignore[return-value]

    def invalidate(self) -> None:
        """Buộc lần gọi token() tiếp theo phải lấy token mới (dùng khi gặp 401)."""
        with self._lock:
            self._token = None
            self._expires_at = 0.0
