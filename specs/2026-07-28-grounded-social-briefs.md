# Spec: Grounded Social Briefs — bài social công nghệ từ dữ liệu Radar

- Date: 2026-07-28
- Status: approved

## Problem

Dashboard hiện có signal, digest và PostAnalysis giàu bằng chứng nhưng vẫn mang hình
thức phân tích: người đọc phải mở nhiều card/section để tự ghép “chuyện gì xảy ra, con
số nào quan trọng, vì sao đáng quan tâm và ảnh hưởng tới mình”. Người dùng muốn một
bề mặt nội dung giống các post Facebook công nghệ mẫu: hook nhanh, dữ kiện cô đọng,
bối cảnh cạnh tranh, nhận định dễ hiểu và câu hỏi cuối bài. Chỉ đọc một post khoảng
vài phút là nắm được phần lớn thông tin, đồng thời có thể copy để biên tập/chia sẻ.

Nếu chỉ prompt LLM “viết giống mẫu”, hệ thống dễ phóng đại benchmark, biến lời đồn
Reddit thành sự thật, thêm số/URL không tồn tại, lặp một sự kiện thành nhiều bài và
không giải thích được câu nào lấy từ đâu. Feature phải giữ ưu điểm dễ đọc của format
social mà không đánh đổi grounding và provenance của Reddit Radar.

## Goal

Thêm data product `Grounded Social Brief` được sinh offline từ Silver + enrichment,
đưa qua quality gate rồi publish vào Gold/Serving. Unified feed tại `/` dùng brief làm
content tier cao nhất; `/briefs` chỉ là compatibility alias tới filter brief. Người
dùng có thể:

1. Đọc một bài tiếng Việt hoàn chỉnh, không cần mở post Reddit gốc để hiểu ý chính.
2. Copy `share_text` bằng một nút, giữ xuống dòng/bullet/emoji đúng format.
3. Mở “Nguồn & kiểm chứng” để xem từng claim/con số dựa vào post, comment hay resource
   nào và mức `verified|source_claim|inference`.
4. Lọc theo thời gian/domain, tìm kiếm và xem các bản `needs_review` khi cần audit.

Pipeline tự chọn tín hiệu/cluster có giá trị, sinh bounded batch bằng Gemini/OpenAI,
không generate trong HTTP request. Chỉ artifact `ready` xuất hiện mặc định; provider
fail, thiếu evidence hoặc quality fail giữ last-known-good và không tạo bài giả local.

## Non-goals

- Không tự đăng Facebook/X/LinkedIn, không quản lý tài khoản social và không scheduling
  publish ra ngoài. Feature chỉ đọc, preview và copy nội bộ.
- Không sao chép danh tính, tên hoặc tuyên bố là bài của người viết mẫu. Style profile
  là giọng biên tập chung của Reddit Radar, rút ra từ cấu trúc chứ không impersonate.
- Không scrape Facebook và không dùng các đoạn mẫu làm nguồn factual cho bài mới.
- Không gọi web search/article fetch trong request hoặc generator. Nguồn chỉ là dữ liệu
  đã ingest/transform/enrich; source coverage thiếu phải được ghi nhãn.
- Không thay thế Knowledge/Evidence view; social brief là bề mặt đọc nhanh, evidence
  drawer và post detail vẫn là nơi kiểm chứng.
- Không generate cho NSFW, deleted/removed, discussion không có nội dung hữu dụng hoặc
  topic chỉ có title nhưng lại yêu cầu kết luận kỹ thuật mạnh.
- Không fine-tune model trong spec này; dùng structured prompting + deterministic
  sanitizer/renderer/quality gate.

## Dependency và layer placement

Spec phụ thuộc các contracts của
`2026-07-28-medallion-lite-internal-production.md`:

- WP5: enrichment lineage và provider failure semantics.
- WP6: versioned Gold materialization, DQ và atomic publish.
- WP7: `ServingRepository` và web/API consumer boundary.
- WP8: bounded CLI/systemd orchestration, backup và run metadata.

