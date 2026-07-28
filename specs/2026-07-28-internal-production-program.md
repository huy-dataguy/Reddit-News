# Spec: Reddit Radar internal-production program

- Date: 2026-07-28
- Status: draft

## Problem

Reddit Radar đang chạy được như một dịch vụ cá nhân nội bộ, nhưng tài liệu triển khai chưa tạo thành một chương trình production thống nhất. Ba spec mới đã mô tả kiến trúc dữ liệu, nội dung social và giao diện, trong khi các spec cũ còn trạng thái chồng chéo hoặc mâu thuẫn: `simplify-radar` muốn bỏ chính các tầng intelligence đang cần xây, `multi-source-intelligence-radar` có phạm vi quá rộng, `evidence-to-intelligence-mvp` vẫn là active/building dù phần outcome cho thấy nhiều hạng mục đã có, và `story-export-bridge` còn một lỗi nội dung ngoài repo. Nếu bắt đầu build trực tiếp, các phiên làm việc có thể chọn sai spec, làm UI trước data contract, tăng tải LLM khi quota chưa được điều phối, hoặc gọi hệ thống là production khi chưa có backup/restore và release có thể tái lập.

## Goal

Tạo một manifest duy nhất để đưa Reddit Radar từ trạng thái hiện tại lên internal production có kiểm soát. Manifest phải:

1. Xác định nguồn sự thật cho từng miền: ingest, Bronze/Silver/Gold, LLM, social brief, serving, UI, release/ops và MediaWorkflow.
2. Đóng hoặc supersede các spec cũ bằng quyết định có bằng chứng; tại mọi thời điểm chỉ có một spec `building`.
3. Ấn định dependency graph, thứ tự release, quality gate, owner và rollback gate.
4. Phân biệt rõ điều kiện bắt buộc cho production nội bộ với các yêu cầu public SaaS được miễn.
5. Kết thúc bằng một release-candidate dossier có thể chứng minh hệ thống thu thập, biến đổi, enrich, tạo bài, phục vụ web, sao lưu và phục hồi end-to-end.

Hệ thống được coi là internal-production-ready khi toàn bộ P0/P1 gate trong spec này đạt, không còn spec mâu thuẫn ở trạng thái `approved`/`building`, và một người vận hành đã ký manual go-live gate.

## Non-goals

- Không biến hệ thống thành sản phẩm public hoặc multi-tenant.
- Không yêu cầu Kubernetes, Kafka, Spark, object storage cloud, data warehouse cloud hoặc microservices.
- Không thêm X, Facebook, ArXiv hay RSS như nguồn crawl độc lập trong release đầu.
- Không tự động đăng bài lên mạng xã hội.
- Không tự động sửa repo sibling MediaWorkflow; chỉ theo dõi dependency và bằng chứng fix.
- Không đặt mục tiêu zero-downtime tuyệt đối cho dashboard localhost.
- Không đánh dấu spec cũ `done` nếu chưa audit acceptance criteria và outcome tương ứng.

## Scope authority và spec registry

| Miền | Spec authority | Quyết định |
|---|---|---|
| Program, gates, thứ tự release | `2026-07-28-internal-production-program.md` | Nguồn điều phối duy nhất |
| Raw → normalized → derived → serving | `2026-07-28-medallion-lite-internal-production.md` | Kiến trúc dữ liệu đích |
| Release, dependencies, CI, runtime hardening | `2026-07-28-release-engineering-runtime-operations.md` | Nguồn sự thật vận hành mã nguồn/runtime |
| Nội dung link/technical report | `2026-07-28-safe-source-content-ingestion.md` | Nguồn sự thật cho fetch/extract an toàn |
| Queue, quota, retry và quality LLM | `2026-07-28-llm-workload-control-quality.md` | Nguồn sự thật cho control plane AI |
| Bài social tiếng Việt có nguồn | `2026-07-28-grounded-social-briefs.md` | Consumer của Silver/Enrichment/LLM control |
| Web social-style một feed | `2026-07-28-single-intelligence-feed-ui.md` | Consumer của ServingRepository |
| Intelligence MVP cũ | `2026-07-26-evidence-to-intelligence-mvp.md` | Audit rồi đóng hoặc ghi phần còn lại; không song song build |
| Simplify cũ | `2026-07-25-simplify-radar.md` | Supersede; không thực thi việc xóa intelligence |
| Multi-source cũ | `2026-07-23-multi-source-intelligence-radar.md` | Supersede phạm vi; chỉ giữ ý tưởng đã được các spec mới nhận |
| Story export | `2026-07-20-story-export-bridge.md` | Bridge đã ship; generic publish bị chặn đến khi MediaWorkflow bỏ text Spotify hard-code |
| Legacy AI refactor | `refactor-spec-ai-analysis.md` | Audit/ghi historical; không phải active factory spec |

