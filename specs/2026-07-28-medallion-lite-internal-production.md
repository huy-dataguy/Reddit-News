# Spec: Medallion-lite data pipeline cho internal production

- Date: 2026-07-28
- Status: draft

## Problem

Reddit Radar đã chạy end-to-end nhưng ranh giới dữ liệu chưa thành contract vận hành:
crawler vừa append JSONL vừa upsert SQLite trong cùng code path; JSONL là các file lớn
không partition/manifest nên khó replay có kiểm soát; `fact_post`/`fact_comment`, output
enrichment và serving mart đang nằm chung một schema; `trend_score` được tính lại khi
API nhận request; web biết trực tiếp nhiều bảng storage. Khi transform hoặc model lỗi,
hệ thống có retry nhưng chưa có một `run_id` xuyên suốt để trả lời bản raw nào tạo ra
record/mart nào, quality gate nào đã pass, và web đang phục vụ lần publish tốt nào.

Với quy mô hiện tại (một máy, SQLite, 6 subreddit, khoảng 7k post), đưa thêm Kafka,
Spark, Airflow, Delta Lake hay một cloud warehouse sẽ tăng vận hành nhiều hơn giá trị.
Cần một kiến trúc data engineering đúng lớp nhưng nhỏ, replayable và dễ phục hồi.

## Goal

Chuyển pipeline sang mô hình Medallion-lite, giữ SQLite + filesystem + systemd:

```text
Reddit OAuth / Arctic Shift
          │
          ▼
Bronze: immutable, partitioned JSONL + manifest
          │ deterministic replay
          ▼
Silver: normalized/conformed facts, dimensions, metrics
          │
          ├── deterministic enrichment: resources, taxonomy
          └── bounded LLM enrichment: PostAnalysis V2, digest
          │
          ▼
Gold: versioned signal/knowledge/digest marts
          │ atomic publish after data-quality gates
          ▼
Serving contract → FastAPI read-only → React / reports / story export
```

Sau khi ship, mọi payload nguồn phải được commit bền vững vào Bronze trước khi
transform; Silver có thể rebuild idempotently từ manifest; enrichment chỉ đọc Silver;
Gold được materialize theo `publish_id`; web không import/query Bronze hoặc tự ghép
trực tiếp storage schema; nếu một run fail quality gate thì web tiếp tục dùng publish
tốt gần nhất. Health hiển thị freshness, row counts và trạng thái từng layer.

## Non-goals

- Không thêm authentication, TLS, public rate limiting hoặc multi-user; web tiếp tục
  chỉ bind `127.0.0.1` cho người dùng nội bộ.
- Không thêm Kafka, Spark, Airflow, dbt, Delta/Iceberg, S3, Postgres hay Kubernetes.
- Không mở rộng nguồn ngoài Reddit và không bật generic article fetch trong spec này.
- Không đổi prompt/chất lượng nội dung LLM ngoài metadata lineage cần thiết.
- Không big-bang rewrite, không reset và không DDL trực tiếp trên `reddit.db` sống.
- Không bắt web chờ một pipeline run; serving luôn trả publish thành công gần nhất.

## Program relationships

- Thứ tự release và P0/P1 gates thuộc
  `2026-07-28-internal-production-program.md`.
- Revision/dependency/systemd/recovery mechanics thuộc
  `2026-07-28-release-engineering-runtime-operations.md`; WP8 ở đây chỉ sở hữu
  data-aware backup/retention/SLO contracts.
- Generic URL fetch không được implement trong spec này; Bronze/Silver extensions
  cho nó do `2026-07-28-safe-source-content-ingestion.md` sở hữu.
- Provider queue/quota/circuit breaker do
  `2026-07-28-llm-workload-control-quality.md` sở hữu; medallion chỉ cung cấp
  control-plane persistence/lineage primitives.

## Target data contracts

### Bronze — source of replay

Mỗi lần gọi nguồn tạo file riêng rồi atomic rename từ `.tmp`. Layout:

```text
raw/bronze/source=reddit/entity=<post|comment|subreddit|user>/date=YYYY-MM-DD/
  run=<run_id>/part-00001.jsonl.gz
```

Mỗi dòng là envelope JSON UTF-8 có đúng các field bắt buộc:

```json
{
  "schema_version": 1,
  "run_id": "uuid",
  "source": "reddit",
  "entity_type": "post",
  "source_id": "reddit-id",
  "fetched_at": 0.0,
  "payload_sha256": "hex-sha256",
  "payload": {}
}
```

`payload` giữ response nguồn; các field envelope phục vụ dedupe, audit và replay.
File JSONL hiện hữu không bị sửa/xóa; một importer compatibility đọc chúng như
Bronze legacy rồi ghi manifest, cho phép migration tăng dần.