Trước live publish còn phụ thuộc:

- `2026-07-28-safe-source-content-ingestion.md` cho story dạng link có technical
  claim/số liệu không đủ bằng chứng từ selftext/comment. Story không cần external
  body vẫn có thể chạy nếu evidence gate đạt.
- `2026-07-28-llm-workload-control-quality.md` cho queue, priority, quota, lineage,
  circuit breaker và golden-set release threshold.
- `2026-07-28-internal-production-program.md` cho release wave/go-live gate.

Feature nằm trong flow:

```text
Silver post/comment/metrics
        +
PostAnalysis V2/resources/digest
        │
        ▼
deterministic story clustering + candidate selection
        │
        ▼
LLM SocialBriefV1 → sanitizer → grounding/style quality gate
        │
        ├── rejected / needs_review → audit only
        └── ready → mart_social_brief(publish_id)
                           │
                           ▼
        ServingRepository → /api/briefs + /api/feed → React
```

Không thêm direct SQL từ `web/app.py`; không đọc Bronze. Trong thời gian Medallion
chưa cutover, feature code có thể phát triển/test bằng `LegacyServingRepository`, nhưng
live publish chỉ được bật sau dependency contracts tương ứng pass.

## Editorial style contract

Style ID: `social_explainer_vi_v1`.

Đặc trưng lấy từ các mẫu người dùng cung cấp:

1. **Hook 1–2 dòng**: thực thể + thay đổi chính + một nhịp cảm xúc; `NÓNG` chỉ dùng khi
   source event ≤24 giờ và timestamp đáng tin.
2. **What happened**: 1–3 đoạn ngắn nói rõ ai làm gì, sản phẩm/model/policy nào.
3. **Key facts**: 2–5 dữ kiện hoặc bullet, ưu tiên số liệu/kiến trúc/cost/benchmark có
   evidence; không liệt kê spec vô nghĩa.
4. **Why it matters**: giải thích ảnh hưởng cho developer/team/doanh nghiệp.
5. **Competitive context**: đối chiếu đối thủ hoặc cách làm cũ chỉ khi source bundle có
   bằng chứng; không biến benchmark đơn lẻ thành kết luận “đã vượt toàn diện”.
6. **Editorial takeaway**: một nhận định có tín hiệu ngôn ngữ như “Điểm đáng chú ý là”,
   “Điều này có thể…”; inference không được trình bày như source fact.
7. **Closing question**: một câu hỏi cụ thể, gắn với quyết định hoặc tranh luận thật.

Output mục tiêu 180–360 từ tiếng Việt, đoạn ngắn 1–3 câu, tối đa 6 emoji, không quá
hai emoji liên tiếp, tối đa một hook viết hoa. Không dùng clickbait rỗng, chửi bới,
khẳng định “đầu tiên/tốt nhất/vượt/giảm X%” nếu evidence không support. Giữ thuật ngữ
kỹ thuật cần thiết nhưng giải thích tác động bằng ngôn ngữ phổ thông.

`share_text` được renderer deterministic ghép từ structured fields; model không được
trả một blob tự do khác nội dung structured payload. Web có thể copy nguyên văn, còn
UI card có thể trình bày các section riêng.

## Data contracts

### StoryClusterV1

Clustering deterministic trước LLM:

```json
{
  "cluster_id": "sha256",
  "cluster_version": "story_cluster_v1",
  "canonical_key": "url-or-title-fingerprint",
  "topic": "string",
  "source_post_ids": ["post_id"],
  "source_resource_ids": ["resource_id"],
  "earliest_source_at": 0.0,
  "latest_source_at": 0.0,
  "created_at": 0.0,
  "source_run_id": "run_id"
}
```

