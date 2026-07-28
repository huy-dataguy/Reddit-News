# Spec: Evidence-to-Intelligence MVP

- Date: 2026-07-26
- Status: blocked

> 2026-07-28: Tạm dừng auto-resume để reconcile AC1–AC10 với Outcome/runtime hiện
> tại. Các phần còn thiếu được chuyển quyền cho program production; không tiếp tục
> build spec này song song với `2026-07-28-internal-production-program.md`.

## Problem

Reddit Radar thu thập post và metrics liên tục nhưng chuỗi tạo tri thức đang đứt:
comment/article enrichment, resource mining, batch analysis, digest và report không
chạy vì systemd gọi các CLI command đã bị xoá. Web hiển thị các bản local extract
như Gemini analysis, V1/V2 tách rời, filter không hoạt động và người dùng vẫn phải
đọc từng post. Sản phẩm chưa đạt North Star: tự khai phá bằng chứng rồi đúc kết
thành tri thức tiếng Việt có nguồn, cảnh báo và hành động cụ thể.

## Goal

Ship một sản phẩm Reddit-first chạy end-to-end không cần thao tác từng bài:

1. Crawl tiếp tục thu thập signal và metrics.
2. Pipeline tự chọn signal giá trị cao, lấy comment còn thiếu, dùng selftext/
   article text đã lưu, bóc tách resource, tạo PostAnalysis V2 grounded và tổng
   hợp briefing theo chu kỳ.
3. Mọi bề mặt (API, web, report, story export) dùng cùng một analysis contract;
   local fallback được ghi nhãn trung thực và không giả làm LLM.
4. Web mặc định trả lời “hôm nay có gì mới, vì sao đáng quan tâm, nên làm gì”,
   còn raw signal là bề mặt kiểm chứng thứ cấp.
5. Health phản ánh freshness và trạng thái từng tầng, không chỉ việc DB mở được.

## Non-goals

- Chưa thêm X/Twitter, ArXiv hay RSS crawler; hoàn thiện Reddit trước.
- Chưa xây vector database, RAG chat hoặc authentication/multi-user.
- Không thay đổi crawl high-water mark/backfill logic.
- Không migrate hoặc reset `reddit.db` sống tại chỗ. Mọi kiểm tra schema/storage
  chạy trên bản copy; live chỉ nhận các ghi dữ liệu thông thường sau khi verify.
- Không xoá backup hay dữ liệu lịch sử.

## Acceptance criteria

- [ ] AC1 — CLI có `enrich`, `analyze-top`, `ai-digest`, `report`, `pipeline`; `analyze-post` dùng V2 mặc định — verify: `.venv/bin/python cli.py --help && .venv/bin/python cli.py analyze-post --help`
- [ ] AC2 — Systemd unit chỉ gọi command/module tồn tại và verify sạch — verify: `systemd-analyze verify deploy/systemd/*.service deploy/systemd/*.timer`
- [ ] AC3 — V2 contract có domain chuẩn, evidence IDs hợp lệ, URL không có trong input bị loại; local fallback không giả tiếng Việt/LLM — verify: `.venv/bin/python -m unittest tests.test_llm`
- [ ] AC4 — Resource extraction ghi được vào schema hiện tại và pipeline storage chạy trên copy của DB thật — verify: `.venv/bin/python -m unittest tests.test_resources tests.test_pipeline`
- [ ] AC5 — API feed/trending/detail/story-export thống nhất ưu tiên V2, health trả pipeline counts/freshness, không có read endpoint âm thầm crawl/ghi — verify: `.venv/bin/python -m unittest tests.test_web_api tests.test_export_story`
- [ ] AC6 — Frontend có ba bề mặt `Hôm nay`, `Kho tri thức`, `Radar`; filter/search hoạt động, provider local không hiện Gemini, mobile không tràn ngang — verify: `npm --prefix web/frontend run build`; Codex kiểm tra thủ công bằng Chrome ở 1440px + 390px và lưu screenshot trong `/tmp`
- [ ] AC7 — Toàn bộ baseline xanh — verify: `.venv/bin/python -m compileall -q reddit_crawler jobs web cli.py && .venv/bin/python -m unittest discover -s tests`
- [ ] AC8 — Pipeline chạy thật có giới hạn và chính run này tạo ít nhất một V2 LLM analysis + một digest đúng period trên DB sống mà không làm hỏng collector — verify: ghi timestamp ngay trước `.venv/bin/python cli.py pipeline --period day --enrich-limit 5 --analysis-limit 3 --provider auto`, rồi query read-only xác nhận `generated_at >= timestamp`, provider thuộc `gemini/openai`, digest `period='day'`.
- [ ] AC9 — Web/service sống hoạt động; enrich/AI/report lần gần nhất thành công, `/api/health` là healthy và UI tải được — verify: `systemctl --user is-active --quiet reddit-web.service && test "$(systemctl --user show -p Result --value reddit-enrich.service)" = success && test "$(systemctl --user show -p Result --value reddit-ai@3h.service)" = success && test "$(systemctl --user show -p Result --value reddit-report@3h.service)" = success && curl -fsS http://127.0.0.1:8080/api/health`
- [ ] AC10 — Story export chọn được V2 analysis để nuôi MediaWorkflow — verify: `.venv/bin/python cli.py story-export --top --dry-check`
- [x] AC11 — Backlog mode duyệt qua toàn bộ post thay vì kẹt ở top-N: lấy
  comment còn thiếu theo batch, chỉ gửi discussion có comment cho Gemini, và bỏ
  qua mọi post đã có V2 Gemini success — verify: `.venv/bin/python -m unittest tests.test_gemini_backlog`