### Control plane và lineage

Schema migration kế tiếp thêm ba bảng:

- `pipeline_run(run_id PRIMARY KEY, pipeline, status, started_at, finished_at,
  input_count, output_count, error_summary)`.
- `bronze_object(object_id PRIMARY KEY, run_id, entity_type, relative_path, sha256,
  row_count, min_fetched_at, max_fetched_at, transform_status, transformed_at)`.
- `data_quality_result(run_id, layer, check_name, status, observed_value, threshold,
  checked_at, PRIMARY KEY(run_id, layer, check_name))`.

Không lưu API key, OAuth token, prompt đầy đủ hoặc raw provider response vào metadata.
`error_summary` phải dùng cùng cơ chế sanitize/fingerprint như Gemini backlog.

### Silver — normalized source data

Các bảng hiện tại `dim_*`, `fact_post`, `fact_comment`, `fact_post_metrics`,
`fact_post_media` là Silver. Transformer đọc `bronze_object` pending theo thứ tự,
verify SHA-256, parse envelope và upsert idempotent. Một object chỉ chuyển sang
`success` trong cùng transaction với Silver writes; retry không tạo record trùng.

Reddit high-water mark chỉ được advance sau khi Bronze object đã atomic commit. Nếu
Silver fail, crawler không cần fetch lại; transformer replay object pending. Comment
enrichment cũng phải land response vào Bronze trước rồi mới transform sang Silver.

### Enrichment — derived evidence

`fact_extracted_resource`, `ai_post_analysis_v2`, `ai_digest` và
`enrichment_state` là derived/enrichment records. Mỗi artifact mới phải có hoặc truy
ra được `source_run_id`, `input_hash`, `transform_version`/`prompt_version`, provider,
model và timestamp. Enrichment chỉ dùng Silver đã transform thành công; không parse
file Bronze trực tiếp và không tự crawl trong read/API path.

### Gold và Serving

Materializer tạo bộ mart versioned tối thiểu:

- `mart_post_signal(publish_id, period, post_id, as_of, trend_score,
  composite_value_score, score_velocity, comment_velocity, resource_count,
  analysis_provider, source_run_id)`.
- `mart_post_knowledge(publish_id, post_id, domain_id, provider, model,
  analysis_version, analysis_json, generated_at, source_run_id)`.
- `mart_digest(publish_id, period, digest_id, generated_at, source_run_id)`.
- `serving_state(singleton_id PRIMARY KEY, current_publish_id, published_at,
  source_run_id)`.

Gold build ghi dưới `publish_id` mới. Sau khi completeness, uniqueness, FK/evidence,
freshness và JSON-contract checks pass, một transaction duy nhất đổi
`serving_state.current_publish_id`. Publish fail bị giữ để audit nhưng không được web
nhìn thấy. `reddit_crawler/serving.py` là repository contract duy nhất cho FastAPI,
report và story selector; module này chỉ trả dữ liệu thuộc current publish hoặc post
detail Silver đã chuẩn hóa, không đọc Bronze.

## Layer ownership và dependency rules

| Layer | Physical storage | Owner/writer | Allowed readers | Không được làm |
|---|---|---|---|---|
| Bronze | `raw/bronze/**.jsonl.gz` + `bronze_object` | collectors/backfill | transformer, backup, audit | web/LLM đọc trực tiếp; sửa file đã commit |
| Control | `pipeline_run`, `bronze_object`, `data_quality_result`, `crawl_state` | orchestrator/transformer | health, operators | chứa secrets/raw prompt; business query |
| Silver | `dim_*`, source `fact_*`, metrics | transformer | enrichment, mart builder, serving post-detail repository | crawler ghi trực tiếp sau cutover |
| Enrichment | resource/analysis/digest/state tables | deterministic/LLM jobs | mart builder, audit | gọi network từ web; ghi đè LLM tốt bằng local fallback |
| Gold | `mart_*` theo `publish_id` | materializer | quality/publisher, serving repository | update current publish từng row |
| Serving | `serving_state` + `ServingRepository` | publisher | FastAPI, report, story export | đọc Bronze; network call; write trong request |

Dependency chỉ đi xuôi. Health được đọc Control + Serving nhưng không biến health
request thành một pipeline job. Các module layer thấp không import module layer cao.

## Internal production SLO và data-quality policy

SLO áp dụng khi máy chủ và Internet hoạt động; LLM availability được đo riêng để quota
Gemini không làm collector bị coi là down:

