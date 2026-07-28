# Spec: Reproducible release và runtime operations cho internal production

- Date: 2026-07-28
- Status: draft

## Problem

Hệ thống đang chạy bằng `.venv`, source tree và systemd trên một máy, nhưng chưa có Git metadata, Python dependency lock, CI, browser smoke, release manifest hoặc automated recovery/alert loop. `requirements.txt` dùng range rộng; frontend có lock nhưng build chưa nằm trong command contract; systemd units chạy được nhưng chưa có hardening, failure notification hay deployment verifier. Với production nội bộ, không cần hạ tầng cloud phức tạp, nhưng vẫn phải biết đang chạy revision nào, tái dựng được môi trường, phát hiện job chết/disk đầy, sao lưu có thể restore và rollback có bằng chứng.

## Goal

Thiết lập một release lane nhỏ gọn cho một máy nội bộ:

- mọi release có revision ID, dependency locks và immutable manifest;
- clean bootstrap + backend verify + frontend build/smoke cho cùng kết quả giữa người, agent và CI/local runner;
- systemd units được cài từ repo, hash-verified, least-privilege trong giới hạn hiện tại và có actionable failure alert;
- secrets/data permissions được kiểm tra mà không in giá trị;
- backup/restore, disk retention và rollback được tự động hóa an toàn;
- operator có dashboard/CLI production status và runbook ngắn đủ xử lý sự cố phổ biến.

## Non-goals

- Không containerize chỉ để gọi là production.
- Không triển khai Kubernetes, Terraform, cloud CI bắt buộc hoặc remote artifact registry.
- Không mở web ra LAN/Internet, không thêm public auth/TLS.
- Không thay thế SQLite hoặc thiết kế schema medallion; spec dữ liệu sở hữu phần đó.
- Không gửi alert ra dịch vụ ngoài nếu operator chưa cấu hình/cho phép credential tương ứng.

## Release contract

### Source và dependency identity

- Khởi tạo version control trước build lớn; release chỉ từ revision được commit/tag và working tree sạch.
- Giữ `requirements.in` cho direct constraints và sinh `requirements.lock` fully pinned bằng tool được khóa phiên bản; không lấy `pip freeze` của môi trường bẩn làm nguồn sự thật.
- Frontend dùng `npm ci` với `package-lock.json`; production install/build không dùng `npm install` tự resolve.
- Ghi Python, pip/locker, Node và npm major/minor trong `.tool-versions` hoặc `runtime-versions.json`.
- Lock refresh là thay đổi R2 riêng, có audit và full verify.

### Unified verify

`scripts/verify-production.sh` là command contract mới và bao gồm:

1. existing backend compile + 60+ unittest regression;
2. spec registry/contracts checks;
3. frontend `npm ci` clean build;
4. API contract smoke trên DB fixture;
5. headless browser smoke cho feed, detail, back/forward và error state;
6. dependency integrity/audit theo policy;
7. systemd unit syntax/security/static checks.

Script không đọc/in `.env`; các integration test cần provider chạy bằng fake transport hoặc explicit opt-in.

### Runtime boundary

- `reddit-web.service` tiếp tục bind `127.0.0.1:8080`.
- Unit dùng dedicated Unix user nếu việc chuyển ownership được operator duyệt; nếu chưa, tối thiểu `UMask=0077`, `NoNewPrivileges=true`, `PrivateTmp=true`, `ProtectSystem=strict` và `ReadWritePaths` tối thiểu được rehearsal.
- One-shot jobs giữ lock/timeout; timeout và exit non-zero tạo failure event.
- Installed unit digest phải khớp file repo ở release revision.
- Deploy dùng `systemctl daemon-reload`, restart có thứ tự và health gate; không copy tay không kiểm tra.

### Recovery và retention boundary

- SQLite backup dùng online backup API hoặc `.backup`, không `cp` file DB đang ghi.
- Backup artifact có checksum, DB quick/FK check, schema version, created time và source revision.
- Restore luôn vào path mới/read-only trước; không overwrite live DB trong drill.
- Bronze/raw retention và reports retention dùng policy từ medallion spec, hỗ trợ dry-run và manifest file list.
- Capacity gate mặc định: cảnh báo dưới 20% hoặc 40 GiB free; dừng các batch/backfill không thiết yếu dưới 10% hoặc 20 GiB. Giá trị cuối phải cấu hình theo filesystem thực tế.

