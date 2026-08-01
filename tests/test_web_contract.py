from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from reddit_crawler.storage import Storage
from web import app as web_app


class WebContractSecurityTests(unittest.TestCase):
    """Contract/security tests cho tầng API hardening (spec 2026-08-01-backend-api-hardening)."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.db = self.root / "web.db"
        Storage(str(self.db), None).close()

        patches = [
            patch.object(web_app, "DB_PATH", str(self.db)),
            patch.object(web_app, "_RATE_LIMIT_ENABLED", False),
            patch.object(web_app, "_WRITE_TOKEN", ""),
            patch.object(web_app, "_CORS_ORIGINS", []),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        web_app._rate_limiter._buckets.clear()
        self.client = TestClient(web_app.app)

    def tearDown(self) -> None:
        self.client.close()

    # ── Versioning ──────────────────────────────────────────────────────────
    def test_versioned_health_and_legacy_alias(self) -> None:
        v1 = self.client.get("/api/v1/health")
        self.assertEqual(v1.status_code, 200)
        self.assertEqual(v1.json()["api_version"], "v1")

        legacy = self.client.get("/api/health")
        self.assertEqual(legacy.status_code, 200)

    def test_unknown_api_prefix_is_404_json(self) -> None:
        response = self.client.get("/api/v2/health")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["content-type"].split(";")[0], "application/json")

    # ── Security headers ────────────────────────────────────────────────────
    def test_security_headers_present_on_api_response(self) -> None:
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    # ── Input validation ────────────────────────────────────────────────────
    def test_invalid_post_id_rejected_400(self) -> None:
        for url in (
            "/api/v1/knowledge/bad!!id",
            "/api/v1/posts/ABCDEFGHIJKLMNOP",
            "/api/v1/posts/a-b-c",
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 400, url)

    def test_invalid_post_id_rejected_on_writes(self) -> None:
        response = self.client.post("/api/v1/user/bookmarks/ABC")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "post_id không hợp lệ")

    def test_query_bounds_422(self) -> None:
        self.assertEqual(self.client.get("/api/v1/knowledge/feed?limit=500").status_code, 422)
        self.assertEqual(self.client.get("/api/v1/knowledge/feed?limit=0").status_code, 422)
        self.assertEqual(self.client.get("/api/v1/knowledge/feed?offset=-1").status_code, 422)
        long_q = "x" * 201
        self.assertEqual(self.client.get(f"/api/v1/knowledge/feed?q={long_q}").status_code, 422)

    # ── Rate limiting ───────────────────────────────────────────────────────
    def test_rate_limit_returns_429_with_retry_after(self) -> None:
        with patch.object(web_app, "_RATE_RULES", {"GET": (0.0, 2.0), "POST": (0.0, 2.0)}), \
                patch.object(web_app, "_RATE_LIMIT_ENABLED", True):
            web_app._rate_limiter._buckets.clear()
            self.assertEqual(self.client.get("/api/v1/health").status_code, 200)
            self.assertEqual(self.client.get("/api/v1/health").status_code, 200)
            third = self.client.get("/api/v1/health")
            self.assertEqual(third.status_code, 429)
            self.assertTrue(third.headers.get("retry-after"))
            self.assertEqual(third.json()["error"], "rate_limited")
            web_app._rate_limiter._buckets.clear()

    # ── Write guard ─────────────────────────────────────────────────────────
    def test_write_from_non_loopback_requires_token(self) -> None:
        with patch.object(web_app, "_is_loopback", lambda ip: False), \
                patch.object(web_app, "_WRITE_TOKEN", "sekret-token"):
            denied = self.client.post("/api/v1/user/bookmarks/abcde12345")
            self.assertEqual(denied.status_code, 401)

            wrong = self.client.post(
                "/api/v1/user/bookmarks/abcde12345", headers={"X-API-Token": "wrong"}
            )
            self.assertEqual(wrong.status_code, 401)

            ok = self.client.post(
                "/api/v1/user/bookmarks/abcde12345", headers={"X-API-Token": "sekret-token"}
            )
            self.assertEqual(ok.status_code, 200)
            self.assertTrue(ok.json()["saved"])

    # ── CORS ────────────────────────────────────────────────────────────────
    def test_cors_preflight_honors_allowlist(self) -> None:
        with patch.object(web_app, "_CORS_ORIGINS", ["http://allowed.example"]):
            allowed = self.client.options(
                "/api/v1/user/bookmarks/abcde12345",
                headers={"Origin": "http://allowed.example", "Access-Control-Request-Method": "POST"},
            )
            self.assertEqual(allowed.status_code, 204)
            self.assertEqual(allowed.headers["access-control-allow-origin"], "http://allowed.example")

            denied = self.client.options(
                "/api/v1/user/bookmarks/abcde12345",
                headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
            )
            self.assertEqual(denied.status_code, 204)
            self.assertNotIn("access-control-allow-origin", denied.headers)


if __name__ == "__main__":
    unittest.main()