| Capability | Target | Health threshold |
|---|---|---|
| Reddit collection | không mất high-water item đã fetch | latest successful Bronze run ≤ 2 giờ |
| Bronze → Silver | replayable, idempotent | pending oldest ≤ 30 phút |
| Silver integrity | FK/critical contract sạch | `foreign_key_check=0`, invalid critical row = 0 |
| Enrichment | best effort, truthful provenance | latest success ≤ 24 giờ; quota lỗi = degraded, không hard-fail serving |
| Gold/Serving | publish nhất quán | current publish ≤ 4 giờ và đủ signal cho requested period |
| Web nội bộ | phục vụ last-known-good | HTTP 200 trong thời gian host up, kể cả enrichment run fail |
| Recovery | RPO 24 giờ, RTO 2 giờ | online backup ≤ 24 giờ và restore drill gần nhất ≤ 90 ngày |

Hard gates chặn publish:

- Bronze checksum/parse/manifest mismatch hoặc còn file `.tmp` thuộc run đã complete.
- Silver FK lỗi, duplicate natural key, row count giảm không có rule, timestamp/ID sai
  contract, hoặc một Bronze object bị đánh success khi transaction Silver rollback.
- Gold duplicate `(publish_id, period, post_id)`, JSON không parse/không đúng contract,
  evidence ID không tồn tại, `source_run_id` không truy được, hay current period không
  có signal trong khi Silver có source rows hợp lệ.
- Concurrency test thấy reader nhận dữ liệu trộn từ hai `publish_id`.

Soft gates làm health `degraded` nhưng vẫn cho publish source signals và giữ rõ
provenance:

- Gemini/OpenAI quota, timeout hoặc output quality fail.
- Một số discussion chưa có analysis hoặc resource.
- Digest period chưa sẵn sàng; UI dùng signal/knowledge từ publish hiện tại và ghi rõ
  briefing provisional/missing, không mượn digest sai period.

Disk policy: warning từ 85%, critical từ 92%, dừng job tạo file/copy lớn ở 95% hoặc
khi còn dưới 15 GiB. Backup/retention job chỉ đề xuất danh sách trong dry-run; delete
archive là thao tác manual ngoài spec cho tới khi restore checksum được chứng minh.

## Migration và compatibility strategy

Triển khai theo expand → shadow → validate → switch → contract, không big-bang:

1. `REDDIT_DATA_PIPELINE_MODE=legacy` (default ban đầu): hành vi đang chạy, module mới
   chưa ảnh hưởng production.
2. `shadow`: crawler vẫn giữ write path cũ, đồng thời tạo Bronze V1 + manifest;
   transformer/mart chạy shadow với bounded input; web vẫn đọc legacy repository.
3. So sánh legacy và medallion theo post/comment counts, IDs, top signals, knowledge
   provider, digest period và API golden fixtures. Sai khác phải được phân loại thành
   expected rule hoặc bug trước khi switch.
4. `medallion`: crawler commit Bronze rồi update checkpoint; transformer là writer
   duy nhất của Silver; enrichment đọc Silver; Gold publish sau DQ.
5. `REDDIT_SERVING_MODE=legacy|mart` tách khỏi data mode. Chỉ đổi sang `mart` sau ít
   nhất ba shadow publish liên tiếp pass và parity test sạch.
6. Giữ legacy read/write code tối thiểu bảy ngày ổn định để rollback tức thời. Xóa
   legacy path cần spec contract-cleanup riêng; spec này không xóa.

Các biến mới được document trong `.env.example`, có default an toàn và không chứa
credential: `REDDIT_DATA_PIPELINE_MODE`, `REDDIT_SERVING_MODE`, `BRONZE_ROOT`,
`BRONZE_HOT_DAYS`, `METRICS_DETAIL_DAYS`, `BACKUP_DIR`.

## Acceptance criteria