Status reconciliation phải dùng status hợp lệ của factory. Do template chưa có `superseded`, spec bị thay thế được chuyển `done` chỉ khi Outcome ghi rõ “superseded without build”, lý do, spec thay thế và bằng chứng không có thay đổi production từ spec đó.

## Internal-production gates

### P0 — bắt buộc trước mọi cutover

- Repo có version history, revision ID và working tree sạch tại release candidate.
- Python và frontend dependency được khóa; build mới từ môi trường sạch tái lập được.
- `verify` backend, frontend build, contract tests và browser smoke đều xanh.
- Không migrate trực tiếp `reddit.db`; migration rehearsal và rollback chạy trên copy thật.
- SQLite quick check, foreign-key check, backup, restore drill và disk-capacity gate đều đạt.
- Bronze không bị web đọc trực tiếp; web chỉ đọc ServingRepository/Gold projection.
- Link fetch không được bật nếu SSRF, redirect, size, content-type và timeout guards chưa đạt.
- LLM queue có lease/idempotency, quota budget, backoff, circuit breaker và không làm crawl dừng.
- Social brief không `ready` nếu thiếu evidence hoặc có số liệu/claim không đối chiếu được.
- Web bind localhost; không mở network interface ngoài `127.0.0.1` trong release này.
- Unit systemd cài đặt khớp file trong repo; secret và dữ liệu production không world-readable.
- Có runbook và named operator cho backup, restore, incident, quota, disk full và rollback.

### P1 — bắt buộc trước khi gọi là vận hành ổn định

- 72 giờ observation window không có data-loss, duplicate storm, queue lease kẹt hoặc Gold publish partial.
- Freshness SLO và job success SLO được đo từ dữ liệu, không suy từ trạng thái timer.
- Alert actionable đến operator khi crawl stale, DQ hard fail, backup stale, disk thấp hoặc LLM breaker mở lâu.
- Một bộ golden stories chứng minh cluster, evidence, brief và feed đúng end-to-end.
- Restore drill dựng được bản read-only độc lập và checksum/count nằm trong tolerance đã định.

### Được miễn vì chỉ chạy nội bộ localhost

- Public signup/login, RBAC nhiều tenant, billing, public API keys và abuse prevention quy mô Internet.
- CDN/WAF, public TLS certificate và 24/7 pager rotation.
- Horizontal autoscaling, multi-region, active-active database và zero-downtime deploy.
- SLA thương mại, privacy portal và public moderation workflow.

Các miễn trừ hết hiệu lực nếu bind ra LAN/VPN/Internet hoặc có nhiều user không cùng trust boundary; khi đó phải tạo security/access spec mới trước khi mở cổng.

## Dependency graph và release waves

```text
Wave 0  Spec reconciliation + release/recovery baseline
                |
Wave 1  Medallion-lite contracts, shadow path, ServingRepository
             /      \
Wave 2  Safe source   LLM workload control
             \      /
Wave 3       Grounded Social Briefs
                         |
Wave 4       Single Intelligence Feed UI
                         |
Wave 5  RC dossier -> restore/rollback drill -> 72h observation -> go-live

External lane: MediaWorkflow generic-copy fix -> story-export publish gate
```

Wave 0 có thể ship các guard khẩn cấp (backup, disk alert, dependency lock) trước khi toàn bộ medallion hoàn thành. Wave 2 được làm song song sau khi contracts của Wave 1 ổn định. UI shell có thể prototype bằng fixture, nhưng chỉ cutover sau khi `FeedStoryV1` và ServingRepository đã đạt contract test.

## Release-candidate dossier

Mỗi RC lưu dưới `reports/operations/release/<release_id>/` và không chứa secret/raw private payload. Dossier tối thiểu gồm:

- revision ID, UTC timestamp, Python/Node versions và dependency lock digests;
- kết quả backend verify, frontend build, browser smoke, schema contract và package audit;
- DB counts/checksums trước-sau migration rehearsal, DQ result và Gold watermark;
- backup artifact metadata, restore drill result và recovery time;
- systemd unit hash/diff, timer status, health snapshot và alert test;
- LLM queue/provider metrics, budget state và golden-set evaluation;
- 10 golden story IDs đi từ source → evidence → brief → feed;
- known deviations, rollback command/procedure và named human sign-off.

## Acceptance criteria

- [ ] Spec registry không còn hơn một spec `building`, không còn spec mâu thuẫn `approved`, và active spec đúng release wave — verify: `../agent-factory/scripts/find-active-spec.sh . && .venv/bin/python -m unittest tests.test_spec_registry`
- [ ] Dependency graph và gate state được xuất machine-readable, không có cycle hoặc dependency chưa biết — verify: `.venv/bin/python cli.py production-status --check-spec-graph --json >/tmp/reddit-radar-production-status.json`
- [ ] Wave 0 release/recovery baseline đạt toàn bộ P0 tương ứng — verify: `.venv/bin/python cli.py production-check --profile wave0`
- [ ] Medallion, source-ingestion và LLM-control contract tests đạt trên fixture và copy DB thật — verify: `.venv/bin/python cli.py production-check --profile data-ai --db-copy-test`
- [ ] Grounded Social Briefs và Single Feed chạy end-to-end trên golden set — verify: `.venv/bin/python cli.py production-check --profile product --golden-set tests/fixtures/golden_stories.json`
- [ ] Release dossier đầy đủ, parse được và không có secret pattern — verify: `.venv/bin/python cli.py production-check --profile dossier --release-id "$REDDIT_RADAR_RELEASE_ID"`
- [ ] Backup/restore và medallion rollback drill đạt trên copy của DB thật — verify: `.venv/bin/python cli.py production-check --profile recovery --db-copy-test`
- [ ] 72 giờ observation đạt SLO và không có P0 incident mở — verify: `.venv/bin/python cli.py production-check --profile observation --window-hours 72`
- [ ] Dashboard vẫn chỉ listen localhost và API mutating route không xuất hiện — verify: `.venv/bin/python cli.py production-check --profile internal-boundary`
- [ ] Operator phê duyệt cutover và xác nhận các miễn trừ internal-only còn đúng — verify-manual: Product owner/operator: review RC dossier, network binding, open incidents, rollback/restore evidence, rồi ký `go_live_approved_by` và `go_live_approved_at` trong release manifest
- [ ] MediaWorkflow không còn copy Spotify hard-code trước khi bật generic story export — verify-manual: MediaWorkflow owner: render một non-Spotify golden story, kiểm tra caption/frame/audio copy và gắn evidence path vào Reddit Radar release dossier

## Risk tier

Tier: R3 — chương trình bao gồm production data migration, systemd deployment, backup/restore, secret boundaries và live cutover; mọi bước live cần named human.

## Constraints

- Không sửa, truncate, vacuum, re-key hoặc migrate `reddit.db` tại chỗ trong build/test.
- Baseline verify hiện tại (60 unit tests) là regression floor, không phải bằng chứng duy nhất cho production.
- SQLite là lựa chọn chủ đích cho một máy nội bộ; chỉ xem xét PostgreSQL khi đo được contention/capacity vượt ngưỡng.
- Crawl phải tiếp tục độc lập nếu provider AI hết quota hoặc source article lỗi.
- Không feature nào đọc raw JSONL trực tiếp từ request web.
- Không ghi secret, prompt chứa secret, Reddit token hoặc nội dung raw nhạy cảm vào release dossier.
- Mọi command chấp nhận path production phải có `--dry-run` hoặc confirmation gate rõ ràng.
- Status `approved` chỉ được đặt khi Product owner chấp thuận scope, thứ tự và các R3 gate.

## Stop if (only if a run could plausibly overreach)

- Bất kỳ bước nào sắp ghi trực tiếp vào `reddit.db` thay vì copy/shadow path trước cutover được duyệt.
- Free disk xuống dưới capacity gate hoặc backup gần nhất chưa được restore-test.
- DQ hard gate fail, FK/quick check fail, Gold watermark lùi hoặc record count giảm ngoài tolerance.
- Spec/implementation đòi bind web ra ngoài localhost, thêm social autopublish hoặc truy cập credential ngoài scope.
- Dependency lock update kéo major version ngoài spec hoặc package audit xuất hiện high/critical vulnerability chưa xử lý.
- LLM cost/token/request budget không xác định hoặc provider retry storm ảnh hưởng crawl.
- Cần sửa sibling MediaWorkflow để vượt acceptance criterion; dừng và chuyển cho owner repo đó.

