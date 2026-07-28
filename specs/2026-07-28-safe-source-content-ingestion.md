# Spec: Safe source-content ingestion và provenance

- Date: 2026-07-28
- Status: approved

## Problem

Radar hiện lưu URL/resources từ Reddit và schema còn `fact_article_content`, nhưng production audit không có article thành công và fetch đã bị vô hiệu vì chưa đủ guard. Chỉ dùng title, selftext và comment sẽ làm các bài social về model, benchmark, paper hoặc announcement thiếu technical report gốc; số liệu dễ bị lặp lại từ lời kể thứ cấp. Ngược lại, bật HTTP fetch ngây thơ tạo SSRF, redirect-to-private-IP, payload quá lớn, decompression bomb, content-type giả, prompt injection và rủi ro web request bị treo. Source content phải là một pipeline offline có allow policy, provenance, raw/normalized separation và failure semantics rõ ràng.

## Goal

Cho phép collector/enrichment worker lấy và chuẩn hóa nội dung URL công khai được Reddit post/resource trỏ tới, an toàn cho một máy nội bộ, để:

- technical report/article/documentation chính thức trở thành evidence có provenance;
- mọi redirect/hop/IP/payload được kiểm tra trước và trong khi tải;
- raw response metadata/body hợp lệ đi vào Bronze, extracted text đi vào Silver;
- claim/brief có thể trỏ đến canonical source, content hash và đoạn evidence;
- lỗi fetch không chặn crawl Reddit, không retry storm và không bao giờ chạy trong web request;
- feature mặc định off đến khi toàn bộ security/DQ tests và live canary gate đạt.

## Non-goals

- Không crawl toàn website, sitemap hoặc link graph nhiều cấp.
- Không bypass paywall, login, CAPTCHA, anti-bot hoặc robots restriction.
- Không chạy JavaScript/headless browser trong release đầu.
- Không tải executable, archive, media/video hoặc model weights.
- Không coi nội dung fetched là sự thật chỉ vì lấy được qua HTTPS.
- Không cho frontend/API tự fetch URL theo input của user.
- Không hỗ trợ URL tùy ý ngoài candidate được sinh từ dữ liệu đã ingest và policy cho phép.

## Source selection policy

Candidate chỉ đến từ URL canonicalized trong `fact_post.url`, selftext hoặc `fact_extracted_resource`; mỗi candidate phải liên kết ít nhất một `source_event_id`/post. Ưu tiên:

1. official announcement, product docs, research report/paper;
2. repository/release notes của owner;
3. reputable technical publication;
4. community article nếu là nguồn duy nhất, được gắn `source_authority=community`.

Không fetch Reddit permalink bằng worker này; Reddit content đã thuộc source adapter. Không follow link tìm thấy trong article. Domain/URL có explicit deny hoặc policy failure được lưu trạng thái, không xóa dấu vết quyết định.

## Network safety policy

### URL validation

- Chỉ `https`; `http` chỉ cho phép nếu policy explicit nâng cấp/allow và vẫn kiểm toàn bộ hop.
- Cấm userinfo, fragment, malformed/ambiguous hostname, non-ASCII chưa IDNA-normalize, URL dài quá limit và port ngoài 80/443.
- Resolve DNS ngay trước connect; từ chối loopback, link-local, private, carrier-grade NAT, multicast, reserved, documentation/test ranges, unspecified và Unix/socket schemes cho cả IPv4/IPv6.
- Kết nối phải bind đến tập IP đã validate; không resolve lại ngầm theo cách cho phép DNS rebinding.
- Mỗi redirect được resolve và validate lại từ đầu; tối đa 3 hop; không tự gửi credential/header nhạy cảm sang host mới.
- Proxy từ environment bị tắt mặc định (`trust_env=false`) trừ cấu hình operator đã audit.

### Transfer limits

