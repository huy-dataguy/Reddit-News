"""Các 'job' chạy định kỳ / theo lô cho pipeline tin tức.

- incremental.py          : cron lấy post MỚI cho danh sách sub + snapshot metrics.
- backfill_arctic_shift.py : nạp dữ liệu LỊCH SỬ qua Arctic Shift (vượt trần 1000).
- enrich.py                : lấy comment còn thiếu và bóc tách resource.
- pipeline.py              : chạy enrich → PostAnalysis V2 → digest → report.
- report.py                : tạo briefing Markdown/JSON 3h/ngày/tuần/tháng/năm.
- export_story.py          : xuất analysis publishable sang MediaWorkflow.

Các job dùng chung star schema (reddit.db) và thư mục report.
"""
