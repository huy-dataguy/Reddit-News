# Star Schema — Mô hình dữ liệu Reddit

## Kiến trúc 2 tầng (medallion rút gọn)

```
   Reddit OAuth API
         │
         ▼
   ┌───────────────┐   BRONZE (raw)   ┌──────────────────────────────┐
   │ reddit_crawler│ ───────────────▶ │ raw/*.jsonl  (JSON gốc)      │
   │   (crawl.py)  │                  │ post / comment / user / sub  │
   └───────────────┘                  └──────────────────────────────┘
         │                                        │  (tái xử lý bất cứ lúc nào)
         │ GOLD (đã mô hình hoá)                   ▼
         ▼
   ┌──────────────────────────── SQLite: star schema ───────────────────────────┐
   │  DIM: dim_subreddit · dim_author · dim_date                                 │
   │  FACT: fact_post (grain=1 post) · fact_comment (grain=1 comment)            │
   └────────────────────────────────────────────────────────────────────────────┘
```

Giữ **raw** để sau này đổi mô hình mà không phải crawl lại — nguyên tắc vàng của data engineering.

## Sơ đồ sao

```
                        ┌───────────────┐
                        │  dim_date     │
                        │  date_key(PK) │
                        └──────┬────────┘
                               │ created_date_key
        ┌───────────────┐      │        ┌────────────────┐
        │ dim_subreddit │◀─────┼───────▶│  dim_author    │
        │ subreddit_id  │      │        │  author_name   │
        └──────┬────────┘      │        └───────┬────────┘
               │        ┌──────▼───────┐        │
   subreddit_id│        │  FACT_POST   │        │author_name
               └───────▶│  post_id(PK) │◀───────┘
                        └──────┬───────┘
                               │ post_id
                        ┌──────▼───────┐
                        │ FACT_COMMENT │  (cũng nối tới dim_author,
                        │ comment_id PK│   dim_subreddit, dim_date;
                        └──────────────┘   parent_fullname = self-ref cây)
```

## Bảng & ý nghĩa

### FACT_POST — grain: **một post**
- Khóa: `post_id`. Đo lường: `score`, `ups`, `upvote_ratio`, `num_comments`,
  `num_crossposts`, `total_awards`.
- FK: `subreddit_id`, `author_name`, `created_date_key`.
- Mô tả: `title`, `selftext`, `url`, `domain`, `link_flair_text`, `over_18`, `is_video`...

### FACT_COMMENT — grain: **một comment**
- Khóa: `comment_id`. Đo lường: `score`, `ups`, `controversiality`, `total_awards`.
- FK: `post_id`, `subreddit_id`, `author_name`, `created_date_key`.
- `parent_fullname` (t3_=post, t1_=comment) + `depth` → dựng lại **cây comment**.
- `is_submitter`: comment có phải của chính OP không.

### DIM_SUBREDDIT / DIM_AUTHOR / DIM_DATE
- `dim_subreddit`: `subscribers`, `created_utc`, `over18`, mô tả...
- `dim_author`: `total_karma`, `link_karma`, `comment_karma`, `verified`, `is_mod`...
- `dim_date`: `year/quarter/month/day/weekday` — cho phân tích theo thời gian.

## Vì sao thiết kế này khớp mục tiêu dự án

| Câu hỏi nghiệp vụ | Truy vấn |
|-------------------|----------|
| Chủ đề nào "nóng" hôm nay/tuần này? | `fact_post × dim_date` gom theo `score`, `num_comments` |
| Ai là người đăng/bình luận ảnh hưởng nhất? | `fact_* × dim_author` theo `SUM(score)` |
| Sub nào đang bùng nổ tương tác? | `fact_post × dim_subreddit` theo thời gian |
| Lấy cây thảo luận của 1 bài để tóm tắt (AI) | `fact_comment` theo `post_id`, sắp theo `depth`, `parent_fullname` |
| Xu hướng theo giai đoạn/năm | gom theo `dim_date.year/quarter` |

→ Đây là nền cho lớp trên: baseline xếp hạng tin hot hiện nằm ở
`reddit_crawler/analytics.py`; tóm tắt bằng LLM và trích địa danh để lên bản đồ là
các lớp enrichment tiếp theo.

## Hướng nâng cấp (khi lên production)
- **Surrogate key + SCD type-2** cho dimension để theo dõi thay đổi theo thời gian
  (karma user, subscribers sub) — hiện v1 dùng natural key cho gọn.
- Chuyển SQLite → **Postgres / DuckDB / BigQuery**; schema gần như giữ nguyên.
- Thêm `dim_domain` (nguồn link ngoài), `dim_flair`, và `bridge_post_entity`
  (địa danh/thực thể trích bằng NER) để phục vụ bản đồ tin tức.
- Khi chuyển sang Postgres, partition `fact_post_metrics` theo thời gian và thêm
  retention/rollup để giữ truy vấn velocity nhanh khi dữ liệu lớn.
