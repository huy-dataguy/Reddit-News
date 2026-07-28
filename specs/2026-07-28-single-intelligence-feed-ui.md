# Spec: Single Intelligence Feed — rebuild web theo kiểu mạng xã hội nội bộ

- Date: 2026-07-28
- Status: approved

## Problem

Web hiện chia ba tab `Hôm nay`, `Kho tri thức`, `Radar`, nhưng cả ba đều hiển thị biến
thể của cùng dữ liệu: title, domain, subreddit, metrics, analysis và link detail. Người
dùng phải đoán nên mở tab nào, cùng một post có thể xuất hiện lặp ở nhiều bề mặt, hero/
digest/grid/list tạo nhiều visual hierarchy nhưng không tạo thêm mental model. Feature
Grounded Social Brief sắp thêm một bề mặt `Bài viết` nữa sẽ làm navigation càng phân
mảnh nếu tiếp tục mô hình mỗi data type là một tab.

Người dùng muốn trải nghiệm giống mạng xã hội: mở một trang, cuộn một feed dọc, đọc
ngay nội dung đáng biết; khi cần mới mở nguồn, analysis hoặc raw signal. Tuy nhiên đây
không phải social network thật: không có account, like/comment nội bộ hoặc engagement
giả. UI cần dùng interaction pattern quen thuộc mà vẫn giữ bản chất intelligence tool.

## Goal

Rebuild dashboard thành **một Single Intelligence Feed** tại `/`. Mỗi story/event chỉ
có một feed item và tự dùng representation tốt nhất hiện có:

```text
Grounded Social Brief ready
          ↓ fallback nếu chưa có
PostAnalysis V2 grounded
          ↓ fallback nếu chưa có
Raw trend signal
```

Feed server-side dedupe/rank/paginate từ current Gold publish. Người dùng đọc full
social brief hoặc compact analysis ngay trong stream, rồi dùng các action có nghĩa:
`Copy`, `Nguồn & kiểm chứng`, `Reddit gốc`, `Mở chi tiết`. Search, period, domain và
content-tier là filter trên cùng feed, không phải top-level pages.

Desktop dùng layout ba cột kiểu social product: navigation/filter rail nhỏ, center feed
đọc thoải mái và right context rail. Mobile là một cột; filters vào chip/drawer. Các
route cũ tiếp tục hoạt động bằng compatibility redirect/filter để bookmark không hỏng.

## Non-goals

- Không xây account/profile, follower, like, comment, share count, notification hay
  activity feed nội bộ. Không hiển thị action giả không có backend semantics.
- Không clone giao diện/branding Facebook, X hoặc Reddit; chỉ dùng pattern vertical
  feed, sticky rails và progressive disclosure.
- Không sửa prompt/social-brief grounding trong spec này; UI tiêu thụ artifact `ready`.
- Không gọi crawl/LLM, materialize hoặc write DB từ web request.
- Không thêm realtime WebSocket; freshness theo current publish và polling health nhẹ.
- Không auto infinite-scroll. Dùng cursor pagination với `Trang trước`/`Trang sau` để
  predictable, accessible, bookmark được và tránh DOM/feed chạy không dừng.
- Không thêm frontend framework/router/state library nếu React + History API hiện tại
  đáp ứng contract; dependency mới cần quyết định riêng có measurement.
- Không xóa Knowledge/raw evidence capability; chúng chuyển thành filter/detail layers.

## Dependency và source authority

Phụ thuộc:

- Medallion-lite WP6: Gold marts/current publish.
- Medallion-lite WP7: `ServingRepository` và API consumer boundary.
- Grounded Social Brief WP4: ready brief trong Gold/Serving.
- Release/ops spec: clean frontend build, browser smoke, immutable release manifest.
- Internal-production program: Wave 4/5 rollout, observation và localhost boundary.

UI phải vẫn hữu dụng trước khi social brief coverage cao: analysis và signal fallback
không phụ thuộc LLM batch hoàn tất. `ServingRepository.feed()` là nơi duy nhất chọn
representation/dedupe; React không fetch ba endpoint rồi tự merge.

## Information architecture