- [ ] AC1 — Bronze writer tạo envelope/partition đúng contract, gzip đọc lại được, atomic rename và không để `.tmp` sau thành công — verify: `.venv/bin/python -m unittest tests.test_bronze`
- [ ] AC2 — Collector commit Bronze trước khi advance high-water mark; lỗi Silver không làm mất item và replay pending object hai lần vẫn cho cùng Silver state — verify: `.venv/bin/python -m unittest tests.test_incremental tests.test_transform`
- [ ] AC3 — Importer legacy đọc được `raw/*.jsonl` hiện tại theo chế độ read-only, dedupe bằng content hash và không thay đổi file nguồn — verify: `.venv/bin/python -m unittest tests.test_bronze_legacy`
- [ ] AC4 — Control-plane tables ghi lifecycle `running|success|failed`, lineage và error đã sanitize; không chứa credential/prompt/provider raw response — verify: `.venv/bin/python -m unittest tests.test_pipeline_runs tests.test_secrets`
- [ ] AC5 — Silver transformer chuẩn hóa post/comment/metric từ Bronze, giữ FK, không giảm metadata giàu bằng payload thiếu và migration chạy được trên copy DB thật — verify: `.venv/bin/python -m unittest tests.test_transform tests.test_storage && tmp_db=$(mktemp) && cp reddit.db "$tmp_db" && .venv/bin/python cli.py --db "$tmp_db" migrate --apply && .venv/bin/python cli.py --db "$tmp_db" transform --pending --limit 0 --check-schema`
- [ ] AC6 — Comment enrichment land response vào Bronze rồi mới ghi Silver; resource và LLM enrichment chỉ nhận Silver record có lineage success — verify: `.venv/bin/python -m unittest tests.test_pipeline tests.test_enrichment_lineage tests.test_gemini_backlog`
- [ ] AC7 — Gold materialization cho kết quả scoring tương đương baseline hiện tại trên fixture, mỗi row có `publish_id/source_run_id`, và rebuild cùng input không nhân đôi row — verify: `.venv/bin/python -m unittest tests.test_marts tests.test_analytics`
- [ ] AC8 — Quality gate fail không đổi current publish; publish pass đổi pointer atomically và reader đồng thời chỉ thấy toàn bộ publish cũ hoặc mới — verify: `.venv/bin/python -m unittest tests.test_publish tests.test_data_quality`
- [ ] AC9 — FastAPI, report và story selector chỉ dùng `reddit_crawler.serving`; API contract cũ vẫn pass và không module nào trong `web/` đọc `raw/` — verify: `.venv/bin/python -m unittest tests.test_serving tests.test_web_api tests.test_export_story && ! rg -n "raw/|raw_dir|sqlite3\.connect" web`
- [ ] AC10 — `/api/health` trả trạng thái/freshness/count/run ID của Bronze, Silver, enrichment, Gold và serving; layer stale/failed làm health degraded nhưng web vẫn đọc publish tốt gần nhất — verify: `.venv/bin/python -m unittest tests.test_health_layers tests.test_web_api`
- [ ] AC11 — CLI có `migrate`, `data-run`, `transform`, `materialize`, `publish`, `layer-status`, `backup` với dry-run/bounded flags; systemd chain không chạy chồng và mọi unit verify sạch — verify: `.venv/bin/python cli.py migrate --help && .venv/bin/python cli.py data-run --help && .venv/bin/python cli.py layer-status --help && systemd-analyze verify deploy/systemd/*.service deploy/systemd/*.timer`
- [ ] AC12 — Backup dùng SQLite online backup/`VACUUM INTO`, kèm manifest Bronze và retention dry-run; restore vào temp DB có `quick_check=ok`, FK sạch và counts bằng snapshot — verify: `.venv/bin/python -m unittest tests.test_backup_retention`
- [ ] AC13 — Full project gate và frontend production build sạch — verify: `.venv/bin/python -m compileall -q reddit_crawler jobs web cli.py && .venv/bin/python -m unittest discover -s tests && npm --prefix web/frontend run build`
- [ ] AC14 — Cutover live có backup trước deploy, chạy bounded Bronze→Silver→Gold→Serving, current publish healthy, counts không giảm ngoài quy tắc retention và rollback về publish trước được — verify-manual: Dataguy: dừng tại gate này, tạo online backup + ghi counts/schema hash, chạy một bounded data-run trên DB sống, kiểm tra layer-status/API/systemd/log rồi thực hành đổi pointer về publish trước và quay lại publish mới

## Risk tier

Tier: R3 — thay đổi schema, collector, systemd và cutover dữ liệu production sống;
mọi build/test trên fixture hoặc DB copy có thể tự động, AC14 phải do Dataguy duyệt.

## Constraints

- `reddit.db` sống chỉ được read-only cho tới AC1–AC13 pass trên fixture và DB copy.
- Bronze-first không được làm tăng nguy cơ mất dữ liệu: raw object commit thành công
  trước checkpoint; partial/temp file không được đưa vào manifest.
- Giữ API response shape hiện có cho dashboard; migration kiến trúc không phải UI
  rewrite và không làm gián đoạn web quá một lần restart ngắn ở cutover.
- Giữ SQLite WAL, transaction ngắn và một writer lock; không thêm distributed system.
- Mỗi network/LLM run tiếp tục bounded; lineage không được biến retry thành call vô hạn.
- Không đọc/in `.env`; file metadata/report không chứa credential hoặc raw prompt.
- Dữ liệu hiện hữu, backup và `raw/*.jsonl` legacy không bị xóa trong spec này.
- Retention mặc định: Bronze hot 180 ngày; dữ liệu cũ chỉ được xóa local sau khi có
  archive đã verify checksum. Metrics chi tiết 90 ngày rồi daily rollup; thay đổi
  policy cần quyết định riêng dựa trên tốc độ tăng thật.