### Alert policy

Alert local tối thiểu được ghi structured JSON và hiện trong `/api/health`/production status. Optional sink (desktop/email/Telegram) là adapter cấu hình sau. Event bắt buộc:

- crawl stale quá freshness SLO;
- timer/service fail liên tiếp hoặc duration vượt budget;
- DQ hard fail/Gold publish fail;
- LLM circuit breaker mở quá window;
- backup stale/restore drill stale;
- disk vượt warning/critical;
- web health fail sau deploy.

Deduplicate theo `(alert_type, resource, state)`; gửi recovery event khi trở lại bình thường.

## Acceptance criteria

- [ ] Repo có revision control, ignore rules bảo vệ `.env`, DB/raw/build artifacts và release manifest ghi clean revision — verify: `git rev-parse --verify HEAD && test -z "$(git status --porcelain)" && .venv/bin/python -m unittest tests.test_release_manifest`
- [ ] Python clean environment cài đúng lock và `pip check` xanh — verify: `scripts/verify-clean-python-lock.sh`
- [ ] Frontend clean install/build từ lock và production audit không có high/critical finding chưa waive — verify: `scripts/verify-clean-frontend.sh`
- [ ] Unified production verify gồm backend, contracts, API và browser smoke — verify: `scripts/verify-production.sh`
- [ ] Systemd repo units hợp lệ, installed units khớp digest và localhost binding được kiểm tra — verify: `.venv/bin/python cli.py ops verify-units --installed --require-localhost`
- [ ] Runtime unit hardening không làm mất write path cần thiết và đạt policy — verify: `scripts/verify-systemd-hardening.sh`
- [ ] Secret/data permission scan chỉ báo metadata và không phát hiện group/other-readable secrets/live DB/raw — verify: `.venv/bin/python cli.py ops check-permissions --redact-values`
- [ ] Backup tạo bằng SQLite-safe method, checksum/quick/FK check xanh — verify: `.venv/bin/python cli.py ops backup --dry-run && .venv/bin/python -m unittest tests.test_backup_restore`
- [ ] Restore drill dựng DB ở temp path, health/read-only query đạt và không chạm live DB — verify: `.venv/bin/python cli.py ops restore-drill --latest --temporary --verify`
- [ ] Retention/capacity command mặc định dry-run, không xóa file ngoài manifest và chặn non-essential batch ở critical threshold — verify: `.venv/bin/python -m unittest tests.test_retention tests.test_capacity_gate`
- [ ] Failure/stale/disk/backup alerts dedupe và recovery đúng trên fake clock — verify: `.venv/bin/python -m unittest tests.test_ops_alerts`
- [ ] Deploy rehearsal từ release manifest có preflight, health gate và rollback — verify: `.venv/bin/python cli.py ops deploy --manifest tests/fixtures/release_manifest.json --rehearse --temporary-root`
- [ ] Operator đã restore một backup mới và rehearsal restart/rollback systemd không gây data loss — verify-manual: Operator: chạy runbook trên copy DB và temporary unit namespace, lưu commands/timestamps/results trong RC dossier

## Risk tier

Tier: R3 — thay đổi version/dependencies, file ownership, systemd, backup/restore và live deployment; rehearsal là bắt buộc và live action cần human approval.

## Constraints

- Không đọc hoặc in nội dung `.env`; test chỉ kiểm permission/path/name đã redact.
- Không `cp`, overwrite, migrate hoặc restore đè `reddit.db` đang sống.
- Không dùng destructive cleanup với glob/path chưa resolve; retention phải manifest-first và dry-run mặc định.
- Không yêu cầu network trong default unit tests/browser smoke.
- Không update dependency ngoài explicit lock-refresh task.
- Installed service vẫn phải hoạt động với absolute project path hiện tại cho đến khi migration dedicated user được duyệt.
- Alert sink mặc định local; không tạo webhook/email/Telegram resource nếu chưa có approval.

