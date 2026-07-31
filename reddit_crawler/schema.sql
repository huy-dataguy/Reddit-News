-- ============================================================================
-- STAR SCHEMA cho dữ liệu Reddit  (SQLite; đổi kiểu dữ liệu là chạy được Postgres/BigQuery)
--
--   DIM: dim_subreddit, dim_author, dim_date
--   FACT: fact_post (grain = 1 post), fact_comment (grain = 1 comment)
--
-- Dùng natural key (id của Reddit) làm khóa dimension cho gọn ở v1.
-- Nâng cấp sau: thay bằng surrogate key + SCD type-2 để theo dõi thay đổi
-- (vd karma của user, subscribers của sub theo thời gian).
-- ============================================================================

CREATE TABLE IF NOT EXISTS schema_migration (
    version             INTEGER PRIMARY KEY,
    name                TEXT NOT NULL,
    applied_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO schema_migration (version, name)
VALUES (2, 'integrity_trending_reports_web');
INSERT OR IGNORE INTO schema_migration (version, name)
VALUES (3, 'article_and_comment_enrichment');
INSERT OR IGNORE INTO schema_migration (version, name)
VALUES (4, 'ai_digest_and_media');
INSERT OR IGNORE INTO schema_migration (version, name)
VALUES (5, 'domain_and_comment_intelligence');
INSERT OR IGNORE INTO schema_migration (version, name)
VALUES (6, 'extracted_resources_hub');
INSERT OR IGNORE INTO schema_migration (version, name)
VALUES (7, 'longread_magazine');
INSERT OR IGNORE INTO schema_migration (version, name)
VALUES (8, 'knowledge_intelligence');
INSERT OR IGNORE INTO schema_migration (version, name)
VALUES (9, 'analysis_v2');

-- ---------- DIMENSIONS -------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_subreddit (
    subreddit_id        TEXT PRIMARY KEY,   -- vd 't5_2qh16' hoặc '2qh16'
    display_name        TEXT,
    title               TEXT,
    subscribers         INTEGER,
    created_utc         REAL,
    over18              INTEGER,
    public_description  TEXT,
    lang                TEXT,
    fetched_at          REAL
);

CREATE TABLE IF NOT EXISTS dim_author (
    author_name         TEXT PRIMARY KEY,   -- username là khóa tự nhiên ổn định
    author_id           TEXT,               -- 't2_xxxxx'
    created_utc         REAL,
    total_karma         INTEGER,
    link_karma          INTEGER,
    comment_karma       INTEGER,
    is_mod              INTEGER,
    is_gold             INTEGER,
    verified            INTEGER,
    has_verified_email  INTEGER,
    fetched_at          REAL
);

CREATE TABLE IF NOT EXISTS dim_date (
    date_key            INTEGER PRIMARY KEY,   -- YYYYMMDD
    full_date           TEXT,                  -- 'YYYY-MM-DD'
    year                INTEGER,
    quarter             INTEGER,
    month               INTEGER,
    day                 INTEGER,
    weekday             INTEGER                -- 0=thứ Hai ... 6=Chủ nhật
);

-- ---------- FACTS ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_post (
    post_id             TEXT PRIMARY KEY,   -- id gọn, vd '1rfgu9a'
    fullname            TEXT,               -- 't3_1rfgu9a'
    subreddit_id        TEXT REFERENCES dim_subreddit(subreddit_id),
    author_name         TEXT REFERENCES dim_author(author_name),
    created_date_key    INTEGER REFERENCES dim_date(date_key),
    created_utc         REAL,
    title               TEXT,
    selftext            TEXT,
    url                 TEXT,
    domain              TEXT,
    permalink           TEXT,
    is_self             INTEGER,
    over_18             INTEGER,
    is_video            INTEGER,
    link_flair_text     TEXT,
    -- measures --
    score               INTEGER,
    ups                 INTEGER,
    upvote_ratio        REAL,
    num_comments        INTEGER,
    num_crossposts      INTEGER,
    total_awards        INTEGER,
    fetched_at          REAL
);

CREATE TABLE IF NOT EXISTS fact_comment (
    comment_id          TEXT PRIMARY KEY,   -- id gọn
    fullname            TEXT,               -- 't1_xxxxx'
    post_id             TEXT REFERENCES fact_post(post_id),
    subreddit_id        TEXT REFERENCES dim_subreddit(subreddit_id),
    author_name         TEXT REFERENCES dim_author(author_name),
    parent_fullname     TEXT,               -- t3_ (post) hoặc t1_ (comment cha)
    created_date_key    INTEGER REFERENCES dim_date(date_key),
    created_utc         REAL,
    depth               INTEGER,
    body                TEXT,
    -- measures --
    score               INTEGER,
    ups                 INTEGER,
    controversiality    INTEGER,
    total_awards        INTEGER,
    is_submitter        INTEGER,            -- có phải OP không
    fetched_at          REAL
);

-- ---------- SNAPSHOT (đo tốc độ lan truyền = "tin nóng") ---------------------
-- Mỗi lần cron chạy lại thấy một post -> ghi 1 dòng. score theo thời gian cho
-- phép tính velocity = Δscore / Δt để phát hiện bài đang "bốc".
CREATE TABLE IF NOT EXISTS fact_post_metrics (
    post_id             TEXT REFERENCES fact_post(post_id),
    observed_at         REAL,               -- epoch lúc quan sát
    score               INTEGER,
    ups                 INTEGER,
    upvote_ratio        REAL,
    num_comments        INTEGER,
    PRIMARY KEY (post_id, observed_at)
);

-- ---------- CRAWL STATE (mốc incremental cho cron) ---------------------------
-- Nhớ tới đâu để lần sau chỉ lấy post MỚI hơn, không cào lại từ đầu.
CREATE TABLE IF NOT EXISTS crawl_state (
    scope               TEXT PRIMARY KEY,   -- vd 'sub:technology:new'
    last_utc            REAL,               -- created_utc mới nhất đã thấy
    last_fullname       TEXT,               -- 't3_...' của post mới nhất
    updated_at          REAL
);

-- ---------- ENRICHMENT: nội dung đọc ngay trong website ----------------------
CREATE TABLE IF NOT EXISTS fact_article_content (
    post_id             TEXT PRIMARY KEY REFERENCES fact_post(post_id),
    source_url          TEXT,
    final_url           TEXT,
    title               TEXT,
    author              TEXT,
    published_at        TEXT,
    language            TEXT,
    body_text           TEXT,
    word_count          INTEGER,
    status              TEXT NOT NULL,      -- success|blocked|empty|error
    http_status         INTEGER,
    fetched_at          REAL,
    error               TEXT
);

CREATE TABLE IF NOT EXISTS enrichment_state (
    post_id             TEXT REFERENCES fact_post(post_id),
    kind                TEXT,               -- article|comments|summary|entities
    status              TEXT,
    attempted_at        REAL,
    completed_at        REAL,
    error               TEXT,
    PRIMARY KEY (post_id, kind)
);

CREATE TABLE IF NOT EXISTS fact_post_media (
    post_id             TEXT PRIMARY KEY REFERENCES fact_post(post_id),
    image_url           TEXT,
    thumbnail_url       TEXT,
    media_type          TEXT,
    width               INTEGER,
    height              INTEGER,
    fetched_at          REAL
);

CREATE TABLE IF NOT EXISTS ai_digest (
    digest_id           TEXT PRIMARY KEY,
    period              TEXT NOT NULL,
    window_start        REAL,
    window_end          REAL,
    provider            TEXT NOT NULL,
    model               TEXT,
    status              TEXT NOT NULL,
    title               TEXT,
    executive_summary   TEXT,
    payload_json        TEXT,
    source_count        INTEGER,
    input_tokens        INTEGER,
    output_tokens       INTEGER,
    generated_at        REAL,
    error               TEXT
);

CREATE TABLE IF NOT EXISTS ai_post_analysis (
    post_id             TEXT PRIMARY KEY REFERENCES fact_post(post_id),
    provider            TEXT NOT NULL,
    model               TEXT,
    status              TEXT NOT NULL,
    payload_json        TEXT,
    comment_count       INTEGER,
    input_tokens        INTEGER,
    output_tokens       INTEGER,
    generated_at        REAL,
    error               TEXT
);

CREATE TABLE IF NOT EXISTS ai_post_analysis_v2 (
    post_id             TEXT PRIMARY KEY REFERENCES fact_post(post_id),
    provider            TEXT NOT NULL,
    model               TEXT,
    status              TEXT NOT NULL,
    payload_json        TEXT,
    comment_count       INTEGER,
    input_tokens        INTEGER,
    output_tokens       INTEGER,
    generated_at        REAL,
    error               TEXT
);

-- ---------- RESOURCE MINING (bóc tách link/repo/paper từ comment & post) -------
CREATE TABLE IF NOT EXISTS fact_extracted_resource (
    resource_id         TEXT PRIMARY KEY,   -- hash (url)
    post_id             TEXT REFERENCES fact_post(post_id),
    comment_id          TEXT REFERENCES fact_comment(comment_id),
    platform_id         TEXT DEFAULT 'reddit',
    url                 TEXT NOT NULL,
    domain              TEXT,               -- github.com, arxiv.org, huggingface.co, substack.com...
    resource_type       TEXT NOT NULL,      -- github_repo | arxiv_paper | tech_blog | documentation | tool | video | other
    title               TEXT,
    description         TEXT,
    context_snippet     TEXT,               -- câu/đoạn xung quanh link
    author_name         TEXT,
    score               INTEGER DEFAULT 0,  -- upvotes của comment/post chứa link
    extracted_at        REAL
);

-- ---------- INDEXES ----------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_post_sub     ON fact_post(subreddit_id);
CREATE INDEX IF NOT EXISTS ix_post_author  ON fact_post(author_name);
CREATE INDEX IF NOT EXISTS ix_post_date    ON fact_post(created_date_key);
CREATE INDEX IF NOT EXISTS ix_cmt_post     ON fact_comment(post_id);
CREATE INDEX IF NOT EXISTS ix_cmt_author   ON fact_comment(author_name);
CREATE INDEX IF NOT EXISTS ix_cmt_parent   ON fact_comment(parent_fullname);
CREATE INDEX IF NOT EXISTS ix_metrics_post ON fact_post_metrics(post_id);
CREATE INDEX IF NOT EXISTS ix_article_status ON fact_article_content(status, fetched_at);
CREATE INDEX IF NOT EXISTS ix_enrichment_kind ON enrichment_state(kind, status);
CREATE INDEX IF NOT EXISTS ix_ai_digest_period ON ai_digest(period, generated_at DESC);
CREATE INDEX IF NOT EXISTS ix_ai_post_analysis_time ON ai_post_analysis(generated_at DESC);
CREATE INDEX IF NOT EXISTS ix_ai_post_analysis_v2_time ON ai_post_analysis_v2(generated_at DESC);
CREATE INDEX IF NOT EXISTS ix_resource_post ON fact_extracted_resource(post_id);
CREATE INDEX IF NOT EXISTS ix_resource_type ON fact_extracted_resource(resource_type, score DESC);
CREATE INDEX IF NOT EXISTS ix_resource_domain ON fact_extracted_resource(domain);

-- Medallion control plane tables
CREATE TABLE IF NOT EXISTS pipeline_run (
    run_id TEXT PRIMARY KEY,
    pipeline TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    input_count INTEGER DEFAULT 0,
    output_count INTEGER DEFAULT 0,
    error_summary TEXT
);

CREATE TABLE IF NOT EXISTS bronze_object (
    object_id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES pipeline_run(run_id),
    entity_type TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    row_count INTEGER DEFAULT 0,
    min_fetched_at REAL,
    max_fetched_at REAL,
    transform_status TEXT NOT NULL DEFAULT 'pending',
    transformed_at REAL
);

CREATE TABLE IF NOT EXISTS data_quality_result (
    run_id TEXT NOT NULL,
    layer TEXT NOT NULL,
    check_name TEXT NOT NULL,
    status TEXT NOT NULL,
    observed_value TEXT,
    threshold TEXT,
    checked_at REAL NOT NULL,
    PRIMARY KEY (run_id, layer, check_name)
);

CREATE TABLE IF NOT EXISTS serving_state (
    singleton_id TEXT PRIMARY KEY DEFAULT 'current',
    current_publish_id TEXT,
    published_at REAL,
    source_run_id TEXT
);

-- Lineage for enrichment (WP5)
-- We need to add source_run_id, input_hash to ai_post_analysis_v2 or similar.
-- Wait, I will use ALTER TABLE ADD COLUMN ... if missing in Python schema migrator, or just add them here if SQLite allows it simply.
-- Wait, the instructions say:
-- INSERT OR IGNORE INTO schema_migration (version, name) VALUES (10, 'medallion_control_plane');

-- Gold marts (WP6)
CREATE TABLE IF NOT EXISTS mart_post_signal (
    publish_id TEXT NOT NULL,
    period TEXT NOT NULL,
    post_id TEXT NOT NULL,
    as_of REAL,
    trend_score REAL,
    composite_value_score REAL,
    score_velocity REAL,
    comment_velocity REAL,
    resource_count INTEGER DEFAULT 0,
    analysis_provider TEXT,
    source_run_id TEXT,
    PRIMARY KEY (publish_id, period, post_id)
);

CREATE TABLE IF NOT EXISTS mart_post_knowledge (
    publish_id TEXT NOT NULL,
    post_id TEXT NOT NULL,
    domain_id TEXT,
    provider TEXT,
    model TEXT,
    analysis_version TEXT,
    analysis_json TEXT,
    generated_at REAL,
    source_run_id TEXT,
    PRIMARY KEY (publish_id, post_id)
);

CREATE TABLE IF NOT EXISTS mart_digest (
    publish_id TEXT NOT NULL,
    period TEXT NOT NULL,
    digest_id TEXT NOT NULL,
    generated_at REAL,
    source_run_id TEXT,
    PRIMARY KEY (publish_id, period)
);

-- Lineage migration
ALTER TABLE ai_post_analysis_v2 ADD COLUMN source_run_id TEXT;
ALTER TABLE ai_post_analysis_v2 ADD COLUMN input_hash TEXT;