- Internal production không yêu cầu HA đa máy. RTO mục tiêu 2 giờ, RPO mục tiêu 24 giờ.

## Stop if (only if a run could plausibly overreach)

- Bất kỳ command nào chuẩn bị DDL/write vào `reddit.db` sống trước AC14.
- Cần reset/drop bảng hiện hữu hoặc sửa/xóa Bronze/backup legacy để tiếp tục.
- Counts post/comment trên DB copy giảm sau transform mà không có DQ rule giải thích.
- Một thay đổi làm web đọc Bronze, gọi Reddit/LLM hoặc ghi DB trong request path.
- Disk đạt 95% hoặc còn dưới 15 GiB trong lúc tạo copy/backup/materialization.
- Cùng file đang bị một spec/session khác sửa hoặc active spec cũ chưa được người dùng
  quyết định đóng/supersede trước khi bắt đầu Build.

## Interfaces (only if criteria depend on each other)

- AC1 produces `BronzeEnvelopeV1`: JSON object đúng schema trong phần Bronze và `BronzeObject` manifest có SHA-256/row count.
- AC2/AC3 produce `transform_pending(db_path, raw_root, limit) -> RunResult`, idempotent theo `bronze_object.object_id`.
- AC4 produces `RunResult`: `{run_id, pipeline, status, input_count, output_count, errors}` với errors đã sanitize.
- AC7 produces `materialize_gold(db_path, source_run_id, periods) -> publish_id` nhưng chưa đổi serving pointer.
- AC8 produces `publish_gold(db_path, publish_id) -> ServingState`, chỉ flip pointer sau DQ pass.
- AC9 produces `reddit_crawler.serving.ServingRepository(db_path)` với methods `health()`, `today()`, `trending()`, `knowledge_feed()`, `knowledge_detail()`, `post_detail()`, `resources()`, `story_candidate()`.
- AC11 produces `cli.py migrate --apply`: chỉ chạy additive migration trên DB path được truyền rõ; live path bị chặn nếu không có manual cutover approval.
- AC11 produces `cli.py data-run`: Bronze ingest (nếu được yêu cầu) → transform pending → enrich bounded → materialize → DQ → publish; exit nonzero khi partial/fail.

## Plan (filled at Plan stage)

### Work package 0 — Governance, baseline và capacity gate

Mục tiêu: không xây trên ground truth mơ hồ và không làm đầy disk khi tạo shadow data.

- Người dùng quyết định đóng/supersede `2026-07-26-evidence-to-intelligence-mvp.md`
  và `2026-07-25-simplify-radar.md`; không sửa lịch sử Outcome, chỉ cập nhật Status và
  Decisions log trung thực.
- Ghi baseline read-only: schema hash/version, counts từng bảng, `quick_check`, FK,
  kích thước DB/raw, current health, timer/service Result, API response fixtures và top
  signals theo 3h/day/week.
- Kiểm kê tốc độ tăng raw/metrics trong tối thiểu 24 giờ. Trước mọi DB copy lớn, disk
  phải dưới 92% và còn ít nhất 15 GiB; dọn/archive là quyết định manual riêng.
- Viết `docs/data-architecture.md` chứa diagram, ownership, glossary Bronze/Silver/
  enrichment/Gold/Serving và runbook invariants; cập nhật `docs/star_schema.md`.
- Files: specs cũ (status/decision only sau user approval), `docs/data-architecture.md`,
  `docs/star_schema.md`, `README.md`, API golden fixtures.
- Fast check: verify hiện tại + snapshot script read-only; chưa đổi runtime/schema.

Exit gate: baseline artifact không có secret; current full verify xanh; capacity gate
pass; một active spec duy nhất được xác định trước Build.

### Work package 1 — Contracts và tests trước behavior

Mục tiêu: khóa interface để các phase sau không tự định nghĩa lại kiến trúc.

- Thêm dataclass/TypedDict hoặc Pydantic-independent validators cho
  `BronzeEnvelopeV1`, `BronzeObject`, `RunResult`, `ServingState`.
- Thêm fixtures post/comment/subreddit/backfill, duplicate payload, corrupt gzip,
  partial file, invalid hash, transform rollback, concurrent readers và legacy JSONL.
- Lập test matrix cho `test_bronze.py`, `test_bronze_legacy.py`, `test_transform.py`,
  `test_pipeline_runs.py`, `test_enrichment_lineage.py`, `test_marts.py`,
  `test_publish.py`, `test_data_quality.py`, `test_serving.py`,
  `test_health_layers.py`, `test_backup_retention.py`, `test_secrets.py`; mỗi behavior
  test được thêm cùng work package implementation tương ứng, không commit test đỏ/skipped.
