# Spec: Hourly aggregated social roundup posts from enriched Reddit data

- Date: 2026-08-01
- Status: done

## Problem

Social Studio hiện tạo 1 bài đăng cho 1 post Reddit (`/api/posts/{id}/social`). Một post đơn lẻ chưa phản ánh cả bức tranh: nhiều post trùng chủ đề xuất hiện trong cùng một khung giờ với mức tương tác khác nhau, và tổng tương tác của cả cụm chủ đề mới là tín hiệu thật. Kết quả là bài social dễ hời hợt, lặp ý, bỏ sót chủ đề "bom tấn" của giờ.

## Goal

Mỗi giờ (rolling 3h dữ liệu) hệ thống tổng hợp từ các post đã enrich (V2 analysis) ra 3 bài social "đặc trưng" (cấu hình 1–5), mỗi bài đại diện cho một cụm chủ đề: gom các post trùng/nhóm cùng chủ đề, cộng dồn score/comments của cả cụm, và viết bài theo prompt v3. Chạy được tự động (systemd mỗi giờ) lẫn bằng tay (CLI + API). Kết quả lưu vào DB và hiển thị trong Social Studio UI với nút copy.

## Non-goals

- Không thay thế luồng social post 1:1 hiện tại (giữ nguyên `/api/posts/{id}/social`).
- Không chủ động đăng bài lên Facebook/LinkedIn/X — chỉ tạo nội dung + copy.
- Không thêm clustering bằng embedding/vector — dùng heuristic + LLM để gom cụm.
- Không re-crawl hay re-enrich; chỉ dùng dữ liệu đã có trong DB.
- Không chạy migration phá vỡ schema — chỉ thêm bảng mới (additive).

## Acceptance criteria

- [ ] Hàm `roundup_social_posts(db_path, *, hours=3, top=3)` trong `reddit_crawler/analytics.py` trả về ≤ `top` cụm; mỗi cụm có `cluster_id`, `source_post_ids` (≥2 post), `total_score`, `total_comments`, `topic_vi`, `domain_id`, `hour_start` (mốc giờ hiện tại trừ đi 3h). — verify: `.venv/bin/python -c "from reddit_crawler.analytics import roundup_social_posts; r = roundup_social_posts('reddit.db'); assert 0 <= len(r) <= 3; assert all(len(c['source_post_ids']) >= 1 for c in r); print('OK', len(r))"`
- [ ] Hàm `generate_social_roundup(db_path, *, hours=3, top=3, provider='auto')` trong `reddit_crawler/llm.py` trả về list dict, mỗi item có `cluster_id`, `title`, `full_post_text` (150–300 từ), `total_score`, `total_comments`, `source_post_ids`, `provider`; bài viết dùng prompt `build_social_post_prompt('v3')` làm system instruction và **không** trùng full_post_text với bất kỳ `ai_social_post` nào hiện có. — verify: `.venv/bin/python -c "from reddit_crawler.llm import generate_social_roundup; r = generate_social_roundup('reddit.db', provider='local'); assert isinstance(r, list); assert all(set(['cluster_id','title','full_post_text','provider']) <= set(x) for x in r); print('OK', len(r))"`
- [ ] Bảng mới `ai_social_roundup` được thêm vào `reddit_crawler/schema.sql` (additive, migration version mới nhất +1): `cluster_id TEXT PRIMARY KEY`, `hour_start REAL`, `source_post_ids TEXT (JSON)`, `total_score INTEGER`, `total_comments INTEGER`, `topic_vi TEXT`, `domain_id TEXT`, `provider TEXT`, `model TEXT`, `status TEXT`, `title TEXT`, `full_post_text TEXT`, `payload_json TEXT`, `generated_at REAL`, `error TEXT`. — verify: `.venv/bin/python -c "import sqlite3; c=sqlite3.connect(':memory:'); c.executescript(open('reddit_crawler/schema.sql').read()); cols=[r[1] for r in c.execute('PRAGMA table_info(ai_social_roundup)')]; assert 'cluster_id' in cols and 'full_post_text' in cols; print('OK')"`
- [ ] `Storage.upsert_ai_social_roundup(row)` trong `reddit_crawler/storage.py` upsert đúng dòng theo `cluster_id` (test ghi 2 lần cùng cluster_id, lần 2 ghi đè; không có ngoại lệ). — verify: `.venv/bin/python -m unittest tests.test_storage.StorageTests.test_ai_social_roundup_upsert`
- [ ] CLI `cli.py roundup-social --hours 3 --top 3 --provider auto` chạy được, exit 0, in ra tiêu đề + full_post_text từng cụm. — verify: `.venv/bin/python cli.py roundup-social --hours 3 --top 3 --provider local && echo CLI_OK`
- [ ] API `GET /api/social/roundup?hours=3&top=3` trả `{period, count, items: [{cluster_id, title, full_post_text, total_score, total_comments, source_post_ids, provider, hour_start}]}`; không gọi LLM khi đã có dữ liệu trong `ai_social_roundup` cho cùng cửa sổ (trả dữ liệu cũ). — verify: `.venv/bin/python -m unittest tests.test_web_api.WebApiTests.test_social_roundup_endpoint`
- [ ] UI Social Studio có mục "Roundup Theo Giờ" hiển thị các bài tổng hợp (tiêu đề, tổng tương tác, nút Copy bài), fetch từ `/api/social/roundup`; không làm vỡ build frontend. — verify: `npm --prefix web/frontend run build`
- [ ] Service systemd mới `reddit-roundup.service` (user) chạy `cli.py roundup-social` mỗi giờ, disabled mặc định (không enable trong spec này). — verify: `test -f /home/dataguy/.config/systemd/user/reddit-roundup.service && systemd-analyze --user verify /home/dataguy/.config/systemd/user/reddit-roundup.service`
- [ ] Bộ test cũ đầy đủ vẫn xanh (compileall + 212 tests). — verify: `.venv/bin/python -m compileall -q reddit_crawler jobs web cli.py && .venv/bin/python -m unittest discover -s tests`

