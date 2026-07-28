# Spec: LLM workload control, quota resilience và quality evaluation

- Date: 2026-07-28
- Status: draft

## Problem

AI enrichment hiện đã tạo hơn một nghìn phân tích, nhưng production audit thấy Gemini backlog có nhiều lỗi 429/quota trong batch gần nhất và hàng nghìn item còn chờ. Grounded Social Briefs sẽ thêm một workload đắt hơn; nếu mỗi job tự retry/budget theo cách riêng, backlog có thể chiếm hết quota, current trends bị trễ, nhiều worker xử lý trùng, hoặc crawl/report bị ảnh hưởng. Đồng thời “model trả JSON hợp schema” chưa chứng minh bài viết đúng: cần golden set, claim grounding, deterministic renderer và release threshold riêng cho từng prompt/model/provider version.

## Goal

Tạo một control plane chung cho mọi LLM workload offline của Radar:

- queue durable có priority, lease, idempotency, dependency và dead-letter semantics;
- budget theo provider/model/workload/time window, tôn trọng Retry-After và circuit breaker;
- current/high-value story được phục vụ trước historical backlog mà không starvation;
- provider failure không chặn crawl, Silver/Gold publish không phụ thuộc AI bắt buộc;
- mọi output gắn model/prompt/input/evidence/version/cost lineage;
- golden-set evaluation và runtime quality gate quyết định output `ready`, không dùng cảm giác;
- operator nhìn được throughput, quota state, cost/token estimate, failure taxonomy và ETA backlog.

## Non-goals

- Không gọi LLM trong FastAPI request.
- Không tự mua quota, đổi billing plan, tạo API key hoặc gửi dữ liệu sang provider mới.
- Không xây generic distributed workflow engine.
- Không fine-tune model trong release đầu.
- Không tự động publish social post ra ngoài.
- Không dùng local heuristic output làm social brief `ready`; fallback chỉ có thể là `draft/insufficient_evidence`.

## Workload classes và priority

| Class | Ví dụ | Priority | Latency target | Khi quota thấp |
|---|---|---:|---|---|
| `interactive_internal` | operator re-run một story | 100 | best effort <5 phút | quota riêng nhỏ; không vượt hard cap |
| `current_brief` | top story cần social brief/feed | 80 | <30 phút | giữ, giảm batch |
| `current_analysis` | V2 analysis top signal | 70 | <60 phút | giữ bounded |
| `digest` | 3h/day digest | 60 | theo schedule | coalesce cùng period |
| `refresh` | output stale sau source update | 40 | <24 giờ | defer |
| `historical_backlog` | Gemini backlog cũ | 10 | không SLO cứng | pause trước tiên |

Scheduler dùng weighted fairness: ưu tiên cao nhưng dành tối đa một tỷ lệ nhỏ cho backlog khi circuit closed và budget còn, tránh starvation. Một story không chạy brief trước khi dependencies `evidence_bundle_ready` và `analysis_quality_passed` đạt.

## Queue và execution semantics

`LLMJobV1` có idempotency key từ workload + subject + input/evidence/prompt/model policy hashes. State machine:

```text
pending -> ready -> leased -> running -> succeeded -> quality_check
quality_check -> ready_to_publish | rejected_quality
leased/running -> retry_wait | dead_letter | cancelled
retry_wait -> ready
```

- Claim lease atomic, có owner/expiry/heartbeat; stale lease được requeue an toàn.
- Result write và job completion idempotent; duplicate response không tạo hai published brief.
- Retry chỉ cho taxonomy retryable; 400/schema/policy/grounding failure không retry mù cùng input.
- Exponential backoff có jitter, honor `Retry-After`, max attempts và max wall-clock age.
- Dependency/cancellation được lưu; source/evidence version mới có thể supersede job cũ chưa chạy.
- Payload DB chỉ giữ references/hashes; evidence bundle immutable lưu theo data contract, không nhét secret.

## Budget và circuit breaker

Budget config versioned, mặc định fail-closed khi không parse được. Mỗi provider có:

- requests/minute, tokens/minute nếu biết;
- daily request/token/cost ceiling và reserve cho `current_brief`/`interactive_internal`;
- concurrency cap;
- per-workload max input/output tokens và batch size;
- optional monthly soft alert; không tự charge/purchase.

Circuit breaker mở theo provider/model khi gặp quota/rate-limit/auth/service error threshold trong rolling window. Khi mở:

- không claim job mới cho provider đó;
- `Retry-After`/cooldown quyết định half-open probe duy nhất;
- crawl, source ingest và non-AI Gold publish tiếp tục;
- historical backlog pause trước current workloads;
- auth/invalid-key là terminal/operator alert, không probe liên tục.

Provider fallback chỉ được dùng nếu workload policy cho phép và data-sharing/model quality đã được operator phê duyệt. Không tự chuyển OpenAI ↔ Gemini chỉ vì lỗi.

## Output lineage và caching

Mỗi attempt/result lưu:

- provider/model/API mode;
- prompt template ID + hash, schema version, decoding params;
- input bundle/evidence version + SHA-256;
- started/finished times, token usage/cost estimate nếu provider trả;
- raw response reference có retention/redaction policy;
- sanitizer/validator versions, quality result/reason codes;
- supersedes/result status/published snapshot.

Exact cache hit chỉ hợp lệ khi workload, subject, prompt, model policy, input/evidence và schema hashes giống nhau. Không cache theo title đơn thuần.

## Quality evaluation

### Offline golden set

Golden set tối thiểu 30 story đại diện: announcement/model, benchmark claim, security, opinion/community, link thiếu body, conflicting comments, Vietnamese/English mixed, low evidence và malicious prompt text. Mỗi item có expected invariants thay vì ép một câu chữ:

- claim nào được phép, evidence IDs tối thiểu;
- con số/tên/version/URL chính xác;
- uncertainty/attribution bắt buộc;
- prohibited fabricated claims/URLs;
- expected decision `ready|draft|insufficient_evidence`;
- style/length/structure constraints cho social brief.

Automated metrics gồm schema pass, URL grounding, numeric grounding, claim-evidence coverage, unsupported-claim rate, citation validity, deterministic renderer snapshot và latency/token budget. Human rubric chấm faithfulness, completeness, nuance, readability và “30 giây nắm toàn bộ thông tin”.

Release threshold P0:

- 100% schema/safety/URL/numeric hard checks;
- 0 fabricated critical claim/URL trên golden set;
- ≥95% claim-evidence coverage cho output `ready`;
- 100% insufficient-evidence cases không được auto-ready;
- human rubric trung bình ≥4/5, không item nào faithfulness <3;
- candidate prompt/model không regress quá tolerance so với approved baseline.

### Runtime quality gate

Runtime validator deterministic chạy trước publish. Hard fail chuyển `rejected_quality`/draft, không retry cùng prompt vô hạn. Sample ready outputs được operator review theo rate cấu hình trong canary/observation; feedback là structured label, không sửa trực tiếp provenance.

## Acceptance criteria

- [ ] Queue claim/lease/heartbeat/recovery/idempotent-completion đúng dưới concurrent workers và fake crash — verify: `.venv/bin/python -m unittest tests.test_llm_job_queue`
- [ ] Priority/dependency/weighted-fair scheduler ưu tiên current story nhưng không starvation backlog — verify: `.venv/bin/python -m unittest tests.test_llm_scheduler`
- [ ] Retry taxonomy, Retry-After, jitter, max-attempt/age và dead-letter deterministic với fake clock — verify: `.venv/bin/python -m unittest tests.test_llm_retry_policy`
- [ ] Budget reserve/caps không oversubscribe dưới concurrent claim; invalid config fail-closed — verify: `.venv/bin/python -m unittest tests.test_llm_budget`
- [ ] Circuit breaker open/half-open/close đúng, auth failure không retry storm và crawl không phụ thuộc breaker — verify: `.venv/bin/python -m unittest tests.test_llm_circuit_breaker tests.test_pipeline_ai_isolation`
- [ ] Provider fallback mặc định off và chỉ chạy khi policy/version/approval fixture cho phép — verify: `.venv/bin/python -m unittest tests.test_llm_provider_policy`
- [ ] Result lineage chứa prompt/model/input/evidence/schema hashes và cache không hit khi một hash đổi — verify: `.venv/bin/python -m unittest tests.test_llm_lineage_cache`
- [ ] Golden-set evaluator kiểm schema, URL, number, claims/evidence, decision và renderer snapshots — verify: `.venv/bin/python cli.py llm-eval --suite tests/fixtures/llm_golden_set --offline-fixtures --require-threshold`
- [ ] Candidate provider/model/prompt comparison sinh report và chặn regression ngoài tolerance — verify: `.venv/bin/python cli.py llm-eval compare --baseline tests/fixtures/llm_baseline.json --candidate tests/fixtures/llm_candidate.json --require-no-regression`
- [ ] Runtime hard validator không cho fabricated URL/critical number/unsupported claim hoặc insufficient evidence thành `ready` — verify: `.venv/bin/python -m unittest tests.test_llm_runtime_quality_gate`
- [ ] Ops status báo queue depth/age/throughput/failure taxonomy/budget/breaker theo workload mà không lộ prompt/secret — verify: `.venv/bin/python cli.py llm-status --json --redact | .venv/bin/python -m json.tool >/dev/null`
- [ ] Rehearsal từ dữ liệu production copy chứng minh 429 burst không tăng request storm, backlog hiện tại được import/dedupe và crawl SLO không đổi — verify: `.venv/bin/python cli.py llm-control rehearse --db-copy-test --scenario tests/fixtures/provider_429_burst.json --require-pass`
- [ ] Operator phê duyệt budget, providers/data policy, baseline prompt/model và golden human rubric — verify-manual: Product owner/operator: review budget config, provider terms/data boundary, eval report and 30-item rubric; record approved versions in release manifest

## Risk tier

Tier: R3 — sử dụng paid/external AI APIs, production content và quota/cost controls; live enable/model/provider change cần human approval.

## Constraints