Ưu tiên canonicalized external URL; tiếp theo Reddit crosspost identity; cuối cùng
normalized title fingerprint/entity-keyword similarity có threshold cố định. Một post
chỉ thuộc một current cluster version. Cluster tối đa 5 source post ưu tiên composite
value/evidence completeness; LLM không tự thêm source vào cluster.

### SocialBriefV1

Structured payload bắt buộc:

```json
{
  "schema_version": "social_brief_v1",
  "style_profile": "social_explainer_vi_v1",
  "cluster_id": "sha256",
  "headline": "string",
  "hook": "string",
  "what_happened": ["paragraph"],
  "key_facts": [
    {
      "text": "string",
      "evidence_refs": ["evidence_id"],
      "verification": "verified|source_claim|inference"
    }
  ],
  "why_it_matters": "string",
  "competitive_context": "string|null",
  "editorial_takeaway": "string",
  "closing_question": "string",
  "limitations": ["string"],
  "source_post_ids": ["post_id"],
  "source_urls": ["https-url"]
}
```

Persistence:

- `ai_social_brief(brief_id PRIMARY KEY, cluster_id, style_profile, provider, model,
  prompt_version, input_hash, status, payload_json, input_tokens, output_tokens,
  generated_at, error, source_run_id)`.
- `social_brief_source(brief_id, post_id, resource_id, PRIMARY KEY(brief_id, post_id,
  resource_id))`; nullable resource dùng sentinel/normalized association implementation
  được khóa trong migration, không dùng nullable composite key mơ hồ.
- `mart_social_brief(publish_id, brief_id, cluster_id, period, domain_id, headline,
  share_text, verification_status, generated_at, source_run_id)`.

Status lifecycle: `running|ready|needs_review|rejected|error`. Chỉ `ready` được publish
mặc định. `needs_review` có thể xem bằng explicit internal filter nhưng không xuất hiện
trong main feed và không được story-export/social-copy mặc định.

`input_hash` bao gồm sorted source IDs + evidence payload hashes + analysis versions +
style profile + prompt version. Cùng input hash đã có `ready` là cache hit; provider
retry không generate trùng. Thay source/analysis/style/prompt tạo artifact version mới,
không overwrite last-known-good.

## Evidence và truthfulness rules

Generator nhận evidence bundle có ID cố định cho title/selftext/article text đã lưu,
comment excerpt, resource metadata và PostAnalysis claims. Input được đánh dấu untrusted
để title/comment không thể chèn prompt instruction.

Mỗi key fact phải có ít nhất một evidence ref, trừ `inference` vẫn phải trỏ tới facts
làm cơ sở. Sanitizer:

- drop unknown evidence/source IDs và URL không có trong bundle;
- reject số, phần trăm, parameter count, context length, giá/cost và benchmark score
  không xuất hiện trong evidence text có ref;
- reject comparison/superlative không có evidence cho cả hai vế hoặc rewrite về dạng
  “nguồn X cho biết…”;
- giữ `source_claim` cho claim chỉ xuất hiện ở Reddit/title/comment hoặc source tự công
  bố; `verified` chỉ khi corroborated bởi stored source text/evidence policy;
- buộc inference dùng modal language, không được gắn `verified`;
- reject bài sai tiếng Việt, generic, lặp đoạn, thiếu why-it-matters/question, vượt word/
  emoji limits, hoặc nhồi sản phẩm không cùng cluster;
- không gọi local extract là AI và không publish local fallback như brief hoàn chỉnh.

Overall `verification_status`:

- `verified`: mọi factual key fact verified, inference được gắn nhãn đúng.
- `partially_verified`: có `source_claim` nhưng không có unsupported claim; được `ready`
  nếu UI/share text thể hiện attribution/limitation rõ.
- `needs_review`: claim quan trọng chỉ có community hearsay, sanitizer phải rewrite quá
  nhiều, source mâu thuẫn hoặc source coverage không đủ cho headline.
