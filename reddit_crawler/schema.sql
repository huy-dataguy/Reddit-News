-- ============================================================================
-- REDDIT RADAR — STANDARDIZED STAR SCHEMA & MEDALLION DATAWAREHOUSE SCHEMA
-- Database Engine: SQLite 3 (Standard SQL compliant for Postgres/BigQuery)
-- Architecture: Medallion Architecture (Bronze -> Silver Star Schema -> Gold Marts -> User Layer)
-- ============================================================================

CREATE TABLE IF NOT EXISTS schema_migration (
    version             INTEGER PRIMARY KEY,
    name                TEXT NOT NULL,
    applied_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO schema_migration (version, name) VALUES (1, 'base_star_schema');
INSERT OR IGNORE INTO schema_migration (version, name) VALUES (2, 'integrity_trending_reports_web');
INSERT OR IGNORE INTO schema_migration (version, name) VALUES (3, 'article_and_comment_enrichment');
INSERT OR IGNORE INTO schema_migration (version, name) VALUES (4, 'ai_digest_and_media');
INSERT OR IGNORE INTO schema_migration (version, name) VALUES (5, 'domain_and_comment_intelligence');
INSERT OR IGNORE INTO schema_migration (version, name) VALUES (6, 'extracted_resources_hub');
INSERT OR IGNORE INTO schema_migration (version, name) VALUES (7, 'longread_magazine');
INSERT OR IGNORE INTO schema_migration (version, name) VALUES (8, 'knowledge_intelligence');
INSERT OR IGNORE INTO schema_migration (version, name) VALUES (9, 'analysis_v2');
INSERT OR IGNORE INTO schema_migration (version, name) VALUES (10, 'medallion_control_plane_and_user_layer');
INSERT OR IGNORE INTO schema_migration (version, name) VALUES (11, 'hourly_social_roundup');
INSERT OR IGNORE INTO schema_migration (version, name) VALUES (12, 'drop_ai_social_post');
INSERT OR IGNORE INTO schema_migration (version, name) VALUES (13, 'post_quality_mart');

-- ============================================================================
-- 1. DIMENSION TABLES (KÍCH THƯỚC CHỦ THỂ)
-- ============================================================================

CREATE TABLE IF NOT EXISTS dim_subreddit (
    subreddit_id        TEXT PRIMARY KEY,   -- Khóa tự nhiên Reddit (vd: 't5_2qh16')
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
    author_name         TEXT PRIMARY KEY,   -- Khóa tự nhiên ổn định (username)
    author_id           TEXT,               -- ID người dùng ('t2_xxxxx')
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
    weekday             INTEGER                -- 0 = Thứ Hai ... 6 = Chủ Nhật
);

-- ============================================================================
-- 2. FACT TABLES (BẢNG SỰ KIỆN CHÍNH - SILVER LAYER)
-- ============================================================================

CREATE TABLE IF NOT EXISTS fact_post (
    post_id             TEXT PRIMARY KEY,   -- ID gọn (vd: '1rfgu9a')
    fullname            TEXT,               -- Fullname Reddit ('t3_1rfgu9a')
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
    -- Đo lường (Measures) --
    score               INTEGER,
    ups                 INTEGER,
    upvote_ratio        REAL,
    num_comments        INTEGER,
    num_crossposts      INTEGER,
    total_awards        INTEGER,
    fetched_at          REAL,
    source_stream       TEXT                -- 'new', 'hot', hoặc 'both'
);

CREATE TABLE IF NOT EXISTS fact_comment (
    comment_id          TEXT PRIMARY KEY,   -- ID gọn
    fullname            TEXT,               -- Fullname Reddit ('t1_xxxxx')
    post_id             TEXT REFERENCES fact_post(post_id),
    subreddit_id        TEXT REFERENCES dim_subreddit(subreddit_id),
    author_name         TEXT REFERENCES dim_author(author_name),
    parent_fullname     TEXT,               -- t3_ (post) hoặc t1_ (comment cha)
    created_date_key    INTEGER REFERENCES dim_date(date_key),
    created_utc         REAL,
    depth               INTEGER,
    body                TEXT,
    -- Đo lường (Measures) --
    score               INTEGER,
    ups                 INTEGER,
    controversiality    INTEGER,
    total_awards        INTEGER,
    is_submitter        INTEGER,            -- OP bài viết
    fetched_at          REAL
);

CREATE TABLE IF NOT EXISTS fact_post_metrics (
    post_id             TEXT REFERENCES fact_post(post_id),
    observed_at         REAL,               -- Mốc thời gian quan sát (epoch)
    score               INTEGER,
    ups                 INTEGER,
    upvote_ratio        REAL,
    num_comments        INTEGER,
    PRIMARY KEY (post_id, observed_at)
);

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

CREATE TABLE IF NOT EXISTS fact_post_media (
    post_id             TEXT PRIMARY KEY REFERENCES fact_post(post_id),
    image_url           TEXT,
    thumbnail_url       TEXT,
    media_type          TEXT,
    width               INTEGER,
    height              INTEGER,
    fetched_at          REAL
);

CREATE TABLE IF NOT EXISTS fact_extracted_resource (
    resource_id         TEXT PRIMARY KEY,   -- SHA256 / Hash URL
    post_id             TEXT REFERENCES fact_post(post_id),
    comment_id          TEXT REFERENCES fact_comment(comment_id),
    platform_id         TEXT DEFAULT 'reddit',
    url                 TEXT NOT NULL,
    domain              TEXT,               -- github.com, arxiv.org, huggingface.co...
    resource_type       TEXT NOT NULL,      -- github_repo | arxiv_paper | tech_blog | documentation | tool | video | other
    title               TEXT,
    description         TEXT,
    context_snippet     TEXT,               -- Ngữ cảnh xung quanh link
    author_name         TEXT,
    score               INTEGER DEFAULT 0,  -- Điểm upvote của comment/post
    extracted_at        REAL
);

-- ============================================================================
-- 3. STATE & CONTROL PLANE TABLES (MEDALLION CONTROL PLANE)
-- ============================================================================

CREATE TABLE IF NOT EXISTS crawl_state (
    scope               TEXT PRIMARY KEY,   -- vd: 'sub:technology:new'
    last_utc            REAL,
    last_fullname       TEXT,
    updated_at          REAL
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

CREATE TABLE IF NOT EXISTS pipeline_run (
    run_id              TEXT PRIMARY KEY,
    pipeline            TEXT NOT NULL,
    status              TEXT NOT NULL,
    started_at          REAL NOT NULL,
    finished_at         REAL,
    input_count         INTEGER DEFAULT 0,
    output_count        INTEGER DEFAULT 0,
    error_summary       TEXT
);

CREATE TABLE IF NOT EXISTS bronze_object (
    object_id           TEXT PRIMARY KEY,
    run_id              TEXT REFERENCES pipeline_run(run_id),
    entity_type         TEXT NOT NULL,
    relative_path       TEXT NOT NULL,
    sha256              TEXT NOT NULL,
    row_count           INTEGER DEFAULT 0,
    min_fetched_at      REAL,
    max_fetched_at      REAL,
    transform_status    TEXT NOT NULL DEFAULT 'pending',
    transformed_at      REAL
);

CREATE TABLE IF NOT EXISTS data_quality_result (
    run_id              TEXT NOT NULL,
    layer               TEXT NOT NULL,
    check_name          TEXT NOT NULL,
    status              TEXT NOT NULL,
    observed_value      TEXT,
    threshold           TEXT,
    checked_at          REAL NOT NULL,
    PRIMARY KEY (run_id, layer, check_name)
);

CREATE TABLE IF NOT EXISTS serving_state (
    singleton_id        TEXT PRIMARY KEY DEFAULT 'current',
    current_publish_id  TEXT,
    published_at        REAL,
    source_run_id       TEXT
);

-- ============================================================================
-- 4. AI & KNOWLEDGE LAYER (LLM ANALYSIS & DIGESTS)
-- ============================================================================

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
    error               TEXT,
    source_run_id       TEXT,
    input_hash          TEXT
);

CREATE TABLE IF NOT EXISTS ai_social_roundup (
    cluster_id          TEXT PRIMARY KEY,
    hour_start          REAL NOT NULL,
    source_post_ids     TEXT NOT NULL,          -- JSON array
    total_score         INTEGER NOT NULL DEFAULT 0,
    total_comments      INTEGER NOT NULL DEFAULT 0,
    n_posts             INTEGER NOT NULL DEFAULT 0,
    topic_vi            TEXT,
    domain_id           TEXT,
    provider            TEXT NOT NULL,
    model               TEXT,
    status              TEXT NOT NULL,
    title               TEXT,
    full_post_text      TEXT,
    payload_json        TEXT,
    input_tokens        INTEGER,
    output_tokens       INTEGER,
    generated_at        REAL,
    error               TEXT
);

CREATE TABLE IF NOT EXISTS ai_buzz_bulletin (
    bulletin_id         TEXT PRIMARY KEY,
    period              TEXT NOT NULL,           -- 'week' | 'month'
    window_start        REAL NOT NULL,
    window_end          REAL NOT NULL,
    provider            TEXT NOT NULL,
    model               TEXT,
    status              TEXT NOT NULL,
    title               TEXT,
    full_bulletin_text  TEXT,
    payload_json        TEXT,                    -- {stories:[{badge,headline,snippet,source_post_ids}]}
    source_count        INTEGER,
    input_tokens        INTEGER,
    output_tokens       INTEGER,
    generated_at        REAL,
    error               TEXT
);

-- ============================================================================
-- 5. GOLD MARTS (DATA MARTS DÙNG PHỤC VỤ WEB & CONSUMERS)
-- ============================================================================

CREATE TABLE IF NOT EXISTS mart_post_signal (
    publish_id            TEXT NOT NULL,
    period                TEXT NOT NULL,
    post_id               TEXT NOT NULL,
    as_of                 REAL,
    trend_score           REAL,
    composite_value_score REAL,
    score_velocity        REAL,
    comment_velocity      REAL,
    resource_count        INTEGER DEFAULT 0,
    analysis_provider     TEXT,
    source_run_id         TEXT,
    PRIMARY KEY (publish_id, period, post_id)
);

CREATE TABLE IF NOT EXISTS mart_post_knowledge (
    publish_id          TEXT NOT NULL,
    post_id             TEXT NOT NULL,
    domain_id           TEXT,
    provider            TEXT,
    model               TEXT,
    analysis_version    TEXT,
    analysis_json       TEXT,
    generated_at        REAL,
    source_run_id       TEXT,
    PRIMARY KEY (publish_id, post_id)
);

CREATE TABLE IF NOT EXISTS mart_digest (
    publish_id          TEXT NOT NULL,
    period              TEXT NOT NULL,
    digest_id           TEXT NOT NULL,
    generated_at        REAL,
    source_run_id       TEXT,
    PRIMARY KEY (publish_id, period)
);

-- ============================================================================
-- 6. USER INTERACTION & PERSISTENCE LAYER (TẦNG LƯU TRỮ NGƯỜI DÙNG)
-- ============================================================================

CREATE TABLE IF NOT EXISTS user_bookmark (
    post_id             TEXT PRIMARY KEY REFERENCES fact_post(post_id),
    saved_at            REAL NOT NULL,
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS user_read_state (
    post_id             TEXT PRIMARY KEY REFERENCES fact_post(post_id),
    read_at             REAL NOT NULL,
    read_count          INTEGER DEFAULT 1
);

-- ============================================================================
-- 7. PERFORMANCE INDEXES (CHỈ MỤC TỐI ƯU TRUY VẤN)
-- ============================================================================

CREATE INDEX IF NOT EXISTS ix_post_sub            ON fact_post(subreddit_id);
CREATE INDEX IF NOT EXISTS ix_post_author         ON fact_post(author_name);
CREATE INDEX IF NOT EXISTS ix_post_date           ON fact_post(created_date_key);
CREATE INDEX IF NOT EXISTS ix_cmt_post            ON fact_comment(post_id);
CREATE INDEX IF NOT EXISTS ix_cmt_author          ON fact_comment(author_name);
CREATE INDEX IF NOT EXISTS ix_cmt_parent          ON fact_comment(parent_fullname);
CREATE INDEX IF NOT EXISTS ix_metrics_post        ON fact_post_metrics(post_id);
CREATE INDEX IF NOT EXISTS ix_article_status      ON fact_article_content(status, fetched_at);
CREATE INDEX IF NOT EXISTS ix_enrichment_kind     ON enrichment_state(kind, status);
CREATE INDEX IF NOT EXISTS ix_ai_digest_period    ON ai_digest(period, generated_at DESC);
CREATE INDEX IF NOT EXISTS ix_ai_post_analysis_t  ON ai_post_analysis(generated_at DESC);
CREATE INDEX IF NOT EXISTS ix_ai_post_v2_time     ON ai_post_analysis_v2(generated_at DESC);
CREATE INDEX IF NOT EXISTS ix_resource_post       ON fact_extracted_resource(post_id);
CREATE INDEX IF NOT EXISTS ix_resource_type       ON fact_extracted_resource(resource_type, score DESC);
CREATE INDEX IF NOT EXISTS ix_resource_domain     ON fact_extracted_resource(domain);
CREATE INDEX IF NOT EXISTS ix_user_bookmark_saved ON user_bookmark(saved_at DESC);
CREATE INDEX IF NOT EXISTS ix_user_read_time      ON user_read_state(read_at DESC);

-- Migration 12: old 1:1 social drama post replaced by hourly roundup (ai_social_roundup)
DROP TABLE IF EXISTS ai_social_post;

-- Migration 13: post quality mart — derived features for low-engagement sources
CREATE TABLE IF NOT EXISTS mart_post_quality (
    post_id             TEXT PRIMARY KEY REFERENCES fact_post(post_id),
    computed_at         REAL NOT NULL,
    window_hours        REAL NOT NULL,
    subreddit_id        TEXT,
    sub_median_score    REAL,
    sub_median_comments REAL,
    sub_post_count      INTEGER,
    score_percentile    REAL,
    comments_percentile REAL,
    score_ratio         REAL,
    comments_ratio      REAL,
    engagement_ratio    REAL,
    upvote_ratio        REAL,
    quality_score       REAL
);
CREATE INDEX IF NOT EXISTS ix_mart_quality_score ON mart_post_quality(quality_score DESC);

-- Migration 14: fact_post.source_stream — provenance của luồng crawl ('new'/'hot'/'both').
-- DB mới đã có cột qua CREATE TABLE ở trên; DB cũ được ALTER bằng cmd_migrate (guarded).
INSERT OR IGNORE INTO schema_migration (version, name) VALUES (14, 'add_fact_post_source_stream');
INSERT OR IGNORE INTO schema_migration (version, name) VALUES (15, 'ai_buzz_bulletin');
