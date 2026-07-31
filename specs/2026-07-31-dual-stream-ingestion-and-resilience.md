# 2026-07-31 — Dual-Stream Ingestion & Boot Resilience Specification

## 1. Context & Goal
System operation on non-24/7 personal machines requires:
1. Resilient automation when the PC is booted intermittently.
2. Comprehensive signal coverage: capturing brand new posts (`/new` within 24 hours) AND viral long-tail discussions (`/hot` up to 30 days old).
3. Fair & quota-optimized LLM analysis that processes both fresh signals and older valuable discussions without starvation or token waste.
4. Polished, compact user interface with full state persistence (bookmarks, read status, pagination offset).

---

## 2. Ingestion & Crawling Strategy

### 2.1 Dual-Stream Listing Ingestion (`/new` + `/hot`)
- **Stream A: `/new` (Incremental - 24 Hours Window)**
  - Collects newly submitted posts from configured subreddits.
  - Dumps posts to Bronze layer and updates `crawl_state` high-water mark.
  - Window bounded to 24h to avoid wasting requests on stale zero-engagement posts.
- **Stream B: `/hot` (Viral & Long-Tail Signals - 30 Days Window)**
  - Periodically fetches `/hot` listing across subreddits.
  - Captures posts that gained sudden traction or high comment volume (even if created 2–30 days ago).
  - Upserts posts into `fact_post` and updates `num_comments` & `score`.

### 2.2 Stale Comment Detection & Re-enrichment
- Automatic comment re-crawling (`stale-comments`) triggers when:
  $$\Delta \text{comments} \ge 10 \quad \text{AND} \quad \text{Reddit comments} \ge 3 \times \text{DB comments}$$
- Allows old posts that blew up with new discussions to be fully re-enriched and re-analyzed by Gemini V2.

---

## 3. LLM Workload & Scheduler Specification

### 3.1 Fairness Scheduler (60/40 Ratio)
- Batch candidates split into:
  - **Recent Pool ($\le 24\text{h}$)**: 60% quota (e.g. 3 of 5 slots).
  - **Backlog Pool ($> 24\text{h}$)**: 40% quota (e.g. 2 of 5 slots).
- Prevents starvation of older high-value posts when a flood of new posts arrives.

### 3.2 Quota-Driven Continuous Batching (`limit=500`)
- Batch size expanded up to 500 articles per run.
- **Stop-on-Quota**: On encountering `rate_limited` or `quota_exhausted` (HTTP 429 / 403), the job immediately halts and gracefully releases all unclaimed candidate locks back to `pending`.

---

## 4. Boot & Network Resilience Specification

### 4.1 Systemd Timer Catch-Up
- All Systemd `.timer` files include `Persistent=true` and `OnBootSec=3min`.
- Ensures missed scheduled jobs during PC power-off run automatically 3 minutes after boot (allowing Wi-Fi / DNS to stabilize).

### 4.2 Network Retry Backoff in LLM Pipeline
- Python LLM client (`reddit_crawler/llm.py`) wraps API requests with exponential backoff (4 retries with 6s sleep).
- Prevents `APIConnectionError` (`Temporary failure in name resolution`) during boot-up DNS setup.

---

## 5. UI Layout & User Interaction Persistence

### 5.1 Compact Header Layout
- Compact `.page-intro` header ($\le 30\text{px}$ title) with inline search bar to maximize vertical screen space.
- Single Feed UI with Smart Control Toolbar (Noise Filter $>2$ comments, Hide Read toggle, Composite Value Ranking).

### 5.2 User State Persistence
- Bookmark (`user_bookmark`) and Read status (`user_read_state`) stored in SQLite Star Schema.
- Page offset (`rr_k_offset`) preserved in `sessionStorage` so clicking "Back" from post details returns to the exact page offset (e.g., Page 40).