- `rejected`: invented source/number, prompt injection thắng, sai cluster/language hoặc
  nội dung có thể gây hiểu sai nghiêm trọng.

## Candidate selection và scheduling

Candidate mặc định:

- non-NSFW current Gold signal trong period 3h/day, ưu tiên composite value;
- có PostAnalysis V2 từ Gemini/OpenAI và ít nhất 2 usable evidence items, hoặc một
  stored source text đủ mạnh + một analysis grounded;
- chưa có `ready` cùng input hash/style/prompt;
- cluster không có brief ready trong 24 giờ, trừ input thay đổi materially;
- tối đa 5 provider calls/run và một run/30 phút; digest/backlog dùng chung provider
  budget/lock để không tự gây quota storm.

CLI:

- `social-brief --post-id ID --provider gemini --dry-run` cho debug có kiểm soát.
- `social-briefs --period day --limit 5 --provider gemini` cho bounded batch.
- `social-briefs --backlog --limit 5` tiến dần qua cluster đủ evidence, không kẹt top-N.
- `social-brief-status` trả ready/review/rejected/error/cache-hit/remaining và freshness.

Không có API POST generate. Systemd service chạy sau analysis/enrichment và trước Gold
materialize/publish; cùng lock/cost ceiling với pipeline LLM.

## Serving API và web UX

Read-only endpoints:

- `GET /api/briefs?period=day&domain=all&q=&status=ready&limit=12&offset=0`.
- `GET /api/briefs/{brief_id}` trả structured brief, rendered `share_text`, sources,
  evidence map, provider/model, verification, limitations và lineage.

Web (theo canonical IA của dependent spec `2026-07-28-single-intelligence-feed-ui.md`):

- Main `/` hiển thị social brief như tier cao nhất của một unified feed item; `/briefs`
  là compatibility alias tới `/?tier=brief`, không thêm top-level tab thứ tư.
- Feed card hiển thị headline, full/expandable brief, domain, verification badge,
  generated time và actions `Copy`, `Nguồn & kiểm chứng`, `Chi tiết`.
- Story detail hiển thị bài đúng xuống dòng; `Copy bài viết` dùng Clipboard API và báo
  thành công/thất bại; không tự append tracking link/hashtag.
- Drawer **Nguồn & kiểm chứng** map key fact → evidence excerpt/source; source claim,
  inference và limitation có visual distinction, không chỉ khác màu.
- Unified feed precedence tự đưa brief `ready` lên representation cao nhất; không có
  Today/Knowledge/Radar page riêng hoặc section brief lặp lại.
- Search/domain/period/pagination hoạt động server-side; default không tải toàn bộ
  payload/evidence list.
- Responsive 1440px và 390px, không tràn ngang, copy button keyboard accessible,
  focus states/ARIA label rõ; khi chưa có brief hiển thị empty state chứ không gọi AI.

## Acceptance criteria