- connect timeout 5s, read timeout 15s, total deadline 30s; configurable nhưng capped.
- Stream response; dừng khi compressed hoặc decompressed bytes vượt limit. Baseline: 5 MiB wire, 10 MiB decoded, 200k extracted characters.
- Chỉ accept allowlisted MIME: HTML/XHTML, plain text, Markdown và PDF nếu PDF adapter được bật riêng với page/size guard. Release đầu có thể ship HTML/text trước.
- Kiểm MIME header lẫn magic/sniff tối thiểu; mismatch thành failure/quarantine.
- Không lưu cookie; User-Agent định danh project/operator contact theo chính sách, không giả browser.
- Rate limit per-domain, global concurrency nhỏ và honor `Retry-After` có cap.

### Robots và legal/operational policy

- Fetch `robots.txt` qua cùng network guard, cache có TTL và fail-closed cho domain chưa có quyết định; policy/decision được lưu.
- Không bypass site block; 401/403/429 là terminal/deferred theo policy, không xoay UA/proxy.
- Domain owner/operator có denylist override ngay lập tức.
- Trước live enable, operator xác nhận User-Agent, source policies và điều khoản của nguồn được chọn tại thời điểm đó.

## Data placement và contracts

### Bronze `source_content_fetch.v1`

Bronze event lưu request/response metadata, redirect chain, validation decision, status, content hash và raw body chỉ khi response pass policy. Không lưu Authorization/Cookie hoặc secret headers. Failed/rejected attempts vẫn có event metadata nhưng không có unsafe body.

### Silver `silver_source_document.v1`

Transformer deterministic tạo:

- normalized/canonical URL và domain;
- title, author, published/updated time nếu có confidence;
- cleaned text, language, headings và bounded excerpts;
- extractor name/version, raw hash, normalized text hash;
- source authority, fetch time và freshness state;
- `content_safety_labels` cho prompt injection/untrusted instructions;
- provenance về Bronze event và originating Reddit posts/resources.

Nội dung fetched luôn được đánh dấu untrusted data khi đưa vào LLM. Các câu kiểu “ignore previous instructions”, tool call, credential request không được thực thi và được giữ như dữ liệu/evidence nếu cần.

### Enrichment evidence

Chunk evidence có stable `evidence_id`, document ID, character offsets hoặc section locator, exact bounded quote, paraphrase-safe context và source URL. Claim resolver chỉ dùng version document đã chỉ định; refresh không âm thầm đổi evidence của brief đã publish.

### Legacy table

`fact_article_content` hiện tại không còn là source authority. Dữ liệu legacy được import vào Bronze/Silver qua one-time importer trên copy DB, hoặc giữ read-only đến hết compatibility window. Không viết dual-path vô hạn.

## State machine

```text
discovered -> policy_pending -> queued -> leased -> fetching
fetching -> succeeded -> extracted -> dq_passed
fetching -> rejected_policy | deferred | failed_retryable | failed_terminal
extracted -> dq_failed | dq_passed
failed_retryable/deferred -> queued  (sau next_attempt_at, trong max attempts/budget)
```

Idempotency key gồm canonical URL + policy version + fetch representation bucket. Lease có expiry/owner; crash không giữ job vĩnh viễn. Refresh tạo version document mới, không overwrite provenance cũ.

## Quality và freshness rules

- Extraction fail nếu text rỗng/quá ngắn so với content type, body hash mismatch hoặc encoding không quyết định được.
- Duplicate document theo normalized content hash được dedupe nhưng giữ mọi source edge.
- Canonical tag từ page chỉ được nhận nếu URL đó pass cùng network policy.
- `published_at` không được suy đoán từ fetched time; unknown là null.
- Documents có source authority thấp hoặc stale vẫn phục vụ evidence nhưng quality flag rõ ràng.
- Social brief có số liệu quan trọng nên ưu tiên official document; nếu chỉ có community source phải diễn đạt attribution/uncertainty.

## Acceptance criteria