- [x] AC12 — Gemini backlog không dùng OpenAI/local fallback; mỗi run có cost
  ceiling, lỗi có retry backoff và được xuất thành manual-review JSON/Markdown
  để Codex/người vận hành xử lý — verify: `.venv/bin/python -m unittest tests.test_gemini_backlog && systemd-analyze verify deploy/systemd/*.service deploy/systemd/*.timer`
- [x] AC13 — Lịch backlog chống chạy chồng và batch live đầu tiên tạo thêm V2
  `provider='gemini'` mà không DDL/migrate DB sống — verify: chạy service một lần,
  kiểm tra `systemctl --user show -p Result --value reddit-gemini-backlog.service`,
  query read-only số Gemini success tăng và `PRAGMA quick_check` trả `ok`.

## Constraints

- `.env` có credentials thật: không in giá trị và không đưa vào diff/log.
- API LLM có chi phí/quota: user đã cho phép xử lý toàn backlog bằng Gemini,
  nhưng mỗi run vẫn phải có hard limit; không được biến lỗi provider thành số lần
  gọi không giới hạn.
- `reddit.db` có collector ghi đồng thời: query đọc dùng `mode=ro`; write transaction
  ngắn, WAL + busy timeout, không DDL live.
- Giữ tương thích đọc V1 trong thời gian chuyển tiếp, nhưng mọi ghi mới dùng V2.
- Không gọi local extract là “AI”; UI phải hiển thị provider/model thật.
- Thư mục hiện không phải Git worktree; không tự khởi tạo repository hoặc push.

## Plan

1. **Contract và grounding** — `reddit_crawler/llm.py`, `analytics.py`,
   `resources.py`, `storage.py`, `jobs/export_story.py`: chuẩn hoá domain, sửa V2
   sanitizer/local fallback, ưu tiên V2 trong mọi consumer, giữ V1 fallback.
2. **Pipeline** — thêm `jobs/enrich.py`, `jobs/report.py`, `jobs/pipeline.py`; nối
   các command trong `cli.py`; dùng bounded selection và skip analysis còn tươi.
3. **Operations/API** — sửa `deploy/systemd/*`, `web/app.py`: endpoint chỉ đọc,
   unified feed, Today briefing, health theo từng tầng và freshness.
4. **Frontend** — tách/đơn giản hoá `App.jsx` khi cần; ba bề mặt Today / Knowledge /
   Radar, provider truth, domain/search thật, responsive.
5. **Tests** — thêm regression cho V2 sanitizer, resource schema, pipeline orchestration,
   API contracts và story-export V2; sửa taxonomy test cũ.
6. **Verify/deploy** — chạy full gates, test storage trên DB copy, bounded live pipeline,
   reload systemd/web, chụp desktop/mobile và kiểm tra health/service logs.
7. **Gemini backlog** — thêm selection toàn backlog cho enrichment/analysis,
   Gemini-only với retry/backoff + manual-review artifact; thêm systemd timer chạy
   batch có lock, test trên DB copy rồi khởi động một batch live có giới hạn.

