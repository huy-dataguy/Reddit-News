# Backend API Hardening — local-first

Date: 2026-08-01 · Status: approved for implementation · Owner: dev

## Context

Web `web/app.py` chạy FastAPI bind `127.0.0.1`, read-only về dữ liệu crawl (ngoại
trừ user_bookmark / user_read_state). Sau sự cố `database is locked` khi lưu bài
và yêu cầu "backend hoàn chỉnh + chống phá hoại", bản này nâng tầng API lên chuẩn
defense-in-depth **mà không mở ra Internet** (boundary vẫn localhost theo
`2026-07-28-internal-production-program.md`).

## Quyết định kiến trúc

- Mọi route API nhận phiên bản: `/api/v1/*`. Giữ `/api/*` làm alias legacy để
  không vỡ SPA trong quá trình chuyển đổi, sau đó frontend dùng `/api/v1/*`.
- Bảo vệ theo lớp, ngay trong ứng dụng (không phụ thuộc proxy):
  1. Rate limiting token-bucket theo client IP (GET/POST riêng) → 429 + Retry-After.
  2. Write guard: endpoint POST chỉ chấp nhận từ loopback; nếu set `WEB_WRITE_TOKEN`
     trong `.env` thì loopback ngoài máy này (qua tunnel) phải kèm header
     `X-API-Token` khớp → nếu không 401.
  3. Input validation: `post_id` regex `^(t[13]_)?[a-z0-9]{4,12}$`, `q ≤ 200` ký tự,
     `limit 1..100`, `offset ≥ 0` (FastAPI Query constraint → 422).
  4. Security headers: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
     `Referrer-Policy: no-referrer`, CSP tự-host an toàn cho SPA + ảnh Reddit.
  5. CORS: mặc định same-origin; chỉ mở nếu `.env` có `CORS_ALLOWED_ORIGINS`.
- Không thêm: auth đa user, TLS trong app, public signup, WAF. Các hạng mục đó
  chỉ cần khi bind ra LAN/VPN/Internet (mở spec mới).

## Implementation

- `web/app.py`:
  - Đổi `@app.get/post("/api/...")` → `router.get/post`, `APIRouter()`.
  - `app.include_router(router, prefix="/api/v1")` + legacy `prefix="/api"`,
    đăng ký trước route catch-all `/{full_path:path}`.
  - Middleware `@app.middleware("http")`: rate limit → write guard → headers.
  - Env: `WEB_RATE_LIMIT` (mặc định `on`; `off` cho test), `WEB_WRITE_TOKEN`,
    `CORS_ALLOWED_ORIGINS`.
- `deploy/systemd/reddit-web.service`: hardening — `NoNewPrivileges`,
  `PrivateTmp`, `ProtectSystem=strict` + `ReadWritePaths` cho `reddit.db`,
  `ProtectHome=read-only`, `RestrictAddressFamilies`, `SystemCallFilter` nhẹ
  (uvicorn đơn luồng). Chạy `systemd-analyze verify/security` và giữ daemon bình thường.
- Frontend: chuyển 8 lệnh fetch sang `/api/v1/*`; giữ `/api/*` chạy cho khách cũ.

## Acceptance Criteria

- [ ] `curl http://127.0.0.1:8080/api/v1/health` trả 200 JSON, `{"api":"v1"}`
- [ ] `/api/v1/knowledge/{post_id}` với post_id không hợp lệ trả 400; `limit=500`
      trả 422
- [ ] POST `/api/v1/user/bookmarks/x` không có token từ non-loopback trả 401
      (mô phỏng header `X-Forwarded-For` không lừa được guard)
- [ ] Quá ngưỡng rate (web giảm ngưỡng qua env test) trả 429 kèm `Retry-After`
- [ ] Response mọi `/api/*` có `X-Content-Type-Options: nosniff`, `X-Frame-Options`
- [ ] Contract tests `tests/test_web_contract.py` phủ 200/400/401/422/429 xanh
- [ ] Frontend build xanh và `/`, `/trends`, `/social` vẫn 200 sau restart
- [ ] `systemd-analyze security reddit-web.service` không có cảnh báo `MEDIUM+`
      hạng mục đã cam kết; service restart thành công, `/api/v1/health` OK
- [ ] Repo-wide backend command (compileall + unittest) xanh, không secret mới
      lọt vào git

## Verify

```bash
.venv/bin/python -m compileall -q web && \
  .venv/bin/python -m unittest tests.test_web_contract -v
.venv/bin/python -m unittest discover -s tests
npm --prefix web/frontend run build
systemctl --user restart reddit-web.service
curl -s http://127.0.0.1:8080/api/v1/health
systemd-analyze security reddit-web.service --user
```
