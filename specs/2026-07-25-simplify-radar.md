# Spec: Simplify Reddit Radar

- Date: 2026-07-25
- Status: blocked

> 2026-07-28: Không được auto-build. Hướng xóa enrich/digest và UI hai tab đã bị
> product direction mới thay thế bằng medallion-lite, Grounded Social Briefs và
> Single Intelligence Feed. WP0 của `2026-07-28-internal-production-program.md`
> sẽ audit phần nào từng ship rồi đóng spec này bằng outcome trung thực.

## Problem

Reddit Radar đã phát triển quá nhiều features (entities, narratives, topic synthesis, longread, notifications, AI digest, ...) trong khi giá trị cốt lõi chỉ là: **crawl Reddit → tính trend → LLM phân tích → hiển thị kết quả**. Kết quả là:

- **20+ CLI commands** khó nhớ, nhiều cái ít dùng
- **18+ DB tables** bao gồm nhiều bảng rỗng hoặc thừa
- **~1500 dòng React** trong 1 file App.jsx, 6+ tabs UI phức tạp
- **Nhiều module backend** (entities, narrative, topics, longread) ít giá trị thực tế
- **Prompt phức tạp** với nhiều Pydantic model thừa, output schema phình to

## Goal

Giảm gọn Reddit Radar thành hệ thống **"crawl → trend → LLM analyze → show"** — đúng chức năng, đủ dùng. Dashboard siêu đơn giản: 2 tab, LLM phân tích cho biết ra đáp án là xong.

### Cụ thể sau khi simplify:

- **Backend**: 8 CLI commands thay vì 20+
- **DB**: Bỏ ~7 tables thừa, giữ core star schema + ai_post_analysis + fact_extracted_resource
- **Frontend**: 2 tab đơn giản (Radar Signals + Knowledge Hub), ~600 dòng thay vì 1500
- **Prompt**: 1 prompt chính cho post analysis, gọn hơn, output schema đơn giản hơn
- **Module**: Bỏ entities.py, narrative.py, topics.py, longread.py + các jobs thừa

## Non-goals

- KHÔNG thay đổi crawl logic (client, auth, incremental, backfill)
- KHÔNG thay đổi schema star schema cơ bản (dim_subreddit, dim_author, fact_post, fact_comment, fact_post_metrics)
- KHÔNG thêm features mới — chỉ giữ lại features đã chọn
- KHÔNG thay đổi LLM providers (giữ Gemini + OpenAI + local fallback)
- KHÔNG thay đổi story-export (giữ nguyên cho MediaWorkflow)

## Acceptance criteria

### Backend cleanup

- [ ] Bỏ CLI commands: `enrich`, `ai-digest`, `rebuild-media`, `extract-resources`, `analyze-top`, `generate-longreads`, `check-notifications`, `extract-entities`, `synthesize`, `topic-insight`, `report` — verify: `python cli.py --help` không còn các lệnh trên
- [ ] Bỏ modules: `entities.py`, `narrative.py`, `topics.py`, `longread.py`, `jobs/enrich_articles.py`, `jobs/enrich_comments.py`, `jobs/rebuild_media.py`, `jobs/extract_resources.py`, `jobs/notify.py`, `jobs/delivery.py`, `jobs/report.py` — verify: `ls reddit_crawler/{entities,narrative,topics,longread}.py` trả error
- [ ] CLI giữ lại: `probe`, `find-subs`, `crawl-sub`, `crawl-post`, `crawl-user`, `incremental`, `backfill`, `serve`, `analyze-post`, `story-export`, `stats` — verify: `python cli.py --help` hiện đủ 11 lệnh trên
- [ ] `python -m compileall -q reddit_crawler jobs cli.py` pass — verify: exit code 0

### Database simplification

- [ ] Bỏ tables: `dim_entity`, `fact_entity_mention`, `fact_entity_relation`, `dim_narrative`, `fact_narrative_post`, `fact_topic_synthesis`, `user_profile`, `fact_longread` — verify: các table này không còn trong schema.sql mới
- [ ] Bỏ index thừa: `ix_entity_*`, `ix_narrative_*`, `ix_topic_*`, `ix_user_profile` — verify: không còn trong schema.sql
- [ ] Bảng `ai_digest` vẫn giữ (AI Briefing cho dashboard) — verify: `ai_digest` còn trong schema.sql
- [ ] DB migrations chạy được trên copy của reddit.db thật — verify: `cp reddit.db /tmp/test.db && python -c "from reddit_crawler.storage import Storage; Storage('/tmp/test.db')"`

### API simplification

- [ ] Bỏ API endpoints: `/api/entities*`, `/api/narratives*`, `/api/topics*`, `/api/longread*`, `/api/notifications*`, `/api/push/*`, `/api/user/*` — verify: `curl localhost:8080/openapi.json | jq '.paths | keys'` không còn các path trên
- [ ] Giữ API: `/api/health`, `/api/stats`, `/api/trending`, `/api/digests/latest`, `/api/knowledge/feed`, `/api/knowledge/{post_id}`, `/api/knowledge/generate/{post_id}`, `/api/posts/{post_id}`, `/api/resources`, `/api/posts/{post_id}/export`
- [ ] Bỏ `PREFS_PATH`, `PUSH_SUB_PATH`, `NOTIF_STATE_PATH` khỏi app.py — verify: không còn reference

