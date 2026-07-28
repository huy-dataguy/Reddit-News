# Reddit Radar — hệ thống theo dõi tín hiệu công nghệ

MVP thu thập Reddit liên tục, lưu raw + star schema, đo vận tốc tương tác, xếp hạng
tin đang nổi, tạo báo cáo định kỳ và phục vụ dashboard web.

## Trạng thái hiện tại

- Reddit OAuth crawler: post, comment tree, user, subreddit, rate-limit/retry.
- Incremental `/new` có high-water mark và bootstrap tiếp nối, không bỏ backlog khi
  dùng `--max-per-sub`.
- Backfill lịch sử qua Arctic Shift.
- Bronze JSONL + SQLite star schema, foreign key và tự sửa reference của DB cũ.
- Snapshot metrics và baseline `trend_score` từ độ mới + tương tác + velocity.
- Report Markdown/JSON: 3 giờ, ngày, tuần, tháng, năm.
- FastAPI API + React 19/Vite responsive dashboard.
- Trang chi tiết nội bộ: selftext, article text đã có sẵn và discussion đã crawl.
- AI briefing có structured output, dẫn nguồn post, số liệu, model/API update,
  so sánh có bằng chứng và ảnh lấy từ dữ liệu crawl.
- Dashboard có ba bề mặt: Hôm nay, Kho tri thức và Radar; taxonomy chuẩn gồm
  AI/ML, devtools, security, infrastructure, science, business và other.
- PostAnalysis V2 tách bài gốc, claim từ discussion, resource, hành động, cảnh báo
  và câu hỏi mở. Evidence dùng ID + excerpt của comment đã crawl; local fallback
  luôn được gắn nhãn provisional, không giả làm Gemini/OpenAI.
- Unit systemd cho collector, enrichment, pipeline AI/report và website.
- Gemini backlog duyệt toàn bộ discussion có bằng chứng theo batch; lỗi có
  retry/backoff và manual-review report, không tự rơi sang OpenAI/local.
- `story-export`: xuất tin giá trị cao (trend_score + Community Intelligence) thành
  story JSON cho MediaWorkflow render video RedditStory tự động.

Chưa có trong MVP này: NER/bản đồ, tài khoản người dùng và connector trực tiếp
tới feed chính thức của từng hãng model.

## Cài đặt

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Chỉnh `.env`, đặc biệt:

```dotenv
REDDIT_USER_AGENT="python:reddit-radar:v0.2 (by /u/ten_reddit_cua_ban)"
REDDIT_CLIENT_ID=client_id_app_cua_ban
REDDIT_DB_PATH=reddit.db
REPORT_DIR=reports
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.6-luna
GEMINI_API_KEY=replace_me
GEMINI_AGENT=antigravity-preview-05-2026
GEMINI_NORMALIZER_MODEL=gemini-3.5-flash
```

App riêng được khuyến nghị cho vận hành dài hạn. Client-id public trong code chỉ
dành cho thử nghiệm.

## Chạy nhanh

```bash
# Kiểm tra kết nối
.venv/bin/python cli.py probe

# Thu thập incremental; lần bootstrap chia 200 post/sub/lần và tự nối tiếp
.venv/bin/python cli.py incremental --max-per-sub 200

# Lấy comment còn thiếu và bóc tách link/resource cho các tin nổi
# (external article fetch đang tắt cho tới khi có đủ SSRF/redirect/robots/size guard)
.venv/bin/python cli.py enrich --kind both --period day --limit 20

# Tiến dần qua toàn bộ backlog thay vì chỉ top signal
.venv/bin/python cli.py enrich --kind both --backlog --limit 20

# Tạo AI briefing. auto ưu tiên Gemini Antigravity, rồi OpenAI, rồi local fallback
.venv/bin/python cli.py ai-digest --period 3h --provider auto

# Phân tích sâu một discussion hoặc các bài hot nhất trong chu kỳ
.venv/bin/python cli.py analyze-post 1rfgu9a --provider auto
.venv/bin/python cli.py analyze-top --period 3h --limit 5 --provider auto

# Một batch Gemini-only trên mọi discussion còn thiếu V2 Gemini
.venv/bin/python cli.py gemini-backlog --limit 5

# Xem thống kê
.venv/bin/python cli.py stats

# Tạo báo cáo
.venv/bin/python cli.py report --period 3h
.venv/bin/python cli.py report --period day
.venv/bin/python cli.py report --period month --limit 50

# Chạy trọn chuỗi evidence → V2 analysis → digest → report
.venv/bin/python cli.py pipeline --period day --enrich-limit 5 --analysis-limit 3

# Chạy website tại http://127.0.0.1:8080
.venv/bin/python cli.py serve

# Khi sửa frontend: build React/Vite trước khi restart web
cd web/frontend
npm install
npm run build
cd ../..
.venv/bin/python cli.py serve
```

