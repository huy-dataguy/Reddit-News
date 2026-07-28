"""reddit_crawler — bộ tool crawl Reddit qua OAuth API (không dùng HTML).

Reverse-engineer từ chính app của Reddit: dùng grant `installed_client`
(public client-id, không cần secret) để lấy token ẩn danh, rồi gọi thẳng
`oauth.reddit.com` — vượt qua trang challenge 403 mà endpoint `.json` gặp phải.
"""

from .auth import TokenManager
from .client import RedditClient
from . import crawl
from .storage import Storage

__all__ = ["TokenManager", "RedditClient", "crawl", "Storage"]
__version__ = "0.1.0"