- [ ] URL validator từ chối toàn bộ private/reserved IPv4/IPv6, unsafe scheme/port/userinfo và ambiguous host corpus — verify: `.venv/bin/python -m unittest tests.test_source_url_policy`
- [ ] DNS pinning/rebinding và mỗi redirect hop đều được validate, tối đa 3 hop — verify: `.venv/bin/python -m unittest tests.test_source_network_guard`
- [ ] Streaming fetch áp connect/read/total timeout, wire/decoded size và MIME/magic limits — verify: `.venv/bin/python -m unittest tests.test_source_transfer_limits`
- [ ] Proxy env, cookies và secret headers không rò sang request/redirect/log — verify: `.venv/bin/python -m unittest tests.test_source_request_privacy`
- [ ] Robots, per-domain rate limit, Retry-After và deny override chạy deterministic với fake clock — verify: `.venv/bin/python -m unittest tests.test_source_fetch_policy`
- [ ] Bronze event redacts headers, hash đúng, chỉ lưu body khi pass policy và replay deterministic — verify: `.venv/bin/python -m unittest tests.test_source_bronze_contract`
- [ ] Silver extraction có version/hash/provenance và prompt-injection labels; không thực thi instruction trong source — verify: `.venv/bin/python -m unittest tests.test_source_document_transform`
- [ ] Duplicate content dedupe nhưng source edges không mất; refresh tạo version mới — verify: `.venv/bin/python -m unittest tests.test_source_document_versioning`
- [ ] Fetch queue có lease/idempotency/retry terminal semantics và crash recovery — verify: `.venv/bin/python -m unittest tests.test_source_fetch_queue`
- [ ] Web/API code path không import/call network fetcher và arbitrary URL endpoint không tồn tại — verify: `.venv/bin/python -m unittest tests.test_web_no_source_fetch`
- [ ] Legacy article importer đạt count/hash/parity trên copy của `reddit.db` thật, không ghi live DB — verify: `.venv/bin/python cli.py source-content import-legacy --db-copy-test --verify-only`
- [ ] Canary 20 domain/URL đại diện có 0 policy bypass, extraction/DQ report và không ảnh hưởng crawl SLO — verify: `.venv/bin/python cli.py source-content canary-report --latest --require-pass`
- [ ] Operator phê duyệt User-Agent, domain policy, robots behavior và canary trước khi bật feature flag — verify-manual: Operator: review request metadata, source policy version, rejected cases and canary report; record approval in release dossier

## Risk tier

Tier: R3 — worker truy cập Internet từ máy production và lưu external content; SSRF/data/provenance risks yêu cầu live canary và human gate.

## Constraints

- Feature flag `SOURCE_CONTENT_FETCH_ENABLED=false` mặc định đến manual gate.
- Không network call trong default unit tests; dùng fake resolver/transport và local isolated test server không bind public interface.
- Không fetch trong FastAPI request lifecycle.
- Raw body chỉ vào Bronze storage; web không đọc trực tiếp.
- Tất cả schema migration/replay/import chạy trên copy DB thật trước live.
- Fetch failure không làm crawl Reddit fail hoặc trì hoãn checkpoint.
- Quote lưu cho evidence phải bounded; không biến hệ thống thành kho sao chép nguyên bài để hiển thị lại.
- Không expose internal IP resolution detail hoặc rejected raw body qua public API/UI.

## Stop if (only if a run could plausibly overreach)

- Transport không thể pin/connect đúng IP đã validate hoặc library tự follow redirect không hook được mỗi hop.
- Một test vector chạm localhost/private/reserved address hoặc environment proxy ngoài fake transport.
- Payload limit được áp sau khi body đã load toàn bộ vào memory/disk.
- Robots/terms decision chưa có nhưng task chuẩn bị bật live fetch.
- Cần headless browser, login, cookie persistence, CAPTCHA bypass hoặc executable/PDF parsing ngoài scope đã duyệt.
- Legacy importer resolve target là live `reddit.db`.

## Interfaces (only if criteria depend on each other)