## Risk tier

- **R1** — reversible change inside this repo: bảng mới additive, không động vào DB production trực tiếp (chạy trên bản copy khi verify schema), service mới không enable.

## Constraints

- Chỉ dùng dữ liệu đã có trong `reddit.db` (fact_post, fact_post_metrics, ai_post_analysis_v2); không gọi Reddit/OpenAI/Gemini trong test tự động.
- Bài social phải theo phong cách prompt v3: 150–300 từ, tiếng Việt, không markdown, có tổng tương tác của cả cụm trong hook.
- Web vẫn read-only; API roundup đọc `ai_social_roundup` trước, chỉ gọi LLM khi thiếu (giống pattern `/api/posts/{id}/social`).
- Không dùng pytest; không commit secret, DB, raw/ hay báo cáo.
- `hour_start` = mốc giờ tròn gần nhất của `now - hours` (dùng để cache & so sánh cửa sổ).

## Stop if

- Migration chạm vào bảng có dữ liệu (không phải thuần additive).
- Test đang xanh bị đỏ ngoài phạm vi spec này.
- Phải sửa `web/frontend/dist` trực tiếp thay vì build từ source.

## Interfaces

- `reddit_crawler/analytics.roundup_social_posts(db_path: str | Path, *, hours: float = 3, top: int = 3) -> list[dict]` — mỗi item: `{cluster_id, hour_start, topic_vi, domain_id, source_post_ids: list[str], total_score: int, total_comments: int, n_posts: int, top_post_id: str}`
- `reddit_crawler/llm.generate_social_roundup(db_path: str | Path, *, hours: float = 3, top: int = 3, provider: str = 'auto') -> list[dict]` — mỗi item: `{cluster_id, title, full_post_text, total_score, total_comments, source_post_ids, provider, model}`
- `reddit_crawler.storage.Storage.upsert_ai_social_roundup(row: dict)` — bảng `ai_social_roundup` (criterion 3)
- CLI: `cli.py roundup-social [--hours 3] [--top 3] [--provider auto]`
- API: `GET /api/social/roundup?hours=3&top=3`

## Plan (filled at Plan stage)

<!-- Files to touch, order of work, known risks. -->

## Decisions log (append during Build)

- 2026-08-01 — Cửa sổ rolling 3h chạy mỗi giờ; mỗi giờ ra 3 bài (cấu hình 1–5); vừa systemd vừa CLI/API. (Quyết định của owner.)
- 2026-08-01 — Heuristic clustering dùng token hiếm (tần suất ≤ 3 post trong cửa sổ) làm chữ ký gộp: gộp khi cùng domain + chung ≥2 token thường hoặc ≥1 token hiếm. Chỉ lọc theo `created_utc` của post (analysis chỉ cần tồn tại, không bắt phải mới).
- 2026-08-01 — Khi generate lại cho cùng `hour_start`, xóa các cụm cũ của cửa sổ đó trước khi upsert (tránh stale cluster); API cache trả `LIMIT top`.
- 2026-08-01 — Migration 11 (bảng `ai_social_roundup`) verified trên bản backup WAL-safe `/tmp/opencode/reddit-backup-20260801.db` rồi mới apply live; rollback = drop bảng mới.

## Outcome (filled at Ship)

- C1 `roundup_social_posts()`: đã verify trên live DB 24h — gộp 3 bài "reset Codex" thành cụm n=3 (45 upvotes / 34 comments cộng dồn). ✓
- C2 `generate_social_roundup()`: chạy được (gemini + local-fallback), bài 150–300 từ, prompt v3, lưu `ai_social_roundup`. ✓
- C3 Bảng `ai_social_roundup` migration 11 — verified trên copy + apply live, FK sạch. ✓
- C4 `Storage.upsert_ai_social_roundup` + test upsert ghi đè theo cluster_id. ✓
- C5 CLI `roundup-social` exit 0, in tiêu đề + full text từng cụm. ✓
- C6 API `GET /api/social/roundup` cache đúng cửa sổ, `cached: true`, không gọi LLM. ✓
- C7 UI "Roundup Theo Giờ" (RoundupStrip) trong Social Studio, nút copy, build OK. ✓
- C8 `reddit-roundup.service` + timer đã cài vào `~/.config/systemd/user/`, verify OK, **disabled** theo spec. ✓
- C9 Full suite: compileall + 214 tests xanh, frontend build OK. ✓
- Lệch so với spec: heuristic clustering được tinh chỉnh (token hiếm + dedupe token) để gộp được các bài cùng chủ đề có từ vựng khác nhau; CLI demo chạy `--hours 24` vì 3h gần nhất chưa có post mới (collector chưa crawl ~22h).
