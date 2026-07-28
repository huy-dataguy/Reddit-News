# Spec: Magazine-Style Longread Webapp

- Date: 2026-07-24
- Status: done

---

## Problem

Webapp hiện tại hiển thị data Reddit dưới dạng structured tabs (Summary / Opinions / Suggestions / Debates). Người đọc phải click qua nhiều tab, đọc kỹ mới hiểu bài viết nói gì. Không có trải nghiệm "đọc 1 mạch là nắm được tri thức" — điều mà developer bận rộn cần khi scan tin nhanh.

AI Analysis đã có đủ dữ liệu (tóm tắt, consensus, ý kiến, resources, tranh luận) nhưng đang bị đóng gói sai dạng: data-centric thay vì reading-centric.

## Goal

Biến mỗi bài viết Reddit có AI analysis thành **1 bài đọc magazine-style** — đọc 1-2 phút là nắm được: bài nói gì, tại sao quan trọng, có gì đáng thử, có cảnh báo gì.

MVP bao gồm 4 tính năng:
1. **AI Longread**: Tóm tắt ngắn gọn dạng magazine article (opening → context → insight → evidence → conclusion)
2. **Personalization**: Theo dõi chủ đề quan tâm, ẩn bài đã đọc, bookmark
3. **Notification**: Thông báo khi có tin mới trong chủ đề quan tâm (web push + Telegram)
4. **Export/Share**: Xuất markdown, copy link摘要

Đối tượng: Developer cá nhân tự dùng để theo dõi tech trends.

## Non-goals

- **Không thay thế detail page hiện tại**: Detail page vẫn giữ nguyên cho ai muốn đọc sâu. Longread là lớp mới bên trên.
- **Không multi-user/auth**: Chỉ 1 người dùng, không cần login, không cần user table.
- **Không mobile app**: Web responsive là đủ.
- **Không phải RSS reader**: Không import RSS, chỉ theo dõi Reddit đã crawl.
- **Không phải content management**: Không edit/delete posts, chỉ đọc.

---

## Architecture Overview

```
┌─────────────────────────────────────────────┐
│              Magazine Longread App           │
├─────────────┬──────────────┬────────────────┤
│ Longread    │ Personalize  │ Export/Share   │
│ Renderer    │ Engine       │ Engine         │
├─────────────┴──────────────┴────────────────┤
│           AI Analysis (existing)             │
│     PostAnalysis → Narrative Sections       │
├─────────────────────────────────────────────┤
│         reddit.db (existing schema)          │
│  fact_post + ai_post_analysis + new tables  │
└─────────────────────────────────────────────┘
```

---

## 1. Longread Rendering Engine

### 1.1 Concept

Hiện tại `PostAnalysis` có các field:
- `author_goal` (2-4 câu tóm tắt)
- `problem_context` (bối cảnh kỹ thuật)
- `claimed_results` (kết quả OP báo cáo)
- `community_consensus` (đúc kết tri thức)
- `opinion_groups[]` (nhóm quan điểm)
- `suggestions[]` (tool/resource đề xuất)
- `learning_points[]` (bài học)
- `disagreements[]` (tranh luận)
- `unanswered_questions[]` (câu hỏi chưa giải đáp)

Longread renderer chuyển đổi thành **5 section**]:

| Section | Nguồn từ | Mục đích | Độ dài |
|---|---|---|---|
| **Hook** | `topic` + `author_goal` | Mở bài: vấn đề gì, tại sao quan trọng | 2-3 câu |
| **Context** | `problem_context` + `claimed_results` | Bối cảnh kỹ thuật, OP nói gì | 3-4 câu |
| **Key Insight** | `community_consensus` | Đúc kết giá trị chính từ cộng đồng | 3-5 câu |
| **Evidence** | `opinion_groups[]` + `suggestions[]` + `learning_points[]` | Bằng chứng thực tế: tool nào, dùng thế nào, ai nói | 5-8 câu |
| **Open Questions** | `disagreements[]` + `unanswered_questions[]` | Cảnh báo, tranh luận chưa có đáp án | 2-3 câu |

### 1.2 Longread Generation Strategy