Các lệnh crawl thủ công:

```bash
.venv/bin/python cli.py find-subs "artificial intelligence"
.venv/bin/python cli.py crawl-sub technology --sort top --time week --max 50 --comments
.venv/bin/python cli.py crawl-post https://www.reddit.com/r/codex/comments/1rfgu9a/
.venv/bin/python cli.py crawl-user spez
```

## Báo cáo

Mỗi report được ghi vào `reports/<period>/latest.md`, `latest.json` và một cặp
archive có timestamp microsecond. Report chỉ dùng digest đúng period; nếu digest
chưa có, phần signal vẫn được tạo và không mượn briefing từ chu kỳ khác.

Tiến độ và lỗi Gemini backlog nằm ở
`reports/operations/gemini-backlog.latest.{json,md}`. Artifact chỉ chứa ID và
metadata lỗi đã rút gọn; không ghi prompt, comment, API response hay credential.
Các mục `blocked` cần người vận hành/Codex xử lý thủ công — service nền không thể
tự gọi một phiên chat Codex.

## Chạy tự động

Các unit trong `deploy/systemd/` cung cấp lịch:

- collector mỗi 20 phút;
- enrichment comment/resource vét toàn backlog theo batch 20 mỗi khoảng 10 phút;
- Gemini-only PostAnalysis V2 vét toàn bộ discussion đủ bằng chứng theo batch 5
  mỗi khoảng 10 phút, với retry/backoff và tối đa 3 lần/bài;
- Gemini digest/report lúc phút 10 mỗi 3 giờ, cùng chu kỳ
  ngày/tuần/tháng/năm;
- briefing mỗi 3 giờ;
- daily briefing lúc 07:10;
- retrospective đầu tuần, tháng và năm;
- web service tự khởi động lại khi lỗi.

Chỉ bật sau khi `.env` đã có User-Agent và client-id hợp lệ:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/*.service deploy/systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now reddit-crawl.timer \
  reddit-enrich.timer \
  reddit-gemini-backlog.timer \
  reddit-ai@3h.timer reddit-ai@day.timer reddit-ai@week.timer \
  reddit-ai@month.timer reddit-ai@year.timer \
  reddit-report@3h.timer reddit-report@day.timer reddit-report@week.timer \
  reddit-report@month.timer reddit-report@year.timer reddit-web.service
```

Chi tiết: [`deploy/systemd/README.md`](deploy/systemd/README.md).

## Kiến trúc

```text
Reddit OAuth ───────────────┐
Arctic Shift (backfill) ────┤
                            ▼
                    crawler / jobs
                      │          │
              raw/*.jsonl    reddit.db
                                 │
                    comment/resource enrichment
                                 │
              Gemini backlog + analytics.trending_posts
                                 │
              sourced digest + post intelligence (SQLite)
                         │                 │
                  reports/*.md/json    FastAPI API
                         │                 │
                  report archive       React/Vite dashboard
```

SQLite schema:

- dimension: `dim_subreddit`, `dim_author`, `dim_date`;
- fact: `fact_post`, `fact_comment`;
- time series: `fact_post_metrics`;
- media: `fact_post_media`;
- structured AI output: `ai_digest`;
- structured post/comment intelligence: `ai_post_analysis_v2` (V1 compatibility
  trong `ai_post_analysis`);
- extracted links/resources: `fact_extracted_resource`;
- job state: `crawl_state`, `enrichment_state`.

## Backfill lịch sử

Reddit listing có giới hạn khoảng 1.000 item. Dữ liệu mới phải được tích lũy bằng
incremental; lịch sử dùng Arctic Shift:

```bash
.venv/bin/python cli.py backfill technology --after 2025-01-01 --before 2025-04-01
.venv/bin/python cli.py backfill technology --after 2025-01-01 --kind both
```

## Kiểm thử

```bash
.venv/bin/python -m unittest discover -s tests -v
systemd-analyze verify deploy/systemd/*.service deploy/systemd/*.timer
```

## Cấu trúc chính

```text
cli.py
reddit_crawler/
  auth.py client.py crawl.py storage.py analytics.py schema.sql
jobs/
  incremental.py backfill_arctic_shift.py enrich.py gemini_backlog.py
  pipeline.py report.py
  export_story.py subs.txt
web/
  app.py frontend/ dist/
deploy/systemd/
tests/
docs/
```

Tài liệu sâu hơn: [`docs/api_findings.md`](docs/api_findings.md),
[`docs/star_schema.md`](docs/star_schema.md).