### Desktop ≥1180px

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Reddit Radar   [Search intelligence................]   ● Healthy   [Filter]│
├───────────────┬──────────────────────────────────────┬─────────────────────┤
│ Feed          │  Briefing hôm nay (compact/pinned)  │ Đang nóng           │
│ • Cho bạn     │                                      │ 01 story...         │
│ • Mới nhất    │  ┌────────────────────────────────┐  │ 02 story...         │
│               │  │ Domain · verification · time  │  │ 03 story...         │
│ Nội dung      │  │ Headline                       │  │                     │
│ □ Bài viết    │  │ full brief / analysis / signal│  │ Chủ đề              │
│ □ Phân tích   │  │                                │  │ AI · Dev · Security │
│ □ Tín hiệu    │  │ Copy · Evidence · Source      │  │                     │
│               │  └────────────────────────────────┘  │ Pipeline freshness  │
│ Chu kỳ/domain │  [← Trước]  Trang 2  [Sau →]         │ Bronze→Serving      │
└───────────────┴──────────────────────────────────────┴─────────────────────┘
```

- Shell tối đa 1240–1320px; left rail 210–230px, center 620–720px, right rail
  260–300px. Center giữ chiều dài dòng khoảng 65–85 ký tự.
- Header chỉ có logo, global search, health và mobile filter; không còn ba main tabs.
- Left rail sticky nhưng không scroll-lock; right rail chứa hot stories, domain counts,
  latest publish/freshness. Không lặp nguyên content card ở rail.
- Digest/day summary là compact pinned module đầu feed, dismiss trong session được;
  không dùng hero 330px đẩy nội dung xuống dưới fold.

### Tablet 760–1179px

- Bỏ right rail; hot topics thành compact horizontal module sau pinned summary.
- Left rail còn ở ≥900px; dưới ngưỡng đó chuyển thành filter drawer/chips.
- Center feed tối đa 720px, không giãn card thành magazine grid.

### Mobile 320–759px

```text
[Logo] [Search] [Filter]
[Cho bạn] [Mới] [24h] [AI/ML ...]
────────────────────────
Feed post full width
  headline
  body / read more
  metrics
  Copy · Evidence · Source