- Files: `reddit_crawler/contracts.py`, `tests/fixtures/data_pipeline/**`, tests trên.
- Fast check: contract validators và fixture integrity tests pass; full suite vẫn xanh.

Exit gate: exact interfaces trong spec và fixtures review được; không thay production.

### Work package 2 — Bronze V1 writer và legacy importer

Mục tiêu: durable landing zone trước khi tách transform.

- Thêm `reddit_crawler/bronze.py`: canonical JSON hashing, gzip part writer, fsync,
  atomic rename, manifest model, reader/validator và compatibility iterator.
- Không append file lớn. Mỗi run/entity tạo part file riêng; object ID deterministic từ
  source/entity/run/path/hash; duplicate registration là no-op.
- Legacy importer stream từng line, không load 300+ MB vào RAM, không sửa source,
  checkpoint theo byte offset/content hash và có `--dry-run --limit`.
- Nối writer dưới feature flag `shadow`; legacy DB upsert vẫn là authoritative ở WP2.
- Files: `reddit_crawler/bronze.py`, `config.py`, `.env.example`, `cli.py`,
  `jobs/incremental.py`, `jobs/backfill_arctic_shift.py`, tests AC1/AC3.
- Fast check: AC1, AC3, incremental regression, compileall.

Exit gate: shadow Bronze tạo/validate/replay được trên temp dir; legacy mode byte-for-
byte behavior không đổi; không chạm raw legacy/live DB.

### Work package 3 — Control plane và Silver transformer

Mục tiêu: crawler không còn là nơi duy nhất biết record đã đi tới đâu.

- Thêm additive schema migration cho `pipeline_run`, `bronze_object`,
  `data_quality_result` và lineage columns/indexes. Migration idempotent và fail-loud.
- Thêm `reddit_crawler/transform.py`: claim pending object, verify, transform theo
  entity, transaction per object, mark success cùng commit; stale running được retry.
- Reuse `Storage` upsert invariants; không duplicate business logic mapper.
- Checkpoint rule: Bronze atomic commit trước `crawl_state`; transformer pending bảo
  đảm eventual Silver. Có recovery khi crash ở từng boundary commit/rename/manifest.
- Chạy migration/replay trên fixture rồi copy thật; so counts/FK/schema hash nguồn.
- Files: `schema.sql`, `storage.py`, `transform.py`, `cli.py`, tests AC2/AC4/AC5.
- Fast check: AC2, AC4, AC5 trên temp/copy; full storage/incremental suite.

Exit gate: replay idempotent; failure injection không mất post/comment; live vẫn legacy.

### Work package 4 — Shadow ingestion và parity validation

Mục tiêu: chứng minh Bronze→Silver tương đương đường cũ trước khi đổi writer.

- Chạy `shadow` bounded: cùng fetched batch đi legacy write và Bronze manifest; transform
  Bronze vào một DB copy/shadow, không phải live Silver hai lần.
- Thêm comparator report chỉ gồm counts, missing/extra IDs, checksum/field differences,
  top signal differences và sample IDs; không dump body/comment/API payload.
- Phân loại sai khác: expected normalization, legacy bug hoặc medallion bug. Mọi bug có
  regression test; parity exception phải ghi rule, owner và rationale.
- Yêu cầu tối thiểu ba run incremental liên tiếp và một comment/backfill fixture parity.
- Files: `jobs/incremental.py`, `jobs/enrich.py`, `jobs/data_pipeline.py`,
  `reddit_crawler/parity.py`, CLI/report operation, parity tests.
- Fast check: AC2/AC5/AC6 và comparator exit 0 trên bounded DB copy.

Exit gate: 0 missing source IDs, 0 unexplained critical field drift, FK/quick-check sạch.

### Work package 5 — Enrichment lineage và failure semantics

Mục tiêu: mọi derived artifact giải thích được input/version mà không giữ secrets.

- Resource rows và analysis/digest mới liên kết `source_run_id`, `input_hash`, version;
  legacy rows được đọc tương thích với lineage `unknown`, không backfill giả.
- Comments fetched bởi enrichment phải Bronze commit trước Silver write.
- Deterministic enrichment retry an toàn; LLM retry tiếp tục cost-bound/fail-closed;
  quota/quality error là soft gate và không ghi đè LLM artifact tốt.
- Sanitize error ở boundary; tests scan DB/report/log fixture để chặn credential, prompt
  và provider raw response.
- Files: `jobs/enrich.py`, `jobs/gemini_backlog.py`, `pipeline.py`, `llm.py`,
  `resources.py`, `storage.py`, schema/indexes, tests AC4/AC6.
- Fast check: AC4/AC6, existing LLM/Gemini/pipeline tests.

Exit gate: lineage trace được Gold input → enrichment → Silver/Bronze; quota fail không
làm collector/Silver fail và không làm mất last-known-good analysis.