- Không log API key, Authorization header hoặc raw `.env`.
- Default tests/eval dùng recorded/redacted fixtures; live eval cần explicit flag và budget preview.
- Crawl/checkpoint/Silver transform không phụ thuộc provider AI.
- Web request không submit/chờ LLM job; operator action chỉ enqueue offline job.
- Budget config có hard upper bounds trong code để lỗi đơn vị không tạo chi phí bất ngờ.
- Existing AI records được import/lineage-backfill trên DB copy; unknown fields để null/legacy, không bịa metadata.
- Không đổi provider/model/prompt baseline âm thầm trong dependency update.
- Raw provider responses có retention và không được trả trực tiếp qua web.

## Stop if (only if a run could plausibly overreach)

- Task cần tạo key, nâng quota, bật billing hoặc gửi data sang provider chưa approved.
- Budget/cost unit không xác định hoặc dry-run estimate vượt hard cap.
- Rehearsal target là live DB hoặc worker live có thể claim cùng queue test.
- 429/auth burst vẫn tạo retry storm hoặc làm crawl/report non-AI fail.
- Golden set có fabricated critical claim/URL, insufficient case auto-ready hoặc human faithfulness dưới threshold.
- Provider fallback được đề xuất mà chưa audit data-sharing và quality policy.

## Interfaces (only if criteria depend on each other)

- `LLMJobV1`: `{job_id, workload, subject_type, subject_id, priority, dependencies[], state, idempotency_key, input_ref, input_sha256, evidence_version, prompt_id, prompt_sha256, model_policy_id, attempts, next_attempt_at, lease_owner, lease_expires_at, created_at, superseded_by}`.
- `LLMAttemptV1`: `{attempt_id, job_id, provider, model, started_at, finished_at, status, error_class, retry_after, input_tokens, output_tokens, cost_estimate, response_ref, response_sha256}`.
- `LLMResultV1`: `{result_id, job_id, schema_version, output_ref, output_sha256, sanitizer_version, validator_version, quality_status, quality_reasons[], ready_at, published_snapshot_id}`.
- `LLMBudgetPolicyV1`: `{version, provider, model_pattern, windows[], concurrency, workload_limits{}, reserves{}, hard_caps{}, approved_by, approved_at}`.
- `LLMEvaluationReportV1`: `{suite_version, candidate, baseline, automated_metrics, human_rubric, regressions[], threshold_status, artifact_paths[]}`.

## Plan (filled at Plan stage)

### WP0 — Baseline và policy

- Đo queue depth/age/success/error taxonomy/429 rate/duration hiện tại theo workload/provider.
- Chốt workload priorities, concurrency, daily hard cap/reserve và named operator.
- Xây 30-item golden set từ production copy đã redact; version expected invariants/rubric.

### WP1 — Queue/control contracts

- Thêm migrations/tables qua medallion control-plane conventions.
- Implement repository atomic claim/lease/complete/supersede/dependencies.
- Import legacy Gemini backlog/analysis state trên DB copy và dedupe bằng hashes khả dụng.

### WP2 — Budget, retry và breaker

- Centralize provider adapters error taxonomy/usage metadata.
- Implement transactional budget reservation/reconciliation, retry planner và circuit breaker.
- Tách worker pool khỏi crawl; thêm systemd timer/service bounded theo spec ops.
- Simulate quota/auth/network/schema bursts bằng fake clock/transport.

### WP3 — Lineage/cache và quality gate

- Persist prompt/model/input/evidence/schema hashes và redacted response references.
- Implement exact cache semantics, sanitizer/runtime validator và publish decision.
- Không cho legacy/unknown lineage result tự động trở thành social brief `ready`.

### WP4 — Evaluation harness

- Implement offline evaluator metrics/snapshots và baseline/candidate comparison.
- Chạy human rubric workflow, lưu reviewer/version nhưng không chứa secret/raw private body.
- Đưa `llm-eval --require-threshold` vào RC gate, không nhất thiết vào fast verify hàng ngày.

### WP5 — Observability và rollout

- Thêm `llm-status`, metrics/alerts và `/api/health` aggregate redacted.
- Rehearse import/429 burst trên DB copy; canary current workloads với budget thấp.
- Pause historical backlog, quan sát current latency/quality/cost, rồi mở weighted drain từ từ.
- Ghi approved budget/model/prompt/eval hashes vào release manifest.

## Decisions log (append during Build)

- 2026-07-28 — Quota là shared production resource; không để từng job tự retry và tự quyết budget.
- 2026-07-28 — Current intelligence ưu tiên hơn lịch sử, nhưng weighted fairness ngăn backlog bị bỏ vĩnh viễn.
- 2026-07-28 — Output schema-valid chưa đủ; claim/evidence/number/URL grounding và human faithfulness là release gate.
- 2026-07-28 — Provider fallback là policy opt-in, không phải phản xạ tự động khi 429.

## Outcome (filled at Ship)

Chưa build. Khi ship, ghi baseline vs post-rollout throughput/429, budget/model/prompt versions, golden-set metrics/rubric, imported backlog counts và live breaker/queue state.