## Interfaces (only if criteria depend on each other)

- `specs/production-program.yaml`: `{version, current_wave, specs[], dependencies[], gates[], exemptions[], external_blockers[]}`; spec ID/path/status/owner/risk tier là bắt buộc.
- `ProductionCheckResultV1`: `{profile, release_id, started_at, finished_at, status, checks[], evidence_paths[], blockers[]}`.
- `ReleaseManifestV1`: `{release_id, revision, lock_digests, schema_version, bronze_contract_version, gold_snapshot_id, unit_digests, checks, backup, restore_drill, observation_window, deviations, go_live_approved_by, go_live_approved_at}`.
- `reports/operations/release/<release_id>/`: immutable evidence directory sau sign-off; có `manifest.json` làm entry point.

## Plan (filled at Plan stage)

### WP0 — Reconcile spec registry

- Tạo `specs/production-program.yaml` từ bảng authority ở trên.
- Audit từng acceptance criterion và outcome của spec cũ bằng code/test/runtime evidence.
- Với spec bị thay thế, ghi Outcome “superseded without build” và link spec authority mới; không giả vờ criterion đã ship.
- Chuyển `evidence-to-intelligence-mvp` về đúng status dựa trên phần còn thiếu thực tế.
- Đảm bảo chỉ spec của wave đang làm được đặt `building`.
- Thêm `tests/test_spec_registry.py` để bắt status lạ, hơn một `building`, dependency cycle và approved conflict.

### WP1 — Ship Wave 0 safety baseline

- Thực thi release/ops spec: revision control, lock files, clean bootstrap, CI/local verify parity.
- Ưu tiên backup/restore, disk capacity, file permission và alert trước refactor lớn.
- Chụp baseline database, raw footprint, job SLO, LLM quota/failure và web performance.

### WP2 — Build data foundation

- Thực thi medallion spec theo legacy → shadow → medallion.
- Khóa Bronze/control-plane/Silver/Gold contracts trước khi consumer migration.
- Chỉ migrate read path khi parity và recovery drill đạt.

### WP3 — Build evidence và AI control planes

- Thực thi safe source-content ingestion và LLM workload-control song song.
- Tắt article body fetch bằng feature flag đến khi security tests xanh.
- Đưa mọi AI feature vào cùng queue/budget/lease semantics.

### WP4 — Build product layer

- Thực thi Grounded Social Briefs bằng golden stories trước, sau đó mở batch giới hạn.
- Thực thi Single Intelligence Feed; giữ compatibility routes trong rollout window.
- Đo accessibility, navigation state, scroll recovery và client performance.

### WP5 — Release candidate và production observation

- Tạo RC từ clean checkout/environment, chạy production-check profiles và lập dossier.
- Rehearse migration/restore/rollback trên copy mới nhất của DB thật.
- Named operator duyệt cutover; bật feature flags theo canary/shadow order.
- Quan sát 72 giờ, xử lý/ghi deviation, rồi mới đổi nhãn internal-production-ready.

### WP6 — External export closure

- Chuyển MediaWorkflow hard-coded Spotify copy thành ticket/spec ở repo owner.
- Chỉ bỏ publish gate khi render non-Spotify golden story qua toàn flow và có manual evidence.

## Decisions log (append during Build)

- 2026-07-28 — Giữ monolith modular + SQLite trên một máy; đây là mức đúng cho internal production hiện tại.
- 2026-07-28 — Medallion được triển khai “lite” bằng file/SQLite contracts và atomic publish, không đưa distributed data platform vào khi chưa có nhu cầu đo được.
- 2026-07-28 — Một social-style feed là product surface chính; raw signal vẫn truy cập được như evidence tier, không là UI ngang hàng.
- 2026-07-28 — Nội dung AI sinh offline rồi lưu/publish có gate; request web không gọi LLM và không tự fetch URL.
- 2026-07-28 — Public-product controls được miễn có điều kiện, nhưng recovery, reproducibility, DQ, secrets và observability không được miễn.

## Outcome (filled at Ship)

Chưa build. Khi ship, ghi từng wave/spec revision, P0/P1 gate evidence, RC dossier path, restore/rollback result, observation window và mọi deviation còn lại.