- [ ] AC1 — `StoryClusterV1` group canonical URL/crosspost/title fingerprint deterministic, không gộp hai topic khác và không để một post thuộc hai current cluster — verify: `.venv/bin/python -m unittest tests.test_story_clustering`
- [ ] AC2 — `SocialBriefV1` validate đủ section, renderer tạo `share_text` 180–360 từ, đúng thứ tự, xuống dòng/bullet ổn định và không lệch structured payload — verify: `.venv/bin/python -m unittest tests.test_social_brief_contract tests.test_social_brief_renderer`
- [ ] AC3 — Grounding sanitizer loại unknown ID/URL, invented number và unsupported comparison; source claim/inference được attribution hoặc modal wording đúng — verify: `.venv/bin/python -m unittest tests.test_social_brief_grounding`
- [ ] AC4 — Prompt coi title/comment/resource là untrusted, injection fixture không đổi instruction/schema và output sai ngôn ngữ/generic/emoji limit bị reject — verify: `.venv/bin/python -m unittest tests.test_social_brief_prompt tests.test_social_brief_quality`
- [ ] AC5 — Candidate selection chỉ lấy non-NSFW cluster đủ evidence, dedupe input hash, không kẹt top-N, bounded provider attempts và không local fallback thành `ready` — verify: `.venv/bin/python -m unittest tests.test_social_brief_pipeline`
- [ ] AC6 — Persistence giữ version/lineage, không overwrite last-known-good, migration chạy trên copy DB thật và FK/quick-check sạch — verify: `.venv/bin/python -m unittest tests.test_social_brief_storage && tmp_db=$(mktemp) && cp reddit.db "$tmp_db" && .venv/bin/python cli.py --db "$tmp_db" migrate --apply && sqlite3 -readonly "$tmp_db" 'PRAGMA quick_check; PRAGMA foreign_key_check;'`
- [ ] AC7 — Gold/Serving chỉ publish `ready`, quality fail giữ current publish cũ, `needs_review` chỉ xuất hiện khi explicit filter — verify: `.venv/bin/python -m unittest tests.test_social_brief_publish tests.test_serving`
- [ ] AC8 — API list/detail read-only, pagination/search/domain/period/status đúng, detail trả evidence/verification/lineage và không trigger crawl/LLM/write — verify: `.venv/bin/python -m unittest tests.test_social_brief_api tests.test_web_api`
- [ ] AC9 — Unified feed render brief ready ở tier cao nhất, `/briefs` alias đúng filter, card/detail/copy/evidence drawer/empty/error states hoạt động và production build pass — verify: `npm --prefix web/frontend run build && .venv/bin/python -m unittest tests.test_social_brief_api tests.test_feed_api`
- [ ] AC10 — UI desktop/mobile không tràn ngang, copy giữ format, keyboard/focus/ARIA hoạt động và visual label phân biệt verified/source claim/inference không chỉ bằng màu — verify-manual: Dataguy: mở `/?tier=brief`, alias `/briefs` và một story detail tại 1440px/390px, dùng keyboard qua filter/card/copy/drawer, paste clipboard vào text editor và đối chiếu exact `share_text`, lưu screenshot vào `/tmp`
- [ ] AC11 — CLI dry-run/batch/backlog/status có cost ceiling, systemd không chạy chồng với Gemini/digest và unit verify sạch — verify: `.venv/bin/python cli.py social-brief --help && .venv/bin/python cli.py social-briefs --help && .venv/bin/python cli.py social-brief-status --help && systemd-analyze verify deploy/systemd/*.service deploy/systemd/*.timer`
- [ ] AC12 — Golden content fixtures đạt structure/style rubric, đủ what/facts/why/context/takeaway/question, không bịa claim và không chứa identity/catchphrase riêng của người viết mẫu — verify: `.venv/bin/python -m unittest tests.test_social_brief_editorial`
- [ ] AC13 — Full project gate, frontend build và dependency specs cần thiết đều xanh — verify: `.venv/bin/python -m compileall -q reddit_crawler jobs web cli.py && .venv/bin/python -m unittest discover -s tests && npm --prefix web/frontend run build`
- [ ] AC14 — Bounded live batch tạo ít nhất 5 brief từ 5 cluster khác nhau; Dataguy đánh giá ≥4/5 bài “đọc riêng đã nắm đủ chuyện”, 0 unsupported number/comparison, web serving healthy và rollback về publish cũ được — verify-manual: Dataguy: sau online backup và dependency cutover, chạy `social-briefs --period day --limit 5 --provider gemini`, review payload/evidence/share text theo rubric, kiểm tra `/`, `/?tier=brief`, alias `/briefs`, story detail và health rồi thực hành serving pointer rollback và restore publish mới

## Risk tier

Tier: R3 — thêm schema, LLM calls có chi phí, Gold/Serving contract, systemd và live
content publish nội bộ; fixture/copy work an toàn nhưng AC14 cần Dataguy phê duyệt.