## Known risks

- Reddit/API quota có thể khiến live analysis không hoàn tất; auto chain phải ghi lỗi
  rõ và không biến local output thành success giả.
- Article fetch cần SSRF/size/timeout guard; ưu tiên selftext/comment nếu nguồn ngoài
  không an toàn hoặc không đọc được.
- V1/V2 coexistence có thể gây duplicate feed; unified query phải chọn một bản tốt
  nhất theo post, ưu tiên V2 LLM > V1 LLM > V2 local > V1 local.
- Existing systemd units trong `~/.config/systemd/user` có thể là bản copy cũ;
  deployment phải cài lại unit rồi daemon-reload.
- 6k+ post có thể cần nhiều Reddit/Gemini request; backlog phải tiến dần qua mọi
  candidate, không retry nóng một lỗi và không chạy hai batch đồng thời.

## Decisions log

- 2026-07-26 — Reddit-first: chưa mở rộng đa nguồn trước khi pipeline hiện tại tạo
  được intelligence đáng đọc và đo được chất lượng.
- 2026-07-26 — Một contract ghi mới: PostAnalysis V2; V1 chỉ là compatibility read.
- 2026-07-26 — Dùng `ai_digest` làm intelligence brief theo chu kỳ để tránh thêm
  schema live; clustering sâu hơn sẽ là spec sau khi có quality baseline.
- 2026-07-26 — Không DDL live và không khởi tạo Git trong phạm vi này.
- 2026-07-26 — Article fetch ngoài Reddit tiếp tục tắt: chưa có đủ SSRF, redirect,
  robots và size guard. MVP chỉ được tuyên bố đã đọc selftext, comment và article
  text vốn đã lưu; không gọi title-only là đã đọc bài.
- 2026-07-26 — Review độc lập phát hiện cost-bound, partial transaction,
  provenance COALESCE, stale-period fallback và provisional story-export. Mỗi lỗi
  được thêm regression trước khi sửa; pipeline tách rõ LLM artifact và provisional.
- 2026-07-26 — `status='success'` ở analysis nghĩa là artifact đọc được; provider
  và `artifact_kind` quyết định đó là LLM hay provisional. Auto fallback không được
  ghi đè LLM V2 cũ và làm pipeline trả trạng thái partial.
- 2026-07-26 — User yêu cầu Gemini xử lý toàn bộ backlog. `provider=gemini` là
  fail-closed: không tự chuyển OpenAI/local. Post chưa có comment phải enrichment
  trước; post rỗng không được gửi LLM. Lỗi được backoff và xuất manual-review để
  xử lý tiếp, vì service nền không thể tự gọi một phiên Codex tương tác.
- 2026-07-26 — Quality gate live cho thấy một output có đúng một key point dùng
  comment ID sai còn các phần khác hợp lệ. Grounding nay sanitize lỗi deterministic
  (drop bad ID/invented URL, downgrade `verified`) trước semantic gate; generic,
  thiếu field, sai ngôn ngữ và provider-supplied quality issues vẫn fail-closed.
- 2026-07-26 — Gemini backlog và enrichment dùng chung systemd `flock`; batch
  analysis tối đa 5 call/10 phút, enrichment tối đa 20 post/10 phút. Periodic
  digest dùng Gemini strict và không chạy PostAnalysis song song với backlog.

## Outcome

Đang vận hành, backlog tiếp tục drain tự động:

- Full gate sau Gemini backlog: compile sạch, 60/60 unittest pass, Vite production
  build pass, `systemd-analyze verify` pass.
- Copy DB thật: selector thấy 181 discussion đủ bằng chứng, 3 Gemini complete và
  178 pending; resource backlog ghi được, schema SHA-256 không đổi và quick-check ok.
- Live backup trước deploy: `/tmp/reddit-gemini-live-backup-20260726-133717/reddit.db`.
- Sau ba bounded batch và manual retry các quality-gate item: Gemini V2 tăng từ
  3 lên 18/181, còn 163 evidence-ready; manual-review 0, blocked 0. Batch cuối
  5/5 success và `reddit-gemini-backlog.service Result=success`.
- Live DB quick-check ok; schema hash trùng backup; 0 analysis có comment ID sai,
  source_post_id sai hoặc provider quality issue. Web healthy và feed trả 18 Gemini.