### Prompt simplification

- [ ] Post analysis prompt (POST_ANALYSIS_INSTRUCTIONS) giữ nguyên core价值, nhưng simplify PostAnalysis Pydantic model: bỏ `claimed_results`, giữ `opinion_groups`, `suggestions`, `learning_points`, `disagreements`, `unanswered_questions` — verify: `python -c "from reddit_crawler.llm import PostAnalysis; print(PostAnalysis.model_fields.keys())"`
- [ ] Digest models giữ nguyên (SourceRef, KeyNumber, ModelUpdate, ComparisonRow, ModelComparison, Story, DigestContent) — verify: `python -c "from reddit_crawler.llm import DigestContent; print('ok')"`
- [ ] Local fallback functions (`local_post_analysis`, `local_digest`) vẫn hoạt động — verify: `python -c "from reddit_crawler.llm import local_post_analysis, local_digest; print('ok')"`

### Frontend simplification

- [ ] Dashboard còn 2 tab: "Radar Signals" (trending + domain filter + signal cards) và "Knowledge Hub" (post analysis feed + detail modal) — verify:打开 browser, thấy 2 tab chính
- [ ] Bỏ tabs: Resources, Entities, Narratives, Bookmarks — verify: không còn nav button
- [ ] Bỏ components: `EntitiesHub`, `NarrativesHub`, `TopicsHub`, `LongreadFeed`, `LongreadView`, `BookmarksPage`, `NotificationBell`, `usePushSubscription`, `usePersonalization` — verify: `grep -c "EntitiesHub\|NarrativesHub\|TopicsHub\|LongreadFeed\|LongreadView\|BookmarksPage" web/frontend/src/App.jsx` trả 0
- [ ] Bỏ push notification, service worker registration — verify: không còn `registerServiceWorker`, `usePushSubscription`
- [ ] Hero section giữ nguyên (stats + live indicator)
- [ ] Knowledge Hub giữ: domain filter pills + knowledge cards + detail modal (author_goal, community_consensus, learning_points, suggestions) — verify: mở Knowledge Hub thấy cards
- [ ] Signal Explorer giữ: period switch + domain sidebar + signal cards + pulse rail (trending) — verify: mở Signal Explorer thấy signal list
- [ ] Frontend build pass: `cd web/frontend && npx vite build` — verify: exit code 0

### Tests

- [ ] `python -m unittest discover -s tests` pass — verify: exit code 0 (hoặc skip nếu test cũ reference module đã bỏ)

## Constraints

- `reddit.db` là dữ liệu sản xuất — KHÔNG migrate tại chỗ, làm việc trên copy
- `.env` chứa API key — KHÔNG in log/commit
- Story export phải giữ nguyên cho MediaWorkflow pipeline
- LLM fallback chain giữ nguyên: Gemini → OpenAI → local

## Plan (filled at Plan stage)

### Phase 1: Backend cleanup (xoá modules thừa)
1. Xoá file: entities.py, narrative.py, topics.py, longread.py
2. Xoá jobs: enrich_articles.py, enrich_comments.py, rebuild_media.py, extract_resources.py, notify.py, delivery.py, report.py
3. Cập nhật cli.py: bỏ 11 commands, giữ 11 commands
4. Cập nhật web/app.py: bỏ endpoints thừa, bỏ imports thừa
5. Cập nhật schema.sql: bỏ 8 tables + indexes thừa

### Phase 2: Prompt simplification
1. Review PostAnalysis model — bỏ `claimed_results`
2. Review POST_ANALYSIS_INSTRUCTIONS — giữ core, sửa nếu cần
3. Review local_post_analysis — đảm bảo hoạt động với model mới

### Phase 3: Frontend simplification
1. Xoá components thừa khỏi App.jsx
2. Đơn giản hoá navigation (2 tabs)
3. Đơn giản hoá Header (bỏ notification bell, push)
4. Review Knowledge Hub UI — giữ clean
5. Review Signal Explorer UI — giữ clean
6. Build & test

### Phase 4: Verify
1. Chạy lint (compileall)
2. Chạy test
3. Chạy server, mở browser test manual
4. Chạy analyze-post test

## Decisions log

- 2026-07-25 — Keep: crawl, trending, AI analysis, dashboard, story export, resource extraction. Remove: entities, narratives, topics, longread, notifications, digest briefing, report delivery.
- 2026-07-25 — UI: 2 tabs siêu đơn giản (Radar Signals + Knowledge Hub).
- 2026-07-25 — LLM: giữ nguyên Gemini + OpenAI + local fallback chain.

## Outcome (filled at Ship)

TBD.