## Constraints

- Không build feature trước khi Medallion WP5–WP7 interfaces ổn định hoặc có compatible
  repository contract được test; không bypass layer để “làm UI trước cho nhanh”.
- Không DDL/write live trước AC1–AC13 pass trên fixture và DB copy.
- Không đưa raw prompt/provider response vào DB/log/report; `.env` không được in/scan.
- Mọi provider run có hard attempt/token/cost limit; failure không overwrite ready brief.
- Bài phải tiếng Việt; thuật ngữ/model/benchmark giữ nguyên khi dịch làm sai nghĩa.
- `NÓNG` và freshness language dựa event/source time, không dựa generated time.
- Reddit title/comment là `source_claim`, không tự thành `verified` vì nhiều upvote.
- Bài `partially_verified` phải giữ attribution/limitation trong cả UI và `share_text`;
  copy renderer không được bỏ disclaimer quyết định status.
- Không lưu/copy tên người viết mẫu hoặc prompt yêu cầu impersonation vào style profile.
- Không thêm social publishing credential; Clipboard API chỉ chạy trong browser nội bộ.
- Story export/MediaWorkflow không tự lấy social brief cho tới khi có spec mapping riêng.

## Stop if (only if a run could plausibly overreach)

- Cần cho web POST/generate, đọc raw hoặc gọi provider trong request path.
- Sanitizer không thể map một number/comparison quan trọng về exact evidence excerpt.
- Model output nhiều lần gộp sai hai event nhưng deterministic cluster không phát hiện.
- Feature cần scrape nguồn ngoài để xác minh nhưng article/source ingestion chưa có guard.
- Bounded live sample có bất kỳ unsupported benchmark/cost/parameter claim nào.
- Disk đạt 95%/còn dưới 15 GiB hoặc provider quota error lặp lại mà scheduler tiếp tục gọi.
- Medallion table/ServingRepository interface thay đổi bởi session khác trong lúc Build.

## Interfaces (only if criteria depend on each other)

- AC1 produces `build_story_clusters(signals, resources, version='story_cluster_v1') -> list[StoryClusterV1]`.
- AC2 produces `SocialBriefV1` đúng schema và `render_social_brief(brief) -> str`; `share_text` luôn là renderer output.
- AC3/AC4 produce `sanitize_and_grade_social_brief(raw, evidence_bundle) -> BriefQualityResult` với status, normalized brief, flags và evidence map.
- AC5 produces `run_social_brief_batch(db_path, period, limit, provider, backlog=False) -> RunResult` bounded theo attempts, không theo success target.
- AC7 extends `materialize_gold` với `mart_social_brief` và `ServingRepository.briefs()/brief_detail()`.
- AC8 produces API response `BriefListPage` và `BriefDetail` từ ServingRepository, không trực tiếp từ enrichment table.
- AC11 produces CLI `social-brief`, `social-briefs`, `social-brief-status`; live systemd service dùng chung LLM lock.

## Plan (filled at Plan stage)

### Work package 0 — Dependency gate và editorial rubric

- Xác nhận Medallion WP5 lineage, WP6 publish và WP7 serving interfaces; nếu chưa có,
  chỉ làm contracts/fixtures, không tạo alternate direct-SQL implementation.
- Chuyển các mẫu user thành abstract rubric/fixture expectations: hook, facts, why,
  context, takeaway, question, word/emoji limits; không lưu tên/identity người viết.
- Chọn 10 post/analysis hiện có đại diện model release, security benchmark, policy,
  hardware/supply và discussion yếu; fixture phải redact/thu gọn nhưng giữ evidence IDs.
- Ghi baseline provider quota, analysis coverage và số cluster eligible read-only.
- Files: spec, `docs/social-brief-editorial-guide.md`, sanitized test fixtures.
- Fast check: fixture JSON parse, secret scan, current full suite xanh.