- `SourceFetchCandidateV1`: `{candidate_id, canonical_url, discovered_from[], source_authority, priority, policy_version, created_at}`.
- `NetworkDecisionV1`: `{url, normalized_host, resolved_ips_redacted, scheme, port, decision, reason_codes[], policy_version, decided_at}`.
- `SourceContentFetchV1`: `{fetch_id, candidate_id, request_url, redirect_chain[], final_url, status, http_status, mime, wire_bytes, decoded_bytes, body_sha256, bronze_uri, error_code, fetched_at, policy_version}`.
- `SilverSourceDocumentV1`: `{document_id, version, canonical_url, title, author, published_at, language, text, headings[], raw_sha256, text_sha256, extractor, extractor_version, authority, safety_labels[], provenance[]}`.
- `SourceEvidenceChunkV1`: `{evidence_id, document_id, version, locator, quote, context, source_url, authority}`.
- Feature flags: `SOURCE_CONTENT_FETCH_ENABLED`, `SOURCE_PDF_ENABLED`; default false.

## Plan (filled at Plan stage)

### WP0 — Threat model và policy corpus

- Viết threat model gồm SSRF, DNS rebinding, redirects, proxy env, payload bomb, prompt injection và poisoned canonical URL.
- Tạo table-driven corpus IPv4/IPv6/IDNA/redirect/MIME/size cases trước implementation.
- Chốt User-Agent, limits, robots fail behavior, allow/deny override và operator contact.

### WP1 — Contracts và control plane

- Thêm versioned Bronze/Silver schemas và migrations theo medallion conventions.
- Implement candidate/state/lease/idempotency repository, không network trước khi queue tests xanh.
- Thêm structured failure reasons và metrics.

### WP2 — Network guard và bounded transport

- Implement URL parser/canonicalizer, resolver classifier và pinned transport.
- Disable automatic redirect/proxy/cookie behavior; tự xử lý từng hop.
- Stream với deadlines/limits/MIME checks; sanitize logs.
- Fuzz/table-test policy corpus và local fake server.

### WP3 — Extraction và provenance

- Bọc trafilatura bằng deterministic adapter/version; HTML/text trước.
- Tạo Silver document, dedupe/version và evidence chunks.
- Label untrusted/prompt-injection patterns; LLM bundle renderer dùng explicit data delimiters.
- Chưa bật PDF đến khi adapter riêng đạt limits/tests.

### WP4 — Legacy compatibility và consumers

- Import legacy table trên DB copy, đo parity và quyết định discard/quarantine những row không đủ provenance.
- Cập nhật enrichment/brief evidence bundle đọc SilverSourceDocument qua repository.
- Đảm bảo web chỉ nhận projection/evidence metadata, không raw body.

### WP5 — Canary và rollout

- Chạy dry policy evaluation trên candidate backlog không fetch.
- Canary bounded 20 URL/domain đại diện, xem rejected/failed/extraction/DQ/crawl impact.
- Manual approve rồi bật batch nhỏ; circuit-breaker về off khi policy anomaly.
- Ghi canary/rollout evidence vào RC dossier.

## Decisions log (append during Build)

- 2026-07-28 — Product owner approved this authoritative spec for bounded WorkForge
  packages. Automated tests stay fixture-only and live article fetching remains off
  until its security gates pass.
- 2026-07-28 — Article/technical report là enrichment source, không phải web-time fetch và không phải nguồn crawl rộng độc lập.
- 2026-07-28 — HTTPS không đủ chứng minh an toàn; IP/redirect/size/MIME validation là mandatory P0.
- 2026-07-28 — Source body là untrusted data, kể cả official domain; prompt instructions trong body không có quyền điều khiển agent/model.
- 2026-07-28 — HTML/text ship trước; PDF/headless browser chỉ thêm bằng criterion riêng.

## Outcome (filled at Ship)

Chưa build. Khi ship, ghi policy version, test corpus size, canary domains/results, enabled adapters, legacy import decision và live feature-flag state.