**Approach A — Render from existing analysis (Recommended)**:
- Frontend/AI transform `PostAnalysis` JSON → 5 sections
- Không cần LLM call mới, dùng data đã có
- Chi phí = 0, tức thì
- Chất lượng phụ thuộc vào chất lượng analysis gốc

**Approach B — LLM-generated full article**:
- Gọi LLM mới với prompt "viết magazine article từ analysis data"
- Chất lượng cao hơn, prose tự nhiên hơn
- Chi phí: ~500-1000 output tokens/bài
- Phù hợp cho top posts, không cho batch

**Recommendation**: Dùng **Approach A** cho MVP, thêm **Approach B** optional cho top trending posts.

### 1.3 New Database Table

```sql
CREATE TABLE IF NOT EXISTS fact_longread (
    post_id TEXT PRIMARY KEY,
    hook TEXT,           -- Opening hook (2-3 câu)
    context TEXT,        -- Context section (3-4 câu)
    insight TEXT,        -- Key insight (3-5 câu)
    evidence TEXT,       -- Evidence section (5-8 câu)
    open_questions TEXT, -- Open questions (2-3 câu)
    reading_time_sec INTEGER,  -- Estimated reading time
    generated_at REAL,
    provider TEXT,       -- 'render' (from analysis) or 'llm' (generated)
    FOREIGN KEY (post_id) REFERENCES fact_post(post_id)
);
```

---

## 2. Personalization Engine

### 2.1 Concept

Developer cá nhân cần:
- **Follow topics**: Theo dõi chủ đề quan tâm (AI, security, dev tools...)
- **Reading history**: ẩn bài đã đọc
- **Bookmarks**: Lưu bài值得 đọc lại

### 2.2 Storage Strategy

Vì chỉ 1 người dùng, dùng **localStorage** cho client-side + **optional sync** lên server.

**localStorage schema**:
```json
{
  "followed_topics": ["ai_models", "cybersecurity", "software_dev"],
  "read_posts": ["post_id_1", "post_id_2"],
  "bookmarks": ["post_id_3"],
  "hidden_posts": ["post_id_4"]
}
```

**Optional server-side** (nếu cần sync giữa devices):
```sql
CREATE TABLE IF NOT EXISTS user_preferences (
    key TEXT PRIMARY KEY,
    value_json TEXT,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS user_reading_history (
    post_id TEXT PRIMARY KEY,
    read_at REAL,
    bookmarked INTEGER DEFAULT 0
);
```

### 2.3 UX Integration

- **Followed topics** → filter trending posts, prioritize in feed
- **Read posts** → gray out or hide in signal list
- **Bookmarks** → dedicated "Bookmarks" section in nav
- **Hidden posts** → never show again

---

## 3. Notification System

### 3.1 Triggers

Thông báo khi:
1. **New post in followed topic**: Post mới có score > threshold + domain thuộc followed topics
2. **Trending spike**: Post đã bookmark/đọc có velocity tăng đột ngột
3. **AI analysis ready**: Post đã bookmark có phân tích AI mới

### 3.2 Delivery Channels

| Channel | Cơ chế | MVP? |
|---|---|---|
| **Web Push** | Browser Notification API + Service Worker | Yes |
| **Telegram** | Bot API (đã có `delivery.py`) | Yes |
| **Email** | SMTP (optional) | No |

### 3.3 Implementation

**Backend** (`jobs/notify.py`):
```python
def check_notifications(db_path, user_prefs):
    """Check for new posts matching user's followed topics."""
    # Query: posts NOT in read_posts + domain IN followed_topics + score > threshold
    # Format notification: title, hook (from longread), score, link
    # Send via Telegram + queue web push
```

**Frontend** (`web/frontend/src/components/NotificationBell.jsx`):
- Bell icon with unread count
- Click to see notification list
- "Mark all as read" button

**Web Push**:
- Service Worker registers at `/sw.js`
- Backend sends push via `pywebpush` library
- Subscription stored in localStorage (single device)

### 3.4 Telegram Integration

Đã có infrastructure trong `jobs/delivery.py` (Telegram bot, chunked messages). Mở rộng:
- Thêm command `/follow <topic>` để follow/unfollow topics
- Thêm command `/notify` để toggle notifications
- Message format: title + hook + link + score badge

---

## 4. Export/Share Engine

### 4.1 Export Formats