────────────────────────
[← Trước]   Trang 2   [Sau →]
```

- Một cột, card sát mép hợp lý, tap target ≥44px, không horizontal page overflow.
- Filter chips có thể cuộn ngang riêng; drawer có label/checkbox/reset/apply rõ.
- Feed footer và detail navigation luôn là button/link thật, không chỉ gesture. Khi đổi
  page, focus tới feed heading và scroll center column lên đầu; browser Back khôi phục
  page, filter, focused card và exact scroll offset trước đó.
- Evidence mở bottom sheet/dialog có focus trap/escape/return focus; fallback inline
  disclosure nếu dialog implementation không đạt accessibility.

## Unified feed contract

### FeedStoryV1

```json
{
  "feed_id": "stable-story-id",
  "publish_id": "current-publish-id",
  "cluster_id": "cluster-or-post-id",
  "primary_post_id": "post-id",
  "content_tier": "brief|analysis|signal",
  "domain_id": "ai_ml",
  "domain_name": "AI & Machine Learning",
  "headline": "string",
  "body": {
    "share_text": "string|null",
    "summary": "string",
    "key_points": ["string"]
  },
  "verification": {
    "status": "verified|partially_verified|source_claim|provisional",
    "label": "string",
    "limitations": ["string"]
  },
  "metrics": {
    "trend_score": 0.0,
    "reddit_score": 0,
    "comment_count": 0,
    "source_count": 0
  },
  "provider": {"name": "gemini", "model": "string", "is_ai": true},
  "source_urls": ["https-url"],
  "created_at": 0.0,
  "updated_at": 0.0,
  "rank_score": 0.0
}
```

Precedence per `cluster_id`: latest `ready` brief matching current publish/input;
otherwise preferred grounded LLM analysis; otherwise signal. Never return three items
for three tiers of the same story. `feed_id` stays stable across tier upgrade so UI
route/history không đổi.

Signal fallback is honest: title + metrics + source link, no generated prose. Analysis
fallback shows verdict/maximum three key points. Brief tier shows rendered social post;
default desktop expands full text up to 360 words, mobile may collapse after a stable
visual length with accessible `Đọc tiếp`, never truncate copied `share_text`.

### Feed API

`GET /api/feed` parameters:

- `mode=for_you|latest`; default `for_you`.
- `tier=all|brief|analysis|signal`; default `all`.
- `period=3h|day|week|month|year`; default `day`.
- `domain=all|<canonical-domain>`.
- `q=<text>` maximum 200 characters.
- `limit=1..30`, default 12.
- `cursor=<opaque>`; cursor binds publish, mode, filters, last rank/time/feed ID.

Response:

```json
{
  "publish_id": "id",
  "generated_at": 0.0,
  "mode": "for_you",
  "filters": {},
  "summary": {},
  "items": [],
  "page_info": {
    "page_number": 2,
    "has_previous": true,
    "has_next": true,
    "previous_cursor": "opaque-or-null",
    "next_cursor": "opaque-or-null",
    "position_start": 13,
    "position_end": 24,
    "total": 1115
  },
  "facets": {"tiers": [], "domains": []},
  "hot": [],
  "pipeline": {}
}
```

Cursor từ publish cũ trả `409 feed_refresh_required`; client hiện banner “Có bản dữ liệu
mới” và chỉ refresh khi người dùng chọn, không prepend/reorder khiến nội dung nhảy giữa
lúc đang đọc. `previous_cursor`/`next_cursor` đều bound cùng publish + normalized filter.
Search/filter đổi thì cursor reset về trang 1. API read-only và chỉ trả `ready` brief
mặc định; audit status không nằm trên main feed.

## Feed ranking và dedupe

`for_you` dùng Gold `rank_score` deterministic từ composite value, freshness và content
quality bonus; social brief/analysis bonus chỉ phá tie hợp lý, không đẩy tin cũ lên đầu
vô hạn. `latest` sort theo event/source time rồi feed ID, không theo generated time.

Pagination dùng seek/cursor theo sort tuple, không `OFFSET` cho page sâu. `page_number`
và positions là navigation metadata trong current publish; browser URL giữ cursor/page
để copy/reload nội bộ được. Total lấy từ metadata/facet đã materialize, không chạy một
count/full scan nặng trên mỗi request.

Dedupe keys theo StoryCluster khi có; fallback canonical external URL/crosspost/post ID.
Một cluster có nhiều subreddit gộp source count/metrics nhưng primary post vẫn truy
được. Right rail hot dùng cùng feed IDs và click scroll/navigate tới item, không tạo một
representation thứ hai.

## Component architecture

Không tiếp tục dồn mọi thứ vào `App.jsx`. Target structure:

```text
web/frontend/src/
  App.jsx                    # route composition only
  router.js                  # parse/compat navigation
  api.js
  pages/
    FeedPage.jsx
    StoryDetailPage.jsx
  components/
    layout/AppHeader.jsx
    layout/LeftRail.jsx
    layout/RightRail.jsx
    layout/FilterDrawer.jsx
    feed/FeedList.jsx
    feed/FeedPost.jsx
    feed/BriefBody.jsx
    feed/AnalysisBody.jsx
    feed/SignalBody.jsx
    feed/FeedActions.jsx
    feed/FeedPagination.jsx
    evidence/EvidenceDrawer.jsx
    common/ProviderBadge.jsx
    common/VerificationBadge.jsx
    common/AsyncState.jsx
    common/ScrollToTop.jsx
  styles/
    tokens.css layout.css feed.css detail.css responsive.css