### Work package 6 — Gold marts, DQ và atomic publisher

Mục tiêu: chuyển business logic khỏi request-time thành versioned data product.

- Thêm `reddit_crawler/marts.py` materialize signal cho 3h/day/week/month/year bằng
  scoring function dùng chung; không copy hai công thức có thể drift.
- Materialize preferred V2/V1/provider precedence, knowledge payload và exact-period
  digest dưới publish ID mới; giữ `as_of/source_run_id`.
- Thêm `quality.py` hard/soft gates theo policy; lưu mọi result vào control table.
- Publisher transaction kiểm tra publish tồn tại/pass rồi flip singleton pointer;
  garbage collection publish cũ chưa thực hiện trong spec này.
- Concurrency tests mở readers liên tục trong lúc publish để chứng minh không mixed set.
- Files: schema/indexes, `analytics.py` extraction of pure scoring,
  `marts.py`, `quality.py`, `publish.py`, CLI, tests AC7/AC8.
- Fast check: AC7/AC8 và analytics regression.

Exit gate: ba shadow publish liên tiếp pass; top-N/order/API fields parity hoặc có rule.

### Work package 7 — ServingRepository và consumer migration

Mục tiêu: web/report/export phụ thuộc data contract, không phụ thuộc table layout.

- Thêm `ServingRepository` đúng interface đã khóa; legacy và mart implementations dùng
  cùng return types để feature flag đổi mà không sửa endpoint.
- Di chuyển SQL/read logic khỏi `web/app.py`; report và story selector dùng repository.
- Post detail được phép đọc Silver đã chuẩn hóa qua repository; không copy toàn bộ
  comment bodies vào Gold nếu chưa có measurement chứng minh cần.
- Health kết hợp Control/Serving, trả run/publish/freshness; không chạy DQ/network.
- Chạy API golden fixtures ở cả `legacy` và `mart`; build/kiểm tra UI desktop/mobile.
- Files: `reddit_crawler/serving.py`, `web/app.py`, `jobs/report.py`,
  `jobs/export_story.py`, frontend chỉ khi API contract thật sự cần, tests AC9/AC10.
- Fast check: AC9/AC10, web/export/report tests, Vite build.

Exit gate: consumer contract parity; mart mode có thể bật/tắt bằng restart và web luôn
phục vụ last-known-good publish.

### Work package 8 — Orchestration, backup, retention và runbooks

Mục tiêu: vận hành được sau khi người xây rời phiên làm việc.

- CLI: `migrate`, `data-run`, `transform`, `materialize`, `publish`, `layer-status`,
  `backup`; mọi command có dry-run/check khi phù hợp, bounded limit khi có external I/O
  và exit nonzero partial. `migrate --apply` cần DB path rõ và không tự chọn live DB.
- Một systemd lock điều phối transform/enrichment/materialize/publish; crawl chỉ phụ
  thuộc Bronze commit. Timer retry không tạo run chồng; service timeout hữu hạn.
- Online backup dùng SQLite backup API hoặc `VACUUM INTO`, checksum DB + Bronze
  manifest, atomic completed marker; daily schedule với 7 daily/4 weekly/6 monthly đề
  xuất nhưng delete chỉ sau manual archive verification.
- `retention --dry-run` báo bytes/objects/metrics sẽ archive/rollup; metrics rollup được
  parity test với velocity windows trước khi có write mode trong spec khác.
- Runbooks: deploy, status, provider quota, corrupt Bronze, failed transform, failed
  publish, restore, rollback, disk critical và key rotation.
- Files: `jobs/data_pipeline.py`, `jobs/backup.py`, CLI, systemd units/timers,
  `deploy/systemd/README.md`, `docs/runbooks/*.md`, tests AC11/AC12.
- Fast check: AC11/AC12, systemd verify, restore entirely in temp dir.

Exit gate: người khác có thể restore temp DB và giải thích current publish chỉ từ docs.

### Work package 9 — Pre-cutover release candidate

Mục tiêu: tạo bằng chứng release mà chưa thay đổi live authority.

- Chạy AC1–AC13, full frontend build và scan secrets.
- Tạo consistent online copy live; migrate/replay/materialize/publish trên copy; chạy
  quick/FK/DQ/API parity, load burst read và concurrent publish tests.
- Đo thời gian/capacity để chứng minh RTO 2h, disk headroom và timer budgets.
- Lập cutover record: exact config before/after, backup path/checksum, counts, current
  publish, commands, rollback trigger và người duyệt.
- Không chạy `npm install`, dependency upgrade hay schema cleanup trong cutover.

Exit gate: release candidate evidence xanh; Dataguy duyệt AC14 và maintenance window.