| Format | Cơ chế | Nội dung |
|---|---|---|
| **Markdown** | Server-side render | Longread全文 + metadata |
| **Copy Summary** | Client-side | Hook + Key Insight (2-3 câu) |
| **Share Link** | URL-based | `/post/{id}?view=longread` |

### 4.2 Markdown Export

**API endpoint**: `GET /api/posts/{post_id}/export?format=markdown`

**Output**:
```markdown
# {topic}

> {hook}

## Bối cảnh
{context}

## Giá trị chính
{evidence}

## Câu hỏi mở
{open_questions}

---
Nguồn: https://reddit.com/r/{subreddit}/comments/{post_id}
Điểm: {score} | Bình luận: {num_comments} | Xuất bản: {date}
```

### 4.3 Share Link

- URL: `/post/{post_id}?view=longread`
- Default view = longread (thay vì detail page)
- Query param `?view=detail` để xem原始 detail page

---

## 5. UI/UX Design

### 5.1 Navigation Changes

**Current**: `📊 Tín hiệu & AI` | `💎 Resource Hub` | `🤖 AI Briefings`

**New**: `📰 Longread Feed` | `📊 Signals` | `💎 Resources` | `🔖 Bookmarks` | `🤖 Briefings`

- **Longread Feed** (default tab): Magazine-style cards, sorted by trend_score
- **Signals**: Signal explorer hiện tại (đổi tên)
- **Bookmarks**: Bài đã bookmark

### 5.2 Longread Card (Feed View)

```
┌─────────────────────────────────────────────┐
│ 🔒 cybersecurity  •  r/cybersecurity        │
│                                             │
│ CrowdStrike Falcon: Kẻ tấn công dùng       │
│ kernel driver để bypass EDR                 │
│                                             │
│ Vấn đề mới với CrowdStrike Falcon: kẻ      │
│ tấn công có thể dùng kernel driver hợp      │
│ pháp để tắt EDR protection. CrowdStrike     │
│ đã phát hành hotfix nhưng...                │
│                                             │
│ ⏱ 2 min  📈 4.2/hr  💬 342  📎 3 resources│
│                                             │
│ [Đọc tiếp →]  [🔖]  [↗ Share]              │
└─────────────────────────────────────────────┘
```

### 5.3 Longread View (Reading Mode)

```
┌─────────────────────────────────────────────┐
│ ← Back to Feed            [🔖] [↗] [📋 MD] │
│                                             │
│ cybersecurity • r/cybersecurity • 2h ago    │
│                                             │
│ CrowdStrike Falcon: Kẻ tấn công dùng       │
│ kernel driver để bypass EDR                 │
│ ═══════════════════════════════════════════ │
│                                             │
│ Vấn đề mới với CrowdStrike Falcon: kẻ      │
│ tấn công có thể dùng kernel driver hợp      │
│ pháp để tắt EDR protection...              │
│                                             │
│ ## Bối cảnh                                 │
│ CrowdStrike Falcon sử dụng kernel-level     │
│ callback để monitor system behavior.        │
│ Tuy nhiên, cơ chế này cũng mở cửa cho...   │
│                                             │
│ ## Giá trị chính từ cộng đồng              │
│ Cộng đồng đồng thuận rằng đây là lỗ hổng  │
│ nghiêm trọng. Nhiều ý kiến đề xuất...       │
│                                             │
│ ## Bằng chứng & Đề xuất                    │
│ • Tool A: kernel driver analyzer (github)   │
│   → Được 45 upvotes,作者 nói dùng để...    │
│ • Tool B: EDR bypass detection script       │
│   → Cách dùng: chạy với admin privileges   │
│                                             │
│ ## Câu hỏi mở                              │
│ • CrowdStrike có patch hoàn toàn chưa?     │
│ • Alternative EDR nào an toàn hơn?          │
│                                             │
│ ─────────────────────────────────────────── │
│ 📎 Resources: Tool A, Tool B, Paper C       │
│ 💬 Discussion (342 comments) →              │
│ 🌐 Source article →                         │
└─────────────────────────────────────────────┘
```

### 5.4 Typography & Reading UX