```

`App.jsx` không chứa data fetching/business mapping. `FeedPost` dispatch body theo
`content_tier`; shared header/metrics/actions chỉ có một implementation. API response
được render trực tiếp theo contract, không có `analysisView()` thứ hai ở client.

## Routes và compatibility

- `/` và `/feed`: main feed; canonical URL `/`.
- `/story/:feed_id`: unified story detail/evidence.
- `/post/:post_id`: compatibility detail route, resolve sang story khi mapping tồn tại.
- `/brief/:brief_id`: compatibility resolve sang story brief.
- `/briefs` → `/?tier=brief`.
- `/knowledge` → `/?tier=analysis`.
- `/radar` và `/signals` → `/?tier=signal`.

Redirect dùng `replaceState`, giữ query period/domain khi có thể. Direct refresh mọi
route trả SPA index từ FastAPI. Back button trở về đúng filter/scroll intent; feed state
được giữ trong history state/session, không cần global store.

Story detail nhận feed navigation context trong history/session và hiển thị
`← Bài trước` / `Bài tiếp theo →`. Nếu mở direct URL không có context, API trả adjacent
IDs theo cùng current publish/default feed; nếu publish đã đổi thì detail vẫn mở story
được nhưng navigation báo cần quay về feed mới, không đoán cursor sai.

## Interaction và visual rules

- Mỗi feed post có một header: domain icon/name, verification, source/time; không fake
  avatar/person profile nếu story không có publisher identity.
- Headline 22–28px desktop, 19–23px mobile; body 15–17px, line-height 1.6–1.75.
- Actions: `Copy bài` chỉ tier brief; `Nguồn & kiểm chứng`; `Reddit gốc`; `Chi tiết`.
  Reddit score/comment hiển thị như source metrics, không biến thành nút like/comment.
- Verification badge luôn có icon + text. `source_claim/inference/provisional` có
  explanation accessible, không chỉ dựa màu.
- Pinned summary, feed card và rails dùng cùng spacing/radius/token; giảm hero, gradient,
  card-grid variants. Một dominant center column, không magazine dashboard.
- Loading skeleton giữ layout; first error có retry; pagination error giữ page cũ;
  empty state giải thích filter và có `Xóa bộ lọc`.
- Feed footer có `Trang trước`, `Trang sau`, `Trang N`, range `13–24 / total`; disabled
  state dùng native `disabled` + text/ARIA, không chỉ giảm opacity.
- Click `Trang sau/Trang trước` cập nhật URL/history, thay page thay vì append, scroll
  feed lên đầu và focus feed heading. Browser Back/Forward khôi phục exact page + scroll
  + focused card, không luôn ép lên top.
- Story detail có previous/next sticky footer trên mobile và inline controls desktop;
  navigation giữ cùng filters/mode và scroll detail lên đầu.
- Floating `Lên đầu trang` chỉ xuất hiện sau khi cuộn quá 600px; click/keyboard focus
  về feed heading. Smooth scroll chỉ dùng khi user không bật `prefers-reduced-motion`.
- Header/filter rail không chặn native PageUp/PageDown/Home/End. Không dùng scroll snap,
  wheel hijacking hoặc tự cuộn theo card.
- Khi right-rail hot item trỏ tới story đang có trên page, scroll/focus tới card và
  highlight ngắn; nếu ở page khác thì navigate bằng server-provided cursor.
- Copy toast, evidence close và filter apply không thay đổi scroll. Mở detail ghi
  `returnFocusFeedId`; quay lại focus đúng action/card đã mở.
- Khi current publish đổi trong lúc đọc, hiện non-blocking refresh banner; không tự
  reorder, reset cursor hoặc mất vị trí. User chọn refresh mới về page 1/publish mới.
- Global search debounce 250ms, abort request cũ; URL phản ánh filters/page để reload/share
  nội bộ được. Không search toàn JSON ở browser.

## Performance và accessibility budgets

- Mỗi page 12 items; thay page thay DOM list thay vì append vô hạn, đồng thời giữ page
  cache gần nhất tối đa 3 page trong session để Back/Forward tức thời.
- Production JS gzip target ≤100 KiB, CSS gzip ≤25 KiB; tăng budget cần measurement và
  decision log. Current build khoảng 71 KiB JS gzip/5 KiB CSS gzip là baseline.
- Feed API first page target p95 ≤300ms trên current production DB copy, warm OS cache;
  query plan không full scan unbounded payload/evidence JSON.
- Không layout shift lớn từ media; ảnh optional có explicit aspect ratio/lazy loading.
- Keyboard usable cho search/filter/load/card actions/drawer; visible focus; Escape và
  return focus; landmark/header/nav/main/aside labels; reduced-motion respected.
- 390px không horizontal page overflow; 200% zoom vẫn đọc/action được.

## Acceptance criteria

- [ ] AC1 — `ServingRepository.feed()` tạo `FeedStoryV1`, precedence brief→analysis→signal đúng và một cluster/feed ID chỉ xuất hiện một lần — verify: `.venv/bin/python -m unittest tests.test_feed_contract tests.test_feed_dedupe`
- [ ] AC2 — `/api/feed` mode/tier/period/domain/search/limit/cursor/facets/page_info đúng, previous/next cursor seek ổn định, không trộn publish và invalid/stale cursor fail rõ — verify: `.venv/bin/python -m unittest tests.test_feed_api tests.test_feed_pagination tests.test_web_api`
- [ ] AC3 — Feed/detail API read-only, không crawl/LLM/write, brief quality fail giữ fallback analysis/signal và last-known-good publish vẫn phục vụ — verify: `.venv/bin/python -m unittest tests.test_feed_readonly tests.test_publish`
- [ ] AC4 — Routes `/briefs`, `/knowledge`, `/radar`, `/signals`, `/post/:id`, `/brief/:id` tương thích và resolve/redirect không làm mất period/domain/back navigation — verify: `.venv/bin/python -m unittest tests.test_feed_routes tests.test_web_api`
- [ ] AC5 — Header không còn ba main tabs; `/` render một center feed với left/right context rails desktop, một cột mobile và không còn TodayPage/KnowledgePage/RadarPage độc lập — verify: `! rg -n "function (TodayPage|KnowledgePage|RadarPage)|const NAV_ITEMS" web/frontend/src && npm --prefix web/frontend run build`
- [ ] AC6 — Một shared `FeedPost` render đúng brief/analysis/signal bodies, action semantics không giả like/comment và copy chỉ xuất hiện cho brief ready — verify: `npm --prefix web/frontend run build && .venv/bin/python -m unittest tests.test_feed_contract`
- [ ] AC7 — Search/filter dùng server query, debounce/cancel/reset về trang 1; previous/next thay page ổn định, pagination error giữ page cũ và empty state xóa filter được — verify-manual: Dataguy: thao tác search, đổi mode/tier/period/domain liên tục, đi Sau→Sau→Trước, mô phỏng API pagination lỗi và xác nhận URL/history/page/range/list state đúng
- [ ] AC8 — Evidence drawer/detail map claim→source, phân biệt verification bằng icon+text, focus trap/Escape/return focus và không expose raw provider payload — verify: `.venv/bin/python -m unittest tests.test_feed_detail tests.test_feed_api`
- [ ] AC9 — Desktop 1440/1024 và mobile 390/320 không overflow, center line length/readability đúng, rails collapse đúng, 200% zoom/keyboard/reduced-motion usable — verify-manual: Dataguy: chụp `/` và `/story/:id` ở 1440, 1024, 390, 320px, test 200% zoom + keyboard + reduced motion và lưu screenshots `/tmp/reddit-radar-feed-*`
- [ ] AC10 — Loading skeleton, first/pagination error, no-brief fallback, empty DB/filter và degraded pipeline states đều có truthful UI, không màn hình trắng — verify: `.venv/bin/python -m unittest tests.test_feed_api && npm --prefix web/frontend run build`
- [ ] AC11 — Frontend được tách pages/layout/feed/evidence/common, `App.jsx` chỉ compose route/shell và không chứa direct fetch/business mapper — verify: `test -f web/frontend/src/pages/FeedPage.jsx && test -f web/frontend/src/components/feed/FeedPost.jsx && test -f web/frontend/src/components/evidence/EvidenceDrawer.jsx && ! rg -n "fetch\(|getJSON\(|analysisView|trending_posts" web/frontend/src/App.jsx`
- [ ] AC12 — Production bundle JS gzip ≤100 KiB, CSS gzip ≤25 KiB và feed first-page p95 ≤300ms trên DB copy theo benchmark script — verify: `npm --prefix web/frontend run build && .venv/bin/python scripts/benchmark_feed.py --db reddit.db --runs 30 --p95-ms 300 --read-only && js=$(find web/dist/assets -name '*.js' -print -quit) && css=$(find web/dist/assets -name '*.css' -print -quit) && test $(gzip -c "$js" | wc -c) -le 102400 && test $(gzip -c "$css" | wc -c) -le 25600`
- [ ] AC13 — Full backend/frontend gate xanh và không weaken API/social-brief/serving tests — verify: `.venv/bin/python -m compileall -q reddit_crawler jobs web cli.py && .venv/bin/python -m unittest discover -s tests && npm --prefix web/frontend run build`
- [ ] AC14 — Live internal rollout phục vụ feed từ current publish, five representative stories render đúng tier/evidence/copy, old routes resolve, health healthy và rollback về old dist/API route được — verify-manual: Dataguy: sau backup + release candidate, deploy API/dist trong maintenance window, smoke `/api/feed`, `/`, 5 story tiers, old routes và mobile; thực hành rollback artifact/service rồi redeploy new artifact, quan sát 24 giờ
- [ ] AC15 — Feed/detail navigation và scroll UX đúng: Trước/Sau, bài trước/tiếp, Back/Forward restore page+offset+focus, lên đầu trang, right-rail jump và new-publish banner không tự làm nhảy nội dung — verify-manual: Dataguy: từ page 1 cuộn giữa card, đi page 2→detail→bài tiếp→Back hai lần, dùng browser Forward, right-rail jump và nút lên đầu; giả lập publish mới rồi xác nhận chỉ refresh sau click, test cả mouse/keyboard/reduced-motion ở 1440px và 390px

## Risk tier

Tier: R3 — thay đổi API/Serving consumer, toàn bộ information architecture và deploy
web production nội bộ; build reversible nhưng live rollout/rollback cần Dataguy duyệt.

## Constraints

- Không bắt đầu Serving/API implementation trước Medallion interfaces ổn định; UI shell
  có thể prototype bằng fixture nhưng không tạo client-side merge contract tạm thời.
- Giữ localhost/internal-only; không thêm auth/public infrastructure.
- Không DDL trong spec UI ngoài mart/feed contract đã thuộc dependency specs.
- FastAPI và web request luôn read-only; không thêm generate/retry/provider endpoint.
- Old routes tồn tại ít nhất một release cycle; removal cần usage evidence/spec riêng.
- Feed default phải hữu dụng khi social brief coverage thấp hoặc provider quota hết.
- Không hiển thị `needs_review/rejected` trên main feed; audit UI ngoài scope.
- Không fake author/avatar/engagement hoặc gọi Reddit metrics là lượt tương tác nội bộ.
- Không thêm dependency/UI kit/router nếu implementation native đáp ứng budgets.
- Giữ Vietnamese-first; source titles/technical terms không bị dịch sai.
- Không sửa social brief content trong frontend; copy exact server-rendered `share_text`.

## Stop if (only if a run could plausibly overreach)

- Cần fetch/merge `/api/today`, `/api/knowledge`, `/api/trending`, `/api/briefs` ở React
  để dựng feed thay vì một server contract.
- Dedupe làm mất source story hoặc gộp hai cluster khác nhau trên fixture/live copy.
- Feed requires a write/generate HTTP endpoint hoặc cannot serve without current LLM.
- Bundle vượt budget và nguyên nhân là dependency mới chưa được phê duyệt/đo lường.
- API p95 vượt 300ms do unbounded JSON/full-table scan và chưa có query-plan fix.
- Live old route, Evidence/Reddit source hoặc copy semantics bị hỏng.
- Disk ≥95%, current publish unhealthy hoặc dependency spec/session đang đổi cùng file.

## Interfaces (only if criteria depend on each other)

- AC1 produces `FeedStoryV1` exact schema và `ServingRepository.feed(mode, tier, period, domain, query, limit, cursor) -> FeedPage`.
- AC2 produces `FeedPage.page_info` với previous/next opaque cursors bound to `publish_id + normalized filters + sort tuple`; stale publish returns typed refresh error.
- AC3 produces `ServingRepository.story(feed_id) -> FeedStoryDetail` với evidence map, sources và tier-specific content.
- AC5 produces canonical route `/`; compatibility routes map to feed filters/story IDs through `router.js`.
- AC6 produces `FeedPost({story})` + `BriefBody|AnalysisBody|SignalBody` và shared `FeedActions`.
- AC8 produces `EvidenceDrawer({storyId, open, onClose, returnFocusRef})`; detail fetched only when opened.
- AC15 produces `FeedNavigationState`: `{publishId, filters, pageNumber, cursor, scrollY, focusedFeedId, cursorTrail}` persisted trong history/session và bounded tối đa ba cached pages.

## Plan (filled at Plan stage)

### Work package 0 — Baseline, UX contract và fixtures

- Capture screenshots/current route/API/bundle baseline at 1440/390; inventory duplicate
  fields/components and navigation paths without editing runtime.
- Produce sanitized fixture page with brief, analysis, signal, partially verified,
  no-source, degraded and empty cases; freeze FeedStoryV1/API golden response.
- Record current `/`, `/knowledge`, `/radar`, `/post/:id` behavior and direct bookmarks.
- Write `docs/web-single-feed.md` with wireframes, responsive transitions, component/
  route ownership and copy/action semantics.
- Files: docs, fixtures, contract tests only; no behavior test left red.
- Fast check: fixture validation, existing web tests/build.

Exit gate: one canonical IA/contract, old behavior baseline and no unresolved content tier.

### Work package 1 — Server feed repository và API

- Extend Gold/Serving query with deterministic precedence/dedupe/ranking/facets; extract
  common story/detail mapping, do not duplicate analysis preference logic.
- Implement opaque cursor validation/current-publish behavior and bounded search.
- Add `/api/feed` and `/api/feed/{feed_id}` or `/api/story/{feed_id}` consistent with
  interface decision; keep legacy endpoints/routes during migration.
- Add query indexes only through Medallion schema/copy migration, not ad-hoc live DDL.
- Files: `reddit_crawler/serving.py`, marts/query modules, `web/app.py`, tests AC1–AC4.
- Fast check: feed contract/API/read-only/route tests, existing API suite.

Exit gate: fixture + production DB copy query parity, p95 budget, no write/network path.

### Work package 2 — New shell/router/component foundation

- Create folder structure; move shared Provider/Domain/Async primitives without visual
  rewrite first; keep each increment build-green.
- Replace three-tab header with logo/search/health/filter; implement canonical router,
  compatibility resolution and history/filter serialization.
- Build responsive `FeedLayout`, rails/drawer and skeleton with fixture data adapter;
  API wiring remains in FeedPage hook, not App.
- Files: router, App, layout/common components, split CSS tokens/layout/responsive.
- Fast check: Vite build, route tests, old detail still reachable.

Exit gate: shell renders all breakpoints, no three-tab navigation, full suite green.

### Work package 3 — Feed post tiers và progressive disclosure

- Implement shared FeedPost header/metrics/actions and tier bodies; full brief desktop,
  accessible collapse mobile, analysis max three points, signal honest fallback.
- Implement exact copy/client announcement, source links and no fake engagement actions.
- Add pinned compact digest and hot/facet right rail from same response feed IDs.
- Files: feed components/styles, tests/fixtures AC5/AC6/AC10.
- Fast check: build, contract tests, manual representative tier check.

Exit gate: one story one card; tier upgrade does not change feed identity/action semantics.

### Work package 4 — Search, filters, cursor và state recovery

- Wire server-side query, debounce/AbortController, URL/history/session page/scroll/focus
  state, previous/next cursors và non-blocking stale-publish refresh banner.
- Implement filter drawer/chips/reset, first/pagination error separation and empty states.
- Implement feed pagination footer/range, scroll-to-heading on explicit page navigation,
  exact restoration on browser Back/Forward and three-page bounded session cache.
- Right rail story click navigates/scrolls without duplicating data; mobile hot module.
- Files: FeedPage/hooks/router/layout/pagination/scroll components, API helper,
  tests/manual AC7/AC10/AC15.
- Fast check: build, API tests, manual rapid-filter/load/error/back checks.

Exit gate: no stale response race, duplicate page item or lost filters/back intent.

### Work package 5 — Story detail và evidence experience

- Build unified story detail around same FeedStory identity; lazy-load evidence drawer/
  detail, map claims to excerpts, sources and limitations.
- Add story previous/next within originating feed context, direct-route adjacent fallback,
  return-focus identity and scroll-to-top respecting reduced motion.
- Resolve old post/brief routes, keep original Reddit/article links and raw discussion
  evidence behind progressive disclosure.
- Implement dialog/bottom-sheet accessibility; no provider raw payload.
- Files: detail page/evidence/navigation/scroll components/styles/router, server detail
  mapper, AC4/AC8/AC15.
- Fast check: detail/route/API tests, keyboard dialog manual test.

Exit gate: every visible factual level has a path to evidence/source; old bookmarks work.

### Work package 6 — Responsive, accessibility và performance hardening

- Test 1440/1024/760/390/320, 200% zoom, keyboard, screen-reader labels/reduced motion;
  fix layout without hiding essential actions/evidence.
- Measure bundle and feed benchmark/query plan; remove duplicated old page code/CSS only
  after compatibility routes use new components and full tests pass.
- Lazy-load optional detail/evidence code only if measurement improves budget without UX
  regressions; optimize images with aspect ratio/lazy loading.
- Files: components/styles/build/benchmark script/tests/docs.
- Fast check: AC9/AC11/AC12 plus full frontend/backend gate.

Exit gate: budgets pass, dead three-page components removed, no weakened tests.

### Work package 7 — Release candidate, rollout và rollback

- Build hashed dist in staging/temp; run AC1–AC13 against production DB copy/current
  publish, API golden parity and five tier examples.
- Prepare artifact backup/current dist checksum, service config, exact smoke URLs and
  rollback commands; no dependency upgrade in rollout.
- Dataguy approves AC14. Deploy API then dist compatibly, restart once, smoke API/feed/
  story/old routes/copy/evidence/mobile/health.
- Rollback drill old dist/API artifact; redeploy new; observe errors, p95, freshness,
  previous/next cursor, scroll restoration, publish-refresh banner và user workflow 24 hours.
- Update README/docs/spec Outcome and preserve legacy endpoints for one release cycle.

Rollback triggers: feed empty/mixed publish, missing stories, old route 404, evidence/
copy wrong, p95 sustained >500ms, mobile unusable, health degraded by feed query or two
consecutive production smoke failures.

### Dependency order

```text
Medallion WP6–WP7 + Social Brief WP4
              ↓