Exit gate: rubric có pass/fail rõ, dependency interface không mơ hồ, không runtime write.

### Work package 1 — Cluster, contracts và renderer

- Thêm `reddit_crawler/social_briefs.py` phần pure: URL/title canonicalizer,
  `StoryClusterV1`, `SocialBriefV1`, validators và deterministic renderer.
- Viết clustering false-positive/false-negative fixtures, Vietnamese word counter,
  emoji/section/length renderer tests; behavior test và implementation cùng increment.
- Không gọi LLM/storage ở package này.
- Files: `social_briefs.py`, `tests/test_story_clustering.py`,
  `test_social_brief_contract.py`, `test_social_brief_renderer.py`.
- Fast check: AC1/AC2, compileall, existing analytics tests.

Exit gate: identical inputs cho identical cluster/render output; full suite xanh.

### Work package 2 — Evidence bundle, prompt và quality gate

- Build bundle chỉ từ Serving/Silver/enrichment contract: source IDs, excerpts, URLs,
  provider/version, timestamp; mark every source string untrusted.
- Prompt structured output theo editorial contract; model không tạo `share_text` blob.
- Implement deterministic sanitizer cho ID/URL/number/comparison/verification và style;
  semantic quality gate fail-closed cho generic/wrong-topic/wrong-language.
- Reuse V2 grounding helpers khi contract phù hợp; không copy sanitizer khác dễ drift.
- Files: `social_briefs.py`, `llm.py` hoặc provider adapter nhỏ,
  tests AC3/AC4/AC12.
- Fast check: grounding/prompt/quality/editorial tests và existing LLM suite.

Exit gate: adversarial fixtures bị reject/downgrade đúng; không invented URL/number.

### Work package 3 — Candidate selector, pipeline và persistence

- Thêm additive migration/table/index trên fixture/copy DB; giữ last-known-good version.
- Selector dùng current signal/knowledge contracts, deterministic cluster, eligibility,
  input hash, freshness/dedupe và backlog cursor; cost ceiling là attempted calls.
- Persist lifecycle/error sanitized/lineage; provider strict không local fallback;
  partial batch exit nonzero nhưng successful artifacts commit ngắn theo item.
- Files: schema/storage migration, `jobs/social_briefs.py`, `cli.py`, tests AC5/AC6.
- Fast check: pipeline/storage tests, migration + quick/FK trên DB copy.

Exit gate: bounded idempotent batch; retry không overwrite ready; live chưa enabled.

### Work package 4 — Gold, Serving và read-only API

- Extend mart builder/publisher với ready social briefs; quality fail không flip pointer.
- Add `ServingRepository.briefs/brief_detail`, server-side filters và paginated query;
  detail maps claim refs to excerpts without exposing raw provider payload.
- Add read-only endpoints; prove request does not change DB hash/count/mtime semantics
  beyond concurrent collector activity by fixture/read-only connections.
- Files: `marts.py`, `quality.py`, `serving.py`, `web/app.py`, tests AC7/AC8.
- Fast check: publish/serving/API tests và full web API suite.

Exit gate: legacy consumers không regress; needs_review hidden by default; API read-only.

### Work package 5 — React Bài viết UX

- Implement brief tier/card/detail qua component contracts của Single Intelligence
  Feed; reuse filters, ProviderBadge, domain và evidence visual language, nhưng social
  copy typography có paragraph/bullet spacing.
- Implement copy with exact renderer text, success/error announcement, evidence drawer,
  non-color verification labels and responsive states.
- Let server feed precedence/dedupe place ready brief; lazy-load detail evidence; no
  generate/regenerate button và không thêm page/tab song song.
- Split components/files nếu `App.jsx` tiếp tục phình; không thêm library khi native
  React/CSS/Clipboard API đủ.
- Files: frontend App/components/api/styles; API fixtures; manual screenshots AC10.
- Fast check: Vite build, API tests, manual 1440/390/keyboard/copy.