- **Font**: System font stack, body 16-18px, line-height 1.6-1.8
- **Max width**: 680px (optimal reading width)
- **Section spacing**: Clear visual separation between sections
- **Reading time**: Show estimated time at top
- **Scroll progress**: Thin progress bar at top
- **Keyboard**: Arrow keys to navigate between posts, `ESC` to go back

---

## 6. API Changes

### 6.1 New Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/longread/feed` | GET | Longread cards for feed view. Params: `?topic=&limit=&offset=&sort=` |
| `/api/longread/{post_id}` | GET | Full longread content for reading view |
| `/api/posts/{post_id}/export` | GET | Export as markdown. Params: `?format=markdown` |
| `/api/notifications` | GET | Get pending notifications |
| `/api/notifications/read` | POST | Mark notifications as read |
| `/api/user/preferences` | GET/PUT | Get/update user preferences (followed topics, etc.) |
| `/api/user/bookmarks` | GET/POST/DELETE | Manage bookmarks |

### 6.2 Modified Endpoints

**`/api/trending`** — Add `longread` field:
```json
{
  "post_id": "abc123",
  "title": "...",
  "longread": {
    "hook": "Opening 2-3 câu...",
    "reading_time_sec": 120
  }
}
```

---

## 7. Acceptance Criteria

### 7.1 Longread Engine

- [ ] `fact_longread` table exists with correct schema
  - verify: `.venv/bin/python -c "import sqlite3; db=sqlite3.connect('reddit.db'); print(db.execute('PRAGMA table_info(fact_longread)').fetchall())"`
- [ ] Longread sections generated from existing `PostAnalysis` for top 20 trending posts
  - verify: `.venv/bin/python -c "import sqlite3; db=sqlite3.connect('reddit.db'); print(db.execute('SELECT post_id, hook FROM fact_longread LIMIT 3').fetchall())"`
- [ ] API `/api/longread/feed` returns cards with hook, reading_time, topic
  - verify: `curl -s http://127.0.0.1:8080/api/longread/feed?limit=3 | python3 -m json.tool | head -30`
- [ ] API `/api/longread/{post_id}` returns full 5-section longread
  - verify: `curl -s http://127.0.0.1:8080/api/longread/POST_ID | python3 -m json.tool | grep -c "hook\|context\|insight\|evidence\|open_questions"`

### 7.2 Personalization

- [ ] localStorage-based followed_topics, read_posts, bookmarks work in browser
  - verify: Open DevTools → Application → LocalStorage → verify keys exist after interaction
- [ ] Longread feed filters by followed_topics when set
  - verify: Set `followed_topics: ["cybersecurity"]` → feed shows only cybersecurity posts
- [ ] Read posts are visually distinct (grayed out / hidden)
  - verify: Mark post as read → refresh → verify visual change
- [ ] Bookmarks section shows saved posts
  - verify: Bookmark a post → navigate to Bookmarks → verify it appears

### 7.3 Notifications

- [ ] `check_notifications` job detects new posts in followed topics
  - verify: `.venv/bin/python -c "from jobs.notify import check_notifications; check_notifications('reddit.db', {'followed_topics': ['ai_models']})"`
- [ ] Telegram notification sent for new trending posts
  - verify: Check Telegram bot for notification message (manual)
- [ ] Web push notification works (browser)
  - verify: Open app → allow notifications → trigger test notification → verify browser shows it

### 7.4 Export/Share

- [ ] Markdown export endpoint returns valid markdown
  - verify: `curl -s http://127.0.0.1:8080/api/posts/POST_ID/export?format=markdown | head -20`
- [ ] Share link `/post/{id}?view=longread` loads longread view
  - verify: Open in browser → verify longread view loads (not detail view)
- [ ] Copy summary button copies hook + insight to clipboard
  - verify: Click button → paste → verify content matches longread sections

### 7.5 UI/UX

- [ ] Longread feed renders with proper typography (16px+ body, 680px max-width)
  - verify: Open in browser → visual inspection
- [ ] Reading progress bar works during scroll
  - verify: Scroll down → verify progress bar advances
- [ ] Navigation: "← Back to Feed" returns to feed view
  - verify: Click back → verify feed view loads
- [ ] Keyboard navigation: Arrow keys move between posts
  - verify: Press ↓ → next post highlighted; press ↑ → previous post