UI WP0 → WP1 → WP2 → WP3 → WP4 → WP5 → WP6 → WP7
```

Wireframe/fixture/component shell may start before full brief coverage, nhưng server
contract remains dependency authority. Mỗi package thêm test cùng behavior, full suite
xanh và record decision trước package kế; live rollout không song song code edits.

## Decisions log (append during Build)

- 2026-07-28 — Product owner approved this authoritative consumer spec for execution
  after `FeedStoryV1` and ServingRepository dependencies pass. Live cutover remains a
  manual R3 gate.
- 2026-07-28 — Một story chỉ có một feed item; ready brief > grounded analysis > raw
  signal. Data completeness nâng cấp card, không tạo thêm tab/item.
- 2026-07-28 — Dùng social-feed interaction model nhưng không giả profile/like/comment;
  actions tập trung copy, evidence, original source và detail.
- 2026-07-28 — Feed được compose server-side trong ServingRepository để dedupe/rank/
  cursor bám current publish; React không merge các API legacy.
- 2026-07-28 — Bỏ giant hero và magazine grids; center column là visual priority, rails
  chỉ chứa context/filter/freshness.
- 2026-07-28 — Cursor pagination với previous/next thay infinite scroll/load-more để
  URL/bookmark/Back-Forward/scroll restoration rõ ràng và vận hành dễ đo.
- 2026-07-28 — Không auto-scroll hoặc reorder khi publish mới; banner cho user chủ động
  refresh. Explicit page navigation lên feed heading, browser history khôi phục offset.
- 2026-07-28 — Spec độc lập với data/content specs; status `approved` cho phép build
  theo dependency nhưng không cấp quyền deploy R3 hoặc tự động sửa live web.

## Outcome (filled at Ship)

Chưa build. IA, feed/route/API contracts, responsive wireframes, component split,
accessibility/performance budgets, acceptance criteria, rollout và rollback plan đã đầy
đủ và scope đã approved; execution chờ ServingRepository, `FeedStoryV1` và brief
dependencies đạt contract gate.