## Stop if (only if a run could plausibly overreach)

- Git initialization có nguy cơ add `.env`, DB, raw, backup hoặc `node_modules` vào index.
- Hardening unit chặn write path chưa được inventory hoặc đòi đổi ownership toàn project/live data không có rollback.
- Backup/restore test resolve target trùng live DB path.
- Disk đã dưới critical threshold trước khi tạo được verified backup.
- Lock refresh đổi major version hoặc audit phát hiện high/critical issue không có fix/waiver.
- Deploy rehearsal hoặc health gate fail; không tiếp tục live restart.

## Interfaces (only if criteria depend on each other)

- `runtime-versions.json`: `{python, pip, lock_tool, node, npm}`.
- `requirements.in` và `requirements.lock`: direct intent và fully pinned transitive graph; header ghi generator/version/command.
- `ReleaseManifestV1`: theo program spec; file ở `reports/operations/release/<release_id>/manifest.json`.
- `OpsAlertV1`: `{id, type, resource, severity, state, first_seen_at, last_seen_at, occurrences, summary, evidence_path}`.
- `BackupManifestV1`: `{backup_id, created_at, source_revision, source_schema_version, db_path_redacted, artifact, sha256, size_bytes, quick_check, foreign_key_check, counts}`.

## Plan (filled at Plan stage)

### WP0 — Inventory và safety floor

- Snapshot runtime versions, dependency graph, `.gitignore`, file permissions, unit hashes và disk thresholds; không log secret.
- Xác định operator, backup root, retention values và alert local path.
- Viết tests cho path safety, redaction và release manifest trước CLI mutating command.

### WP1 — Revision và reproducible dependencies

- Harden ignore rules rồi initialize Git dưới explicit R2 approval; stage dry-run/list trước commit đầu.
- Tách `requirements.in`, chọn/khóa lock generator, tạo fully pinned lock.
- Ghi runtime versions; thêm clean bootstrap scripts dùng temp venv/cache.
- Giữ npm lock, đổi verify/build path sang `npm ci`.

### WP2 — Unified verify và browser smoke

- Mở rộng command contract bằng `scripts/verify-production.sh` nhưng giữ command AGENTS hiện tại như fast backend verify.
- Thêm API contract fixtures không cần live DB.
- Thêm browser test runner tối thiểu cho feed/detail/navigation/error; pin browser/tool version.
- Tích hợp local CI runner; remote CI là optional nếu chưa có remote repo.

### WP3 — Ops CLI, backup và restore

- Thêm `cli.py ops ...` qua module `reddit_crawler/ops/`.
- Implement safe path resolver, SQLite online backup, manifest/checksum và restore-to-new-path.
- Thêm capacity/retention planner với dry-run mặc định, quarantine/trash window trước permanent deletion.
- Rehearse trên copy của DB thật và lưu timing/size.

### WP4 — Systemd hardening và alerts

- Inventory read/write/network requirements cho từng unit.
- Áp dụng hardening incrementally, chạy `systemd-analyze verify/security`, temporary/rehearsal trước installed unit.
- Tạo failure/status collector và local structured alerts; gắn vào health/production status.
- Viết unit installer/verifier có digest, daemon-reload, ordered restart, health và rollback.

### WP5 — Release candidate

- Build từ clean revision/locks, chạy unified verify và tạo manifest/dossier.
- Restore drill backup mới, deploy rehearsal, test failure alert/recovery.
- Named operator duyệt live rollout; quan sát và rollback khi gate fail.

## Decisions log (append during Build)

- 2026-07-28 — Production nội bộ vẫn cần reproducibility và recovery; container/cloud CI không phải điều kiện bắt buộc.
- 2026-07-28 — Backup dữ liệu và release identity là Wave 0, không chờ refactor medallion hoàn tất.
- 2026-07-28 — Alert local structured là baseline; external notification là adapter opt-in.
- 2026-07-28 — Bind localhost là security boundary được test, không chỉ là convention trong command line.

## Outcome (filled at Ship)

Chưa build. Khi ship, ghi revision/tag đầu tiên, lock digests, clean-build evidence, installed unit digests, backup/restore timing, alert rehearsal và release dossier path.