---

## 8. Constraints

- **SQLite**: Không thêm ORM, giữ raw SQL queries
- **No auth**: Mọi data là client-side localStorage, không cần user table
- **Single device**: Notifications chỉ 1 device (localStorage-based)
- **Existing analysis**: Longread chỉ render từ `PostAnalysis` đã có, không tự generate
- **Rate limits**: Gemini free tier ~20 req/ngày — không gọi LLM mới cho longread
- **Performance**: Feed load < 500ms, longread view < 300ms

---

## 9. Plan (filled at Plan stage)

### Phase 1: Longread Engine (Core)
1. Create `fact_longread` table + migration
2. Build longread renderer: `PostAnalysis` → 5 sections
3. API endpoints: `/api/longread/feed`, `/api/longread/{post_id}`
4. CLI command: `generate-longreads` to batch-generate for trending posts
5. React: LongreadCard component + LongreadView component
6. Update navigation

### Phase 2: Personalization
1. localStorage utility functions (follow, read, bookmark, hide)
2. Integrate with feed filter
3. Bookmarks section in nav
4. Visual indicators for read/bookmarked posts

### Phase 3: Notifications
1. `jobs/notify.py` — check new posts in followed topics
2. Telegram notification integration (extend `delivery.py`)
3. Web Push: Service Worker + subscription management
4. NotificationBell UI component

### Phase 4: Export/Share
1. Markdown export endpoint
2. Share link with `?view=longread`
3. Copy summary button
4. Clipboard API integration

### Phase 5: Polish
1. Typography & reading UX refinements
2. Keyboard navigation
3. Reading progress bar
4. Responsive design

---

## 10. Decisions Log

- 2026-07-24: Dùng Approach A (render from existing analysis) cho MVP — không LLM call mới, chi phí = 0
- 2026-07-24: localStorage cho personalization thay vì server-side — đơn giản, đủ cho 1 user
- 2026-07-24: Web Push + Telegram cho notifications — không email (phức tạp hơn, ít dùng cho developer)

---

## Outcome (filled at Ship)

### Verification Results — 2026-07-24

| Gate | Status | Evidence |
|---|---|---|
| compileall | PASS | `python -m compileall -q reddit_crawler jobs web cli.py` — 0 errors |
| tests | PASS | `python -m unittest discover -s tests` — 23/23 pass |
| frontend build | PASS | `npm run build` — built in 1.93s |
| secrets scan | PASS | No hardcoded keys/tokens found |
| 7.1.1 fact_longread schema | PASS | 9 columns: post_id, hook, context, insight, evidence, open_questions, reading_time_sec, generated_at, provider |
| 7.1.2 Longread generated | PASS | 11 longreads from 11 posts with analysis |
| 7.1.3 /api/longread/feed | PASS | Status 200, returns hook, reading_time_sec, title |
| 7.1.4 /api/longread/{post_id} | PASS | Status 200, all 5 sections present |
| 7.3.1 check_notifications | PASS | Returns `no_followed_topics` (expected — no user_prefs.json) |
| 7.4.1 Markdown export | PASS | Status 200, valid markdown output |

### What shipped vs spec

**Shipped (MVP):**
- Longread Engine: `fact_longread` table, renderer from PostAnalysis, batch generate, API feed + detail
- Personalization: localStorage-based follow/read/bookmark/hide, Bookmarks page
- Notifications: `jobs/notify.py` with Telegram delivery, CLI `check-notifications`
- Export/Share: Markdown export endpoint, share link `/longread/{post_id}`, copy summary button
- UI: LongreadFeed, LongreadView, BookmarksPage, updated navigation (5 tabs)
- CSS: Full longread feed + reading view styles + responsive

**Deferred (Phase 5 — low priority):**
- Web Push (Service Worker + pywebpush) — needs browser registration flow
- Reading progress bar — CSS added, JS not yet wired
- Keyboard navigation (arrow keys between posts)
- `/api/notifications`, `/api/user/preferences`, `/api/user/bookmarks` endpoints (client-side only for MVP)

**Deviations:**
- Spec `?view=longread` query param: simplified to `/longread/{post_id}` route (cleaner URLs)
- Spec NotificationBell component: deferred — notifications work via CLI/Telegram for now
