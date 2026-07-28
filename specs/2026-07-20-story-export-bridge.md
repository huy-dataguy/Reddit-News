# Spec: story-export — cầu nối Reddit Radar → MediaWorkflow

- Date: 2026-07-20
- Status: done (shipped 2026-07-20; one content follow-up filed — see Outcome)

## Problem

Reddit Radar đã chấm được tin giá trị cao (`trend_score`) và có Community Intelligence tiếng Việt (`ai_post_analysis`, 65 bài), nhưng dữ liệu đó dừng ở dashboard/report. MediaWorkflow đã có pipeline `make-video.sh` nhận story JSON → video RedditStory hoàn chỉnh. Hai đầu chưa nối: chọn tin đắt → thành story JSON vẫn là việc tay.

## Goal

Lệnh `cli.py story-export` chọn post giá trị cao (theo `--post-id` hoặc `--top` trend_score có phân tích AI), map deterministic sang schema `RedditStoryData` (16 field) + 8 câu thoại tiếng Việt sinh từ template, tải ảnh post, và ghi thẳng vào `MediaWorkflow/repo-review-studio/stories/reddit-<postId>.json` — sẵn sàng cho `make-video.sh` chạy không cần sửa tay.

## Non-goals

- Không gọi LLM lúc export (chất liệu VN đã có sẵn trong `ai_post_analysis`; polish thoại bằng Gemini là spec sau).
- Không tự động render/scheduler (cron nối 2 lệnh là spec sau).
- Không đổi schema composition RedditStory bên studio.

## Acceptance criteria

- [ ] AC1 — Unit test mapper (fixture → story hợp lệ: đủ 16 field không rỗng, đúng 3 reactions, ≤3 policy, 8 scene × ≥1 line thoại) — verify: `.venv/bin/python -m unittest discover -s tests`
- [ ] AC2 — Code sạch cú pháp — verify: `.venv/bin/python -m compileall -q reddit_crawler jobs cli.py`
- [ ] AC3 — Export thật từ DB sản xuất ra file story + tự validate — verify: `.venv/bin/python cli.py story-export --top --dry-check`  (exit 0, in đường dẫn file đã ghi)
- [ ] AC4 — Story render được thành video hoàn chỉnh bên studio — verify: `cd <studio> && bash scripts/make-video.sh stories/reddit-<postId>.json && bash scripts/check-av-sync.sh reddit-<postId>`
- [ ] AC5 — Môi trường 2 dự án nguyên vẹn — verify: `verify-env.sh <MediaWorkflow> <Reddit>` → ENV OK

## Constraints

- `reddit.db` chỉ đọc (mở `mode=ro`); collector có thể đang ghi song song.
- Ảnh tải bằng stdlib urllib (timeout 15s); lỗi tải → fallback `screenshot.png` có sẵn trong studio, export vẫn thành công.
- Thoại tránh đọc nguyên username Reddit (VO đọc rất gượng) — template ưu tiên consensus/learning_points.
- Đường dẫn hai bên đều chứa dấu cách — quote mọi path.

## Plan

1. `jobs/export_story.py`: chọn post (trending + join ai_post_analysis success) → gom fact_post, metrics mới nhất, media, payload phân tích → `build_story()` thuần (test được) → validate → ghi JSON + tải ảnh.
2. Map: hook/hookSub = score định dạng VN + "upvote trên r/<sub>"; daily/dailySub = số bình luận; policy = suggestions/learning_points (≤3, cắt 90 ký tự); reactions = opinion_groups (3 nhóm, score = độ ủng hộ hoặc điểm comment); lesson = learning_points[0]; question = unanswered_questions[0] (fallback template).
3. Thoại 8 câu VN từ topic, số liệu, consensus, 2 ý kiến, lesson, question.
4. `cli.py`: subcommand `story-export` (--post-id | --top, --period, --out, --dry-check).
5. `tests/test_export_story.py`: fixture payload thật (rút gọn) + assert schema.
6. E2E render bên studio (nền, ~10 phút), cập nhật CLAUDE.md cả hai dự án.

## Decisions log

- 2026-07-20 — Deterministic-first: không LLM trong export để pipeline chạy được cả khi hết API quota; polish là tầng sau.

## Outcome

Shipped 2026-07-20. Evidence per AC:

- AC1: 23/23 unittest PASS (9 new mapper tests). AC2: compileall clean.
- AC3: `story-export --top` chọn đúng bài trend nhất có phân tích (1v0mnqa — "YouTube AI Slop Purge", r/Futurology; metrics tươi: 23.471 điểm / 1.511 bình luận), story + ảnh thật ghi sang MediaWorkflow.
- AC4: render trọn dây chuyền mới (TTS 54.63s → normalize → mp4 54.70s, Δ0.07s PASS) — chạy qua `run-job.sh` tách session, sống sót qua 1 lần session chết + 1 lần script bị session song song sửa giữa chừng (retry `--skip-tts` thành công).
- AC5: verify-env cả 2 dự án ENV OK.

**Follow-up bắt buộc trước khi publish video từ export generic:** composition `RedditStory` còn text hardcode sót từ story Spotify gốc (scene 1 ghép "track AI bị Spotify xóa" với hook 23.471 → sai sự thật; scene 3 "AI music... 2 NĂM"). Captions/thoại từ export thì đúng. Cần spec "RedditStory fully data-driven" bên MediaWorkflow: mọi headline lấy từ story JSON, xoá copy cứng.

Ghi chú vận hành: pipeline giờ có tầng tự hồi phục (supervisor systemd 15', stop-gate, run-job) — flow "crawl → score → analyze → export → video" chạy được không người trực; nội dung cuối vẫn cần mắt người duyệt trước khi đăng (đúng luật contact-sheet review trong CLAUDE.md MediaWorkflow).