### Work package 10 — Live cutover, observation và rollback drill

Mục tiêu: chuyển authority có kiểm soát, không chỉ “restart thấy lên”.

- Chụp baseline/online backup; verify checksum/restore metadata trước thay config.
- Bật data `shadow`, chạy một bounded cycle và parity; sau pass bật data `medallion`
  nhưng serving vẫn `legacy`; quan sát tối thiểu một crawl + transform + enrich cycle.
- Tạo ba publish pass; đổi serving `mart`, restart web, kiểm tra API/UI/report/export.
- Rollback drill: đổi serving về `legacy`, xác nhận healthy; đổi lại `mart`. Nếu data
  path fail, trở về `legacy` và giữ Bronze mới để replay sau, không restore DB vội.
- Quan sát 24 giờ: freshness, pending age, counts, DQ, disk, quota, timers. Giữ legacy
  path bảy ngày; cleanup/deletion phải có spec sau.
- Cập nhật AC evidence, Outcome, README/runbooks; chỉ đánh dấu done khi AC14 và full
  gate sau observation pass.

Rollback triggers: hard DQ fail, missing source IDs, FK/quick-check lỗi, current publish
mixed/empty, web contract regression, disk ≥95%, collector checkpoint advance khi
Bronze chưa commit, hoặc hai consecutive scheduled Silver/Gold runs fail cùng root cause.

### Dependency order và parallelism

```text
WP0 → WP1 → WP2 → WP3 → WP4 → WP5 → WP6 → WP7 → WP8 → WP9 → WP10
```

WP5 contract analysis và WP8 runbook drafting có thể chuẩn bị song song sau WP3, nhưng
không merge/deploy trước dependency gates. WP6 phải dùng transform/lineage contract đã
ổn định; WP7 không tự định nghĩa mart schema; WP10 tuyệt đối không song song với code,
schema, dependency hoặc systemd edits.

Known risks: legacy JSONL có duplicate/shape khác nhau; Bronze-first làm tăng số file;
shadow mode tạm tăng disk I/O; materialized mart có thể drift so với Python scoring;
LLM lineage migration phải giữ V2 đang có. Mỗi WP phải để full suite xanh, ghi decision
và không stack work tiếp theo trên một gate đang đỏ.

## Decisions log (append during Build)

- 2026-07-28 — Chọn Medallion-lite theo logical contracts, không chọn lakehouse stack;
  quy mô và team hiện tại phù hợp filesystem + SQLite + systemd hơn distributed tools.
- 2026-07-28 — `fact_post`/`fact_comment` được phân loại là Silver; raw JSONL mới là
  Bronze; trend/intelligence/digest curated là Gold; FastAPI là Serving consumer.
- 2026-07-28 — Tách ingest khỏi transform bằng durable Bronze manifest. Checkpoint
  crawl advance sau Bronze commit, còn failed transform được replay không gọi Reddit.
- 2026-07-28 — Giữ một DB vật lý ở phase đầu nhưng tách bằng ownership/module/table
  contracts. Chỉ cân nhắc `serving.db` snapshot riêng nếu đo được lock/latency issue.
- 2026-07-28 — Internal-only: localhost/read-only web là đủ; auth/TLS/public hardening
  bị loại khỏi scope. Backup, lineage, DQ, disk retention và rollback vẫn bắt buộc.
- 2026-07-28 — Migration dùng hai feature flags độc lập cho data path và serving path;
  shadow/parity trước switch, legacy giữ bảy ngày để rollback, không big-bang rewrite.
- 2026-07-28 — DQ chia hard/soft: source integrity và atomic publish fail-closed;
  provider quota/coverage chỉ degraded để website tiếp tục phục vụ last-known-good.
- 2026-07-28 — Plan hoàn chỉnh nhưng Status giữ `draft`; yêu cầu lập plan không đồng
  nghĩa cho phép một supervisor tự chạy R3 hoặc thay đổi DB/systemd sống.

## Outcome (filled at Ship)

Chưa build. Architecture, interfaces, work packages, gates, cutover và rollback plan
đã hoàn chỉnh; structure gate phải pass trước khi đổi Status sang `approved`. Build và
đặc biệt AC14 chỉ bắt đầu khi người dùng phê duyệt thực thi R3 rõ ràng.

Dependent feature spec:

- `2026-07-28-grounded-social-briefs.md` tiêu thụ WP5–WP8 contracts để tạo bài social
  tiếng Việt grounded và publish qua Gold/Serving; feature không thay đổi acceptance
  criteria hoặc critical path của Medallion foundation.
- `2026-07-28-single-intelligence-feed-ui.md` rebuild web thành một unified feed, tiêu
  thụ Serving + Social Brief contracts và giữ route cũ bằng compatibility filters.
