# Chạy tự động bằng systemd user

Trước tiên chỉnh `.env`, nhất là `REDDIT_USER_AGENT` và `REDDIT_CLIENT_ID`, rồi cài
dependency vào môi trường riêng:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/*.service deploy/systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now reddit-crawl.timer \
  reddit-enrich.timer \
  reddit-gemini-backlog.timer \
  reddit-ai@3h.timer reddit-ai@day.timer reddit-ai@week.timer \
  reddit-ai@month.timer reddit-ai@year.timer \
  reddit-report@3h.timer reddit-report@day.timer reddit-report@week.timer \
  reddit-report@month.timer reddit-report@year.timer reddit-web.service
```

`reddit-enrich.timer` vét comment/resource toàn backlog theo batch 20.
`reddit-gemini-backlog.timer` lấy mọi discussion non-NSFW có comment nhưng chưa
có Gemini V2 success, xử lý tối đa 5 bài mỗi batch bằng Gemini strict. Lỗi được
backoff, tối đa 3 lần rồi chuyển `blocked`; tiến độ ở
`reports/operations/gemini-backlog.latest.{json,md}`. Không có OpenAI/local
fallback trong queue này.

Các instance `reddit-ai@*.timer` chỉ tạo Gemini digest đúng period và report;
PostAnalysis đã do backlog singleton đảm nhiệm. Tất cả job enrichment/Gemini dùng
chung `flock` để không gọi provider chồng nhau. Timer report riêng chỉ
materialize lại snapshot gần nhất; `After=` không tự kéo AI chạy.
External article fetch không chạy trong job này vì phiên bản hiện tại chưa có
lại đầy đủ guard SSRF, redirect, robots và giới hạn kích thước.

Có thể kiểm tra toàn chuỗi với giới hạn nhỏ trước khi bật timer:

```bash
.venv/bin/python cli.py enrich --kind both --backlog --limit 2
.venv/bin/python cli.py gemini-backlog --limit 1
```

Dashboard chạy tại `http://127.0.0.1:8080`. Kiểm tra trạng thái:

```bash
systemctl --user list-timers
journalctl --user -u reddit-crawl.service -n 100
journalctl --user -u reddit-enrich.service -n 100
journalctl --user -u reddit-gemini-backlog.service -n 100
journalctl --user -u reddit-ai@3h.service -n 100
journalctl --user -u reddit-report@3h.service -n 100
```
