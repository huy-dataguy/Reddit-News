# Reddit Radar — Toàn bộ dự án

> Đọc file này = hiểu toàn bộ hệ thống: flow end-to-end, mỗi module làm gì, prompt LLM, input/output, schema, cách chạy.

---

## 1. Mục tiêu

Theo dõi tín hiệu công nghệ trên Reddit: crawl liên tục → tính điểm trend → LLM phân tích thành tri thức tiếng Việt → hiển thị trên dashboard. Đầu ra phục vụ 2 nhu cầu:

1. **Đọc nhanh** trên web: biết điều gì đang nổi, phân tích sâu bằng AI
2. **Xuất video** sang MediaWorkflow: story JSON + ảnh để render video RedditStory

---

## 2. Flow end-to-end

```
┌─────────────────────────────────────────────────────────────┐
│                     DATA COLLECTION                         │
│                                                             │
│  Reddit OAuth API ──→ crawl.py (iter_listing, fetch_*)     │
│  Arctic Shift API ──→ backfill_arctic_shift.py (lịch sử)  │
│                                                             │
│  Output: raw/*.jsonl (bronze) + reddit.db (gold)           │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                     INCREMENTAL CRON                        │
│                                                             │
│  jobs/incremental.py (mỗi 15-30 phút)                      │
│  - Duyệt /new, dừng khi gặp post cũ (high-water mark)     │
│  - Snapshot score/comments → fact_post_metrics             │
│  - Tính velocity = Δscore/Δt                               │
│                                                             │
│  Output: fact_post, fact_comment, fact_post_metrics mới    │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                     ANALYTICS                               │
│                                                             │
│  reddit_crawler/analytics.py                                │
│  - trending_posts(): tính trend_score                       │
│  - post_detail(): lấy post + comments + analysis           │
│  - classify_domain(): phân loại lĩnh vực                   │
│                                                             │
│  trend_score = 0.55*log(1+score) + 0.8*log(1+comments)    │
│              + 1.5*log(1+score_vel) + 2.2*log(1+cmt_vel)  │
│              + 4.0/(1+age/6)                               │
│                                                             │
│  composite_value = trend_score + 1.2*log(1+resources)      │
│                  + 1.5*(1 if has AI analysis else 0)        │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                     AI ANALYSIS                             │
│                                                             │
│  reddit_crawler/llm.py                                      │
│  - generate_post_analysis(): phân tích 1 post bằng LLM     │
│    Input: post content + 120 comments top                   │
│    Output: PostAnalysis (JSON) lưu vào ai_post_analysis     │
│                                                             │
│  - generate_digest(): AI briefing tổng hợp                  │
│    Input: 40 trending posts + metadata                      │
│    Output: DigestContent (JSON) lưu vào ai_digest           │
│                                                             │
│  Providers: Gemini → OpenAI → local fallback                │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                     RESOURCE EXTRACTION                     │
│                                                             │
│  reddit_crawler/resources.py                                │
│  - Quét comments/posts tìm URL                             │
│  - Phân loại: GitHub repo, arxiv paper, tech blog, tool    │
│  - Lưu vào fact_extracted_resource                          │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                     LONGREAD RENDER                         │
│                                                             │
│  reddit_crawler/longread.py                                 │
│  - Biến PostAnalysis → 5-section magazine article           │
│  - Hook → Context → Insight → Evidence → Open Questions    │
│  - Không gọi LLM, chỉ render từ analysis có sẵn           │
│  - Lưu vào fact_longread                                    │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                     DISPLAY / EXPORT                        │
│                                                             │
│  FastAPI (web/app.py) → React dashboard (web/frontend/)    │
│  Story Export (jobs/export_story.py) → MediaWorkflow        │
│  Report (jobs/report.py) → Markdown/JSON → Telegram/webhook│
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Cấu trúc thư mục

```
Reddit/
├── cli.py                          # CLI trung tâm (20+ commands)
├── reddit.db                       # SQLite database (sản phẩm sống)
├── reddit.db.backup-*              # Backup theo ngày
├── raw/                            # Bronze: JSONL append-only
│   ├── subreddit/*.jsonl
│   ├── post/*.jsonl
│   ├── comment/*.jsonl
│   └── user/*.jsonl
├── reports/                        # Report output
├── .env                            # API keys (KHÔNG commit)
├── user_prefs.json                 # User preferences (bookmarks, etc.)
├── push_subscriptions.json         # Web push subscriptions
├── run.sh                          # Quick start server
├── requirements.txt                # Python dependencies
│
├── reddit_crawler/                 # Core library
│   ├── __init__.py                 # exports RedditClient, Storage, crawl
│   ├── auth.py                     # OAuth token manager (Reddit API)
│   ├── client.py                   # HTTP client với rate limit + retry
│   ├── crawl.py                    # Logic crawl: listings, comments, users
│   ├── storage.py                  # Storage 2 tầng: bronze JSONL + gold SQLite
│   ├── schema.sql                  # Star schema定义
│   ├── config.py                   # .env loader tối giản
│   ├── analytics.py                # Trending queries, trend_score, post_detail
│   ├── llm.py                      # AI analyst: digest + post analysis
│   ├── resources.py                # Resource extraction (GitHub, papers, blogs)
│   ├── longread.py                 # Magazine article renderer
│   ├── entities.py                 # Entity extraction (regex-based)
│   ├── narrative.py                # Narrative clustering
│   └── topics.py                   # Topic-level synthesis
│
├── jobs/                           # Batch jobs
│   ├── subs.txt                    # Danh sách sub cần crawl
│   ├── incremental.py              # Cron: incremental crawl + refresh metrics
│   ├── backfill_arctic_shift.py    # Backfill lịch sử qua Arctic Shift
│   ├── enrich_articles.py          # Fetch article content (trafilatura)
│   ├── enrich_comments.py          # Fetch comment discussion
│   ├── rebuild_media.py            # Backfill images/thumbnails
│   ├── extract_resources.py        # Extract links/repos from comments
│   ├── report.py                   # Generate markdown/JSON report
│   ├── delivery.py                 # Send report via Telegram/webhook
│   ├── export_story.py             # Export story JSON cho MediaWorkflow
│   └── notify.py                   # Check notifications cho followed topics
│
├── web/                            # Web server
│   ├── app.py                      # FastAPI backend (30+ endpoints)
│   ├── frontend/                   # React 19 + Vite
│   │   ├── src/
│   │   │   ├── App.jsx             # 1535 dòng: toàn bộ UI
│   │   │   ├── api.js              # getJSON helper
│   │   │   ├── main.jsx            # Entry point
│   │   │   └── styles.css          # CSS
│   │   ├── dist/                   # Built assets
│   │   └── package.json
│   └── dist/                       # Serving directory
│
├── deploy/systemd/                 # Systemd units (collector, web, etc.)
├── tests/                          # Unit tests
├── docs/                           # Documentation
└── specs/                          # Feature specs
```

---

## 4. Reddit OAuth Authentication

**File**: `reddit_crawler/auth.py`

### Cơ chế

Reddit cho phép đăng nhập ẩn danh (anonymous OAuth) bằng "installed app" grant:

```
POST https://www.reddit.com/api/v1/access_token
Authorization: Basic base64("<client_id>:")
Body: grant_type=installed_client&device_id=<random_uuid>
```

- **Client ID public** (`ohXpoqrZYub1kg`) dùng cho thử nghiệm
- **Production**: đăng ký app riêng tại reddit.com/prefs/apps (loại "installed app")
- Token hết hạn sau ~1h, tự refresh
- Không cần username/password

### TokenManager

```python
class TokenManager:
    def token() -> str      # Lấy token, tự refresh nếu hết hạn
    def invalidate()        # Buộc refresh (khi gặp 401)
```

Thread-safe, dùng `threading.Lock`.

---

## 5. Reddit HTTP Client

**File**: `reddit_crawler/client.py`

### RedditClient

```python
class RedditClient:
    def get(path, **params) -> Any         # GET request
    def get_morechildren(link_id, ids)     # Bung comment ẩn
    def request(method, path, params, data) # Core request
```

**Rate limit handling**:
- Đọc header `x-ratelimit-remaining` và `x-ratelimit-reset`
- Khi remaining < 3 → sleep theo reset time
- Retry 5 lần cho 401, 429, 5xx với exponential backoff
- Base URL: `https://oauth.reddit.com`

---

## 6. Crawl Logic

**File**: `reddit_crawler/crawl.py`

### Listing (posts)

```python
iter_listing(client, subreddit, sort, t, max_items, start_after)
# → Iterator[dict] — mỗi dict là 1 post data từ Reddit
# Tự phân trang qua con trõ `after`, max 1000 item (Reddit hard cap)
```

### Comments

```python
fetch_post_with_comments(client, post_id_or_url, subreddit, sort, depth, resolve_more)
# → (post_dict, [comment_dict, ...])
# Lấy post + toàn bộ cây comment, flatten thành list phẳng
# resolve_more=True → bung node "more" ẩn bằng /api/morechildren
```

### Search

```python
search_subreddits(client, query, limit)
# → [subreddit_dict, ...] — tìm sub theo keyword
```

### User

```python
fetch_user(client, username)
# → user_dict hoặc None (nếu bị xóa/suspend)
```

---

## 7. Storage (2 tầng)

**File**: `reddit_crawler/storage.py` + `schema.sql`

### Bronze Layer (raw)

Mỗi post/comment/user → 1 file JSONL append-only:

```
raw/post/<YYYY-MM-DD>.jsonl
raw/comment/<YYYY-MM-DD>.jsonl
raw/user/<YYYY-MM-DD>.jsonl
raw/subreddit/<YYYY-MM-DD>.jsonl
```

Mỗi dòng = 1 JSON object = Reddit API response gốc. Mục đích: tái xử lý sau nếu đổi schema.

### Gold Layer (SQLite star schema)

```
┌─────────────────────┐
│    dim_subreddit    │  ← Subreddit info
│    dim_author       │  ← User info
│    dim_date         │  ← Date dimension
└────────┬────────────┘
         │
┌────────▼────────────┐
│     fact_post       │  ← Mỗi post = 1 dòng
│     fact_comment    │  ← Mỗi comment = 1 dòng
│  fact_post_metrics  │  ← Snapshot score/comments theo thời gian
│  fact_post_media    │  ← Image/thumbnail URL
│ fact_article_content│  ← Article body (đã extract)
│  enrichment_state   │  ← Trạng thái enrichment
│   crawl_state       │  ← High-water mark cho incremental
└────────┬────────────┘
         │
┌────────▼────────────┐
│     ai_digest       │  ← AI briefing output
│  ai_post_analysis   │  ← AI post analysis output
│fact_extracted_resource│ ← GitHub repos, papers, links
│   fact_longread     │  ← Magazine-style article
└─────────────────────┘
```

### Storage class

```python
class Storage:
    def __init__(db_path, raw_dir)
    def upsert_subreddit(data)        # Insert/update subreddit
    def upsert_post(data, subreddit_id) # Insert/update post
    def upsert_comment(data, post_id, subreddit_id) # Insert/update comment
    def upsert_author(data)           # Insert/update author
    def snapshot_metrics(data)        # Ghi fact_post_metrics (score history)
    def write_raw(kind, items)        # Append JSONL
    def set_state(scope, last_utc, last_fullname)  # Lưu high-water mark
    def get_state(scope)              # Đọc high-water mark
    def commit()                      # WAL checkpoint
    def close()                       # Đóng connection
    def counts()                      # Đếm rows mỗi table
```

---

## 8. Database Schema (chi tiết)

### Dimensions

```sql
dim_subreddit (subreddit_id, display_name, title, subscribers, created_utc, ...)
dim_author (author_name, author_id, total_karma, link_karma, comment_karma, ...)
dim_date (date_key, full_date, year, quarter, month, day, weekday)
```

### Facts

```sql
fact_post (post_id, subreddit_id, author_name, title, selftext, url, domain,
           permalink, score, num_comments, upvote_ratio, created_utc, fetched_at)

fact_comment (comment_id, post_id, subreddit_id, author_name, parent_fullname,
              depth, body, score, is_submitter, created_utc)

fact_post_metrics (post_id, observed_at, score, ups, upvote_ratio, num_comments)
-- Primary key: (post_id, observed_at) — mỗi lần cron chạy tạo 1 snapshot
```

### AI Output

```sql
ai_digest (digest_id, period, provider, model, status, title,
           executive_summary, payload_json, source_count, input_tokens, output_tokens)

ai_post_analysis (post_id, provider, model, status, payload_json, comment_count,
                  input_tokens, output_tokens, generated_at)
```

### Resource Mining

```sql
fact_extracted_resource (resource_id, post_id, comment_id, url, domain,
                         resource_type, title, description, context_snippet,
                         author_name, score, extracted_at)
-- resource_type: github_repo | arxiv_paper | tech_blog | tool | other
```

### Longread

```sql
fact_longread (post_id, hook, context, insight, evidence, open_questions,
               reading_time_sec, generated_at, provider)
```

### Knowledge Intelligence (sẽ bỏ trong simplify)

```sql
dim_entity, fact_entity_mention, fact_entity_relation
dim_narrative, fact_narrative_post
fact_topic_synthesis
user_profile
```

---

## 9. Trend Score Algorithm

**File**: `reddit_crawler/analytics.py` — `trending_posts()`

```
age_hours = (now - created_utc) / 3600

score_velocity = (latest_score - previous_score) / hours_between_snapshots
comment_velocity = (latest_comments - previous_comments) / hours_between_snapshots

recency = 4.0 / (1.0 + age_hours / 6.0)

trend_score = 0.55 * log(1 + latest_score)
            + 0.8  * log(1 + latest_comments)
            + 1.5  * log(1 + score_velocity)
            + 2.2  * log(1 + comment_velocity)
            + recency

composite_value = trend_score
                + 1.2 * log(1 + resource_count)
                + 1.5 * (1 if has AI analysis else 0)
```

**Ý nghĩa**:
- `trend_score`: kết hợp độ mới (recency), score hiện tại, velocity (tốc độ tăng)
- `composite_value`: cộng thêm资源数量 và phân tích AI
- Posts được xếp hạng theo `composite_value` giảm dần

---

## 10. AI Analysis — Prompts & Output

**File**: `reddit_crawler/llm.py`

### 10.1 Post Analysis (phân tích 1 bài viết)

**Trigger**: `cli.py analyze-post <post_id>` hoặc API `/api/knowledge/generate/{post_id}`

**Input cho LLM**:
```json
{
  "post": {
    "post_id": "...",
    "title": "...",
    "subreddit": "...",
    "author": "...",
    "score": 1234,
    "num_comments": 567,
    "selftext": "...(tối đa 8000 chars)",
    "article_body": "...(tối đa 8000 chars)"
  },
  "comments": [
    {
      "comment_id": "...",
      "author": "...",
      "score": 456,
      "depth": 0,
      "parent_id": "t3_xxx",
      "body": "...(tối đa 1800 chars)"
    }
    // ... tối đa 120 comments
  ]
}
```

**Prompt** (POST_ANALYSIS_INSTRUCTIONS):

```
Bạn là chuyên gia phân tích tin tức công nghệ. Nhiệm vụ: đọc bài viết Reddit và
toàn bộ comment, sau đó tạo BẢN ĐÚC KẾT TRI THỨC HOÀN TOÀN BẰNG TIẾNG VIỆT.

YÊU CẦU QUAN TRỌNG NHẤT:
- KHÔNG được chỉ nói "cộng đồng đánh giá tốt/xấu" hay đếm upvote một cách vô nghĩa.
- PHẢI đúc kết ra GIÁ TRỊ THỰC SỰ: bài viết nói về cái gì, công nghệ/sản phẩm
  hoạt động thế nào, use case thực tế nào đáng thử, hạn chế gì, ai nên dùng.
- PHẢI tổng hợp thông tin từ COMMENT thành tri thức: ví dụ nếu comment chia sẻ
  repo GitHub, tool, kinh nghiệm triển khai → đúc kết thành danh sách công cụ
  và bài học cụ thể.

CẤU TRÚC OUTPUT (100% tiếng Việt):

1. topic: Tiêu đề tiếng Việt súc tích mô tả nội dung cốt lõi.
2. author_goal: 2-4 câu tóm tắt nội dung thực sự của bài viết.
3. problem_context: Bối cảnh kỹ thuật và vấn đề mà bài viết giải quyết.
4. community_consensus: ĐÚC KẾT tri thức từ tất cả comment thành 1 đoạn văn mạch lạc.
5. opinion_groups: Các nhóm quan điểm/giải pháp cụ thể (label, summary, comment_ids).
6. suggestions: Tool/library/resource được đề xuất trong comment.
7. learning_points: 3-6 bài học / đề xuất hành động CỤ THỂ.
8. disagreements: Các tranh luận cụ thể trong comment (nếu có).
9. unanswered_questions: Câu hỏi chưa được giải đáp.

QUY TẮC:
- Toàn bộ output PHẢI bằng tiếng Việt tự nhiên, mạch lạc.
- KHÔNG quote nguyên văn tiếng Anh dài. Dịch và tóm tắt.
- KHÔNG bịa URL, tên tool, hay số liệu không có trong input.
```

**Output** (PostAnalysis Pydantic model):

```json
{
  "language": "vi",
  "source_post_id": "1rfgu9a",
  "domain": "ai_models",
  "topic": "Claude Artifacts giờ có thể kết nối MCP Connectors để tạo dashboard tương tác",
  "author_goal": "Claude Artifacts giờ không còn chỉ là trang tĩnh. Từ hôm nay, Artifacts có thể kết nối trực tiếp với MCP Connectors...",
  "problem_context": "Chủ đề nhận được 1234 điểm upvote và 567 thảo luận từ r/ClaudeAI.",
  "community_consensus": "ĐÚC KẾT KẾT QUẢ TỪ 567 BÌNH LUẬN:\n• (450 upvotes) u/xxx: \"Tool này thay đổi cách mình làm việc...\"",
  "opinion_groups": [
    {
      "label": "🏆 Đề xuất Hàng đầu",
      "stance": "support",
      "summary": "Giải pháp được đánh giá cao nhất: ...",
      "comment_ids": ["abc123"],
      "support_count": 450
    }
  ],
  "suggestions": [
    {
      "name": "Tool/repo được đề xuất",
      "kind": "tool",
      "description": "Mô tả tool làm gì",
      "url": "https://github.com/...",
      "comment_ids": ["abc123"]
    }
  ],
  "learning_points": [
    "Nên dùng JSON task list thay vì Markdown vì máy parse được",
    "..."
  ],
  "disagreements": ["Tranh luận giữa tool all-in-one vs custom harness"],
  "unanswered_questions": ["Harness nào sẽ trở thành tiêu chuẩn công nghiệp?"],
  "methodology_note": "Bản tổng hợp từ bài viết và cộng đồng thảo luận."
}
```

**Lưu vào DB**: `ai_post_analysis.payload_json` (JSON string)

---

### 10.2 AI Digest (Briefing tổng hợp)

**Trigger**: `cli.py ai-digest --period 3h`

**Input cho LLM**:
```json
[
  {
    "post_id": "...",
    "title": "...",
    "subreddit": "...",
    "score": 1234,
    "comments_count": 567,
    "score_velocity_per_hour": 45.2,
    "comment_velocity_per_hour": 12.3,
    "source_url": "...",
    "article_excerpt": "...(tối đa 3000 chars)",
    "reddit_selftext": "...(tối đa 1800 chars)",
    "top_comments": [{"score": 100, "text": "...(tối đa 600 chars)"}],
    "image_url": "..."
  }
  // ... tối đa 40 posts
]
```

**Prompt** (SYSTEM_INSTRUCTIONS):

```
Bạn là biên tập viên dữ liệu cho bản tin công nghệ tiếng Việt. Chỉ được dùng dữ
liệu trong INPUT_SOURCES; không dùng trí nhớ để thêm sự kiện, model, benchmark,
giá, ngày hoặc số liệu. Mọi story, model update và comparison phải ghi đúng ít
nhất một source_post_id có trong input. Reddit/community là tín hiệu, không phải
nguồn xác nhận chính thức: hạ confidence và nói rõ khi chưa có bài báo/nội dung
đầy đủ. Chỉ tạo comparison khi input có bằng chứng so sánh trực tiếp; không tự
xếp hạng "model tốt nhất" từ popularity. Gộp các post cùng một sự kiện, tránh
đếm trùng. Số liệu phải tính được từ trường score/comments/velocity trong input.
Viết ngắn, rõ, ưu tiên: model mới, context window/token, pricing, deprecation,
benchmark, API/tooling, security và thay đổi ảnh hưởng developer/data scientist.
Không dùng markdown trong các field. watchlist là điều cần theo dõi, không khẳng
định là sự thật. Trả structured output đúng schema.
```

**Gemini-specific** (thêm vào SYSTEM_INSTRUCTIONS):

```
Bạn đang chạy như một analyst agent có Google Search và URL Context. Dùng hai
tool này để kiểm chứng claim quan trọng từ website chính thức của vendor hoặc
tài liệu gốc. Web chỉ dùng để kiểm chứng/enrich các signal trong INPUT_SOURCES;
không tạo story mới không có source_post_id tương ứng.
```

**Output** (DigestContent Pydantic model):

```json
{
  "language": "vi",
  "title": "Technology Radar — 3h",
  "executive_summary": "Có 40 tín hiệu nổi bật từ 12 cộng đồng...",
  "key_numbers": [
    {"name": "Tín hiệu nổi bật", "value": 40, "unit": "post", "context": "Cửa sổ 3h"}
  ],
  "model_updates": [
    {
      "vendor": "Anthropic",
      "model": "Claude 3.7",
      "change_type": "release",
      "headline": "Anthropic ra mắt Claude 3.7 Sonnet",
      "summary": "...",
      "confidence": 0.85,
      "source_post_ids": ["abc123"]
    }
  ],
  "comparisons": [],
  "stories": [
    {
      "category": "technology",
      "headline": "OpenAI hợp tác SoftBank 10 tỷ USD",
      "summary": "...",
      "why_it_matters": "...",
      "key_facts": ["Score: 5000", "Bình luận: 320"],
      "confidence": 0.8,
      "source_post_ids": ["def456"],
      "image_url": "..."
    }
  ],
  "watchlist": ["Xác minh các model update từ trang chính thức"],
  "methodology_note": "Local fallback: thống kê trực tiếp, chưa có suy luận từ LLM.",
  "sources": [{"post_id": "...", "title": "...", "url": "...", "subreddit": "..."}]
}
```

---

### 10.3 Provider Fallback Chain

```python
# Post Analysis
candidates = ["gemini", "openai", "local"]  # auto mode
# Thử依次: Gemini → OpenAI → local (regex + template)
# Nếu provider cụ thể → chỉ chạy provider đó

# Digest
candidates = ["gemini", "openai", "local"]  # auto mode
# Gemini dùng Antigravity Agent (managed) + Google Search + URL Context
# OpenAI dùng Responses API + structured output
# Local: thống kê + regex, không gọi LLM
```

---

### 10.4 Local Fallback (không tốn API)

**local_post_analysis()**: Dùng regex extract insight từ comments, tạo opinion groups từ top comments, extract URLs thành suggestions. Template-based, không có suy luận thực sự.

**local_digest()**: Đếm score/comments, detect model names bằng regex, tạo stories từ top posts. Thống kê thuần túy.

---

## 11. CLI Commands (toàn bộ)

**File**: `cli.py`

### Data Collection

| Command | Mô tả | Input | Output |
|---------|-------|-------|--------|
| `probe` | Kiểm tra token + lấy thử 3 posts | - | Token info + 3 posts |
| `find-subs <query>` | Tìm subreddit theo keyword | query string | Danh sách sub |
| `crawl-sub <sub> [opts]` | Crawl posts (và comments) 1 sub | subreddit name | Posts + comments → DB + JSONL |
| `crawl-post <url>` | Crawl 1 post + comments | Reddit URL hoặc post ID | Post + comments → DB |
| `crawl-user <name>` | Crawl thông tin user | Username | User info → DB |
| `incremental [opts]` | Cron: lấy posts MỚI + refresh metrics | Subs list (subs.txt) | New posts → DB |
| `backfill <sub> --after <date>` | Backfill lịch sử qua Arctic Shift | Sub + date range | Historical posts → DB |

### Analysis

| Command | Mô tả | Input | Output |
|---------|-------|-------|--------|
| `analyze-post <id>` | Phân tích 1 post bằng LLM | Post ID | PostAnalysis → ai_post_analysis |
| `analyze-top [opts]` | Phân tích batch các posts nổi | Period + limit | Batch analysis → ai_post_analysis |

### AI Digest

| Command | Mô tả | Input | Output |
|---------|-------|-------|--------|
| `ai-digest [opts]` | Tạo AI briefing tổng hợp | Period + provider | DigestContent → ai_digest |

### Content

| Command | Mô tả | Input | Output |
|---------|-------|-------|--------|
| `enrich [opts]` | Fetch article content + discussion | Period + kind | Article body → DB |
| `rebuild-media` | Backfill ảnh/thumbnail từ raw | - | fact_post_media |
| `extract-resources` | Quét bóc tách links/repos | - | fact_extracted_resource |
| `generate-longreads` | Tạo magazine articles | Limit | fact_longread |
| `extract-entities` | Trích xuất thực thể (company, model, tech) | Period | dim_entity + mentions |

### Intelligence

| Command | Mô tả | Input | Output |
|---------|-------|-------|--------|
| `synthesize` | Nhóm posts thành narratives | Period | dim_narrative |
| `topic-insight [opts]` | Tổng hợp tri thức theo chủ đề | Topic + period | fact_topic_synthesis |

### Export

| Command | Mô tả | Input | Output |
|---------|-------|-------|--------|
| `report [opts]` | Tạo/gửi báo cáo | Period + format | reports/*.md + *.json |
| `story-export --top` | Xuất tin cho MediaWorkflow | Period | Story JSON + ảnh → MediaWorkflow |

### System

| Command | Mô tả | Input | Output |
|---------|-------|-------|--------|
| `serve [opts]` | Chạy API + dashboard | Host + port | http://127.0.0.1:8080 |
| `stats` | Đếm rows trong DB | - | Table counts |
| `check-notifications` | Kiểm tra tin mới theo chủ đề quan tâm | - | Notification list |

---

## 12. API Endpoints

**File**: `web/app.py`

### Core

```
GET  /api/health                    → {"status": "ok", "database": "...", "last_fetched_at": ...}
GET  /api/stats                     → {table: count, ...}
GET  /api/trending?period=day&limit=30 → {period, domains, items: [...]}
GET  /api/digests/latest?period=3h  → AI digest object
GET  /api/posts/{post_id}           → Post detail + comments + analysis
```

### Knowledge Hub

```
GET  /api/knowledge/feed?domain=&limit=30&offset=0 → AI analysis feed
GET  /api/knowledge/{post_id}       → Single analysis detail
POST /api/knowledge/generate/{post_id}?provider=gemini → Trigger AI analysis
```

### Resources

```
GET  /api/resources?kind=all&limit=50 → Extracted resources (GitHub, papers, etc.)
```

### Longread

```
GET  /api/longread/feed?limit=30   → Magazine articles feed
GET  /api/longread/{post_id}       → Single longread (auto-generate if missing)
GET  /api/posts/{post_id}/export?format=markdown → Export as markdown
```

### Knowledge Intelligence

```
GET  /api/entities?entity_type=&limit=60 → Entity list
GET  /api/entities/{entity_id}           → Entity detail
GET  /api/entities/{entity_id}/timeline  → Entity timeline
GET  /api/narratives?status=active       → Narrative list
GET  /api/narratives/{narrative_id}      → Narrative detail + timeline
GET  /api/topics                         → Topic list
GET  /api/topics/{topic_key}/synthesis   → Topic synthesis
GET  /api/topics/syntheses               → All syntheses
```

### User

```
GET  /api/user/preferences           → User prefs
PUT  /api/user/preferences           → Update prefs
GET  /api/user/bookmarks             → Bookmarks
POST /api/user/bookmarks/{post_id}   → Add bookmark
DELETE /api/user/bookmarks/{post_id} → Remove bookmark
GET  /api/notifications              → Notifications
POST /api/notifications/read         → Mark as read
POST /api/push/subscribe             → Web push subscribe
POST /api/push/unsubscribe           → Web push unsubscribe
```

### Static

```
GET  /                               → Dashboard SPA
GET  /signals                        → Dashboard SPA (signals tab)
GET  /knowledge                      → Dashboard SPA (knowledge tab)
GET  /resources                      → Dashboard SPA (resources tab)
GET  /entities                       → Dashboard SPA (entities tab)
GET  /narratives                     → Dashboard SPA (narratives tab)
GET  /bookmarks                      → Dashboard SPA (bookmarks tab)
GET  /post/{post_id}                 → Dashboard SPA (detail page)
GET  /longread/{post_id}             → Dashboard SPA (longread page)
```

---

## 13. Dashboard UI

**File**: `web/frontend/src/App.jsx` (1535 dòng)

### Architecture

React 19 SPA, client-side routing (không dùng React Router — dùng `window.location.pathname` + `popstate`). Build bằng Vite, serve từ FastAPI static files.

### Pages / Components

```
App
├── HomePage
│   ├── Header (logo, nav tabs, search, notification bell)
│   ├── Hero (stats: posts, comments, subs, AI-analyzed)
│   │
│   ├── [Tab: Knowledge]
│   │   └── KnowledgeHub
│   │       ├── Domain filter pills (AI, Software, Security, ...)
│   │       ├── Knowledge cards (topic, consensus, learning points, tools)
│   │       └── KnowledgeDetailModal (full analysis view)
│   │
│   ├── [Tab: Signals]
│   │   ├── Briefing (AI digest sidebar)
│   │   ├── DomainFilter (sidebar)
│   │   ├── FeaturedSignal (top signal)
│   │   ├── SignalCard list (ranked signals)
│   │   └── PulseRail (trending + recently analyzed)
│   │
│   ├── [Tab: Resources]
│   │   └── ResourceHub (GitHub repos, papers, blogs)
│   │
│   ├── [Tab: Entities]
│   │   └── EntitiesHub (entity cards + timeline drawer)
│   │
│   ├── [Tab: Narratives]
│   │   └── NarrativesHub (story arc cards + detail modal)
│   │
│   └── [Tab: Bookmarks]
│       └── BookmarksPage (saved longreads)
│
├── DetailPage (post detail: analysis panel + comments + resources)
├── LongreadView (magazine article: 5 sections + progress bar)
└── Footer
```

### UX Flow

1. **Mở app** → Tab Knowledge Hub (mặc định): hiển thị các bài đã AI phân tích
2. **Chuyển sang Signals** → Xem trending posts theo period (3h/24h/7d/30d/1y)
3. **Click post** → DetailPage: xem analysis panel (4 tabs: Tổng hợp, Quan điểm, Đề xuất, Tranh luận)
4. **Click "Generate"** → Trigger AI analysis qua API → reload
5. **Longread** → Đọc magazine-style article (5 sections)
6. **Resources** → Xem GitHub repos, papers được cộng đồng chia sẻ
7. **Entities** → Xem bản đồ thực thể (công ty, model, tech)
8. **Narratives** → Xem các story arc (nhóm posts liên quan)

---

## 14. Incremental Crawl (Cron)

**File**: `jobs/incremental.py`

### Algorithm

```
Với mỗi sub trong subs.txt:
  1. Đọc high-water mark (last_utc) từ crawl_state
  2. Duyệt /new (mới → cũ)
  3. Với mỗi post:
     - Nếu created_utc <= last_utc → DỪNG (đã thấy)
     - Nếu created_utc > last_utc → upsert + snapshot metrics
  4. Lưu last_utc mới nhất
  5. (tùy chọn) Lấy comments cho posts mới
  6. Refresh metrics cho posts gần đây (≤48h) qua /api/info
```

### Bootstrap Mode

Lần đầu chạy (chưa có high-water mark):
```
--max-per-sub 200 → chỉ lấy 200 posts đầu, lưu bootstrap state
Lần sau → tiếp tục từ chỗ dừng
Sau khi full → lưu high-water mark chính thức
```

### Output

```
Incremental 6 sub: codex, ClaudeAI, artificial, AI_Agents, Anthropic, claudeskills
  r/codex                +12 post mới
  r/ClaudeAI             +8 post mới
  ...
XONG: +45 post mới, 0 comment, 23 refresh -> reddit.db
```

---

## 15. Backfill (Arctic Shift)

**File**: `jobs/backfill_arctic_shift.py`

### Nguồn dữ liệu

Arctic Shift (`arctic-shift.photon-reddit.com/api`) — bản sao lịch sử Reddit, API công khai, không cần auth.

### Algorithm

```
GET /posts/search?subreddit=technology&after=1704067200&before=1711929600&limit=100&sort=asc
→ Mỗi trang: tạo con trỏ after = max(created_utc) vừa nhận
→ Dừng khi hết dữ liệu hoặc chạm before
→ Map trực tiếp vào star schema qua Storage.upsert_*
```

### Usage

```bash
cli.py backfill technology --after 2025-01-01 --before 2025-04-01
cli.py backfill technology --after 2025-01-01 --kind both  # posts + comments
```

---

## 16. Resource Extraction

**File**: `reddit_crawler/resources.py`

### Algorithm

```
1. Scan comments + posts có trong DB
2. Regex tìm URLs trong body text
3. Lọc bỏ: reddit.com, imgur.com, twitter.com, ...
4. Phân loại theo domain:
   - github.com/* → github_repo
   - arxiv.org/*, huggingface.co/papers/* → arxiv_paper
   - substack.com/*, medium.com/*, dev.to/* → tech_blog
   - huggingface.co/models/* → tool
5. Extract context snippet (câu xung quanh URL)
6. Lưu vào fact_extracted_resource
```

---

## 17. Longread Renderer

**File**: `reddit_crawler/longread.py`

### Input → Output

```
PostAnalysis (có sẵn từ ai_post_analysis)
  ↓ render (không gọi LLM)
fact_longread:
  hook           = topic + author_goal (2-3 câu mở bài)
  context        = problem_context + claimed_results
  insight        = community_consensus (giá trị chính)
  evidence       = opinion_groups + suggestions + learning_points
  open_questions = disagreements + unanswered_questions
  reading_time   = estimate từ tổng ký tự (~200 từ/phút tiếng Việt)
```

---

## 18. Story Export (MediaWorkflow Bridge)

**File**: `jobs/export_story.py`

### Flow

```
1. pick_top_post(): tìm post trend_score cao nhất CÓ ai_post_analysis
2. load_post_bundle(): đọc post + analysis + resources từ DB
3. Map sang RedditStoryData (16 fields):
   - postId, subreddit, title, score, comments, upvoteRatio
   - image, source, hook, hookSub, daily, dailySub
   - policy, reactions, lesson, question
4. Tạo 8 câu thoại JSON (dialogue lines)
5. Ghi vào MediaWorkflow/stories/<post_id>.json
6. Copy ảnh (thumbnail/screenshot) vào MediaWorkflow/public/images/
```

### CLI Usage

```bash
cli.py story-export --top                    # Tự chọn post trend cao nhất
cli.py story-export --post-id 1rfgu9a       # Chọn post cụ thể
cli.py story-export --top --dry-check       # Không tải ảnh
```

---

## 19. Dependencies

```
requests>=2.31          # HTTP client cho Reddit API
fastapi>=0.115          # Web framework
uvicorn[standard]>=0.30 # ASGI server
trafilatura>=2.1        # Article content extraction
openai>=2               # OpenAI API client
google-genai>=2         # Gemini API client
```

Frontend:
```
react@19, vite, lucide-react (icons)
```

---

## 20. Cron Schedule (Systemd)

```bash
# Crawl mỗi 20 phút
reddit-crawl.timer → reddit-crawl.service → cli.py incremental

# Enrichment nội dung mỗi giờ
reddit-enrich.timer → reddit-enrich.service → cli.py enrich

# AI analysis mỗi 3 giờ (lúc phút 10)
reddit-ai@3h.timer → reddit-ai@3h.service → cli.py analyze-top + ai-digest

# Daily briefing lúc 07:10
reddit-ai@day.timer → cli.py ai-digest --period day

# Web service tự restart
reddit-web.service → cli.py serve
```

---

## 21. Environment Variables

```bash
# Reddit
REDDIT_USER_AGENT="python:reddit-radar:v0.2 (by /u/username)"
REDDIT_CLIENT_ID=xxx           # Production: app riêng
REDDIT_DB_PATH=reddit.db

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.6-luna

# Gemini
GEMINI_API_KEY=xxx
GEMINI_AGENT=antigravity-preview-05-2026
GEMINI_NORMALIZER_MODEL=gemini-3.5-flash

# Report
REPORT_DIR=reports
REPORT_WEBHOOK_URL=xxx         # Optional
TELEGRAM_BOT_TOKEN=xxx         # Optional
TELEGRAM_CHAT_ID=xxx           # Optional
```

---

## 22. Common Workflows

### Workflow 1: Bootstrap từ đầu

```bash
# 1. Setup
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
cp .env.example .env  # Edit với keys thật

# 2. Kiểm tra kết nối
.venv/bin/python cli.py probe

# 3. Incremental crawl (bootstrap)
.venv/bin/python cli.py incremental --max-per-sub 200

# 4. Backfill lịch sử
.venv/bin/python cli.py backfill technology --after 2025-01-01

# 5. Enrich nội dung
.venv/bin/python cli.py enrich --kind both --period day --limit 20

# 6. Phân tích AI
.venv/bin/python cli.py analyze-top --period day --limit 10
.venv/bin/python cli.py ai-digest --period 3h

# 7. Chạy web
.venv/bin/python cli.py serve
```

### Workflow 2: Daily operations (cron)

```
Mỗi 20 phút: incremental crawl
Mỗi giờ: enrich articles
Mỗi 3 giờ: analyze-top + ai-digest
```

### Workflow 3: Xuất video

```bash
# 1. Đảm bảo đã có AI analysis
.venv/bin/python cli.py analyze-top --period week --limit 5

# 2. Export story
.venv/bin/python cli.py story-export --top --period week

# 3. Render video (bên MediaWorkflow)
cd ../MediaWorkflow && ./make-video.sh stories/<post_id>.json
```

---

## 23. DB Gotchas

- **`reddit.db` là dữ liệu sản xuất sống** (23k+ post). WAL file cho thấy có thể collector systemd đang ghi.
- **Không xoá, không migrate tại chỗ** — làm việc trên copy
- **Backup strategy**: `reddit.db.backup-YYYYMMDD-before-*`
- **Schema migrations**: chạy qua `Storage.__init__()` tự động qua schema_migration table
- **Foreign keys**: Bật `PRAGMA foreign_keys=ON`
- **WAL mode**: `PRAGMA journal_mode=WAL` cho concurrent read/write