Exit gate: AC9/AC10 pass; unavailable brief degrades to empty state, không ảnh hưởng app.

### Work package 6 — Scheduling, observability và runbook

- Add CLI/help/status, systemd oneshot/timer sau analysis và trước materialize; dùng
  existing flock/provider budget; schedule ban đầu 30 phút/batch 5.
- Extend health/layer-status counts/freshness/error reasons; operation report chỉ lưu
  IDs/error fingerprint/quality flags, không lưu prompt/raw response.
- Runbook: quota, needs_review, rejected grounding, cluster error, disable service,
  serving rollback và editorial spot-check.
- Files: CLI, job, systemd, health, deploy README, runbook, tests AC11.
- Fast check: CLI tests, systemd verify, full suite.

Exit gate: dry-run/status không write/call provider; timer không chồng backlog/digest.

### Work package 7 — Release candidate và live validation

- Run AC1–AC13; online DB copy migration; bounded fixture/provider test; materialize,
  publish, API/UI/content review trên copy/staging mode.
- Record five candidate inputs and expected evidence before live call; backup/checksum,
  disk/quota capacity và rollback pointer plan.
- Dataguy authorizes AC14. Run batch 5, review every number/comparison/source, score
  ≥4/5 usefulness; bất kỳ unsupported factual claim nào fail gate dù prose hay.
- Publish live only after content gate; test unified feed/brief filter/detail/health;
  rollback drill; observe 24 giờ và keep feature flag off by default until evidence recorded.
- Update docs/spec Outcome per AC; no automatic external social publishing.

Rollback: disable brief timer, keep enrichment artifacts for audit, flip Serving to
previous publish. Không xóa brief/rewrite source data; provider issue không rollback
collector/Silver/Knowledge.

### Dependency order

```text
Medallion WP5–WP7 → Brief WP0 → WP1 → WP2 → WP3 → WP4 → WP5 → WP6 → WP7
```

Editorial fixtures/rubric của WP0 có thể chuẩn bị sớm; live/schema/Serving work không
được đi vòng dependency. Mỗi package thêm regression test cùng behavior, full suite
xanh trước package kế và ghi non-obvious decision vào spec.

## Decisions log (append during Build)

- 2026-07-28 — Product owner approved this authoritative consumer spec for execution
  after its Medallion, enrichment and LLM-control dependencies pass. Live generation
  and publishing remain gated.
- 2026-07-28 — Feature là grounded data product, không phải prompt/UI shortcut; generate
  offline, quality gate rồi Gold publish, web chỉ read/copy.
- 2026-07-28 — Dùng style chung `social_explainer_vi_v1`, mô phỏng cấu trúc hữu ích của
  mẫu chứ không tên/identity/catchphrase của một cá nhân.
- 2026-07-28 — `share_text` render deterministic từ schema để UI copy không khác payload
  đã được grounding review.
- 2026-07-28 — Reddit/community/title claim không tự được coi verified; attribution và
  limitations phải sống trong cả payload lẫn bản copy.
- 2026-07-28 — Không local fallback publishable. Quota/provider fail giữ last-known-good
  và health degraded, không sinh bài nghe tự tin nhưng không grounded.
- 2026-07-28 — Spec độc lập, phụ thuộc Medallion contracts; không làm architecture spec
  phình thành một mega-feature và không bypass ServingRepository.
- 2026-07-28 — Single Intelligence Feed spec supersedes ý tưởng thêm tab `/briefs`;
  brief là tier cao nhất của cùng story item, `/briefs` chỉ là compatibility filter.

## Outcome (filled at Ship)

Chưa build. Product contract, editorial rubric, schema, grounding rules, API/UX,
acceptance criteria, work packages, live content review và rollback plan đã được đặc tả;
scope đã approved nhưng execution chờ Medallion, ingestion và LLM-control dependencies.
