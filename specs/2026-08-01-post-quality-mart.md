# Spec: Post quality mart + relative-heat ranking for low-engagement sources

- Date: 2026-08-01
- Status: approved

## Problem

Reddit Radar crawls 6 niche AI subreddits (codex, ClaudeAI, artificial, AI_Agents,
Anthropic, claudeskills) whose posts rarely exceed a few dozen upvotes. The current
`trending_posts` ranking scores posts with absolute engagement only
(`log1p(score) + log1p(comments) + velocity + recency`), so nearly every post lands
in the same low band and "Hot Now" does not discriminate. Posts that are unusually
hot *for their own subreddit* or that spark deep debate relative to their size never
surface, and fresh posts have no quality signal until they accumulate raw votes.

## Goal

A derived-features mart (`mart_post_quality`) computed by a dedicated idempotent job
(`cli.py transform-quality`) that stores, per post, subreddit-relative heat
(score/comments ratio vs. the sub's rolling median and percentile), engagement/debate
ratios (comments-per-upvote, upvote_ratio) and a composite `quality_score`. The
`trending_posts` ranking consumes the mart so Hot Now (`/api/today`) ranks a post
with 8 upvotes that is 5x its subreddit's median above a 100-upvote post that is
merely average for its subreddit. No new UI tabs: the existing Hot Now view is
improved in place.

## Non-goals

- No new feed tab / page ("Bài mới & giá trị" deferred; only Hot Now ranking changes).
- No change to crawl, enrich, analysis, digest, roundup or story-export behavior.
- No re-scoring of historical mart rows beyond recomputation by the job.
- No changes to `fact_post_metrics` schema or the incremental collector.
- The live-DB migration (adding `mart_post_quality`) is a manual human step after
  spec approval, executed with a SQLite-safe backup; the automated criteria below
  only ever run against copy databases.

## Acceptance criteria

- [ ] `mart_post_quality` table and migration 13 exist in `reddit_crawler/schema.sql` with columns `post_id TEXT PRIMARY KEY, computed_at REAL, window_hours REAL, subreddit_id TEXT, sub_median_score REAL, sub_median_comments REAL, sub_post_count INTEGER, score_percentile REAL, comments_percentile REAL, score_ratio REAL, comments_ratio REAL, engagement_ratio REAL, upvote_ratio REAL, quality_score REAL` — verify: `grep -q "CREATE TABLE IF NOT EXISTS mart_post_quality" reddit_crawler/schema.sql && grep -q "(13, 'post_quality_mart')" reddit_crawler/schema.sql && .venv/bin/python -m unittest tests.test_storage`
- [ ] `cli.py transform-quality --hours 72` runs idempotently: on a fresh SQLite copy (built with the SQLite backup API from `reddit.db`), the first run writes rows for posts in the window and a second run writes the same row count with no errors — verify: `.venv/bin/python -m unittest tests.test_quality_mart`
- [ ] `mart_post_quality` rows for the live dataset are consistent: median/percentile are per-subreddit within the window, `0 <= score_percentile <= 1`, `0 <= upvote_ratio <= 1`, and `quality_score = 100 * (0.45 * relative_heat + 0.25 * engagement + 0.15 * upvote + 0.15 * freshness)` where `relative_heat = 0.5 * (score_ratio + comments_ratio) / (0.5 * (score_ratio + comments_ratio) + 1)` — verify: `.venv/bin/python -m unittest tests.test_quality_mart`
- [ ] `trending_posts` consumes the mart and ranks relative-heat first: in a synthetic DB where subreddit A posts have ~100 upvotes (their baseline) and subreddit B posts have ~8 upvotes with `score_ratio = 5.0` and `engagement_ratio = 2.0`, the subreddit B post ranks above the subreddit A average post in `period="day"` — verify: `.venv/bin/python -m unittest tests.test_analytics`
- [ ] Hot Now API exposes the quality fields and still returns 200 sorted by the new composite score — verify: `.venv/bin/python -m unittest tests.test_web_api`
- [ ] `quality_score` never overrides genuine outliers: a post with `latest_score >= 200` in any subreddit keeps a `score_percentile >= 0.9` regardless of subreddit baseline — verify: `.venv/bin/python -m unittest tests.test_analytics`
- [ ] Manual live rollout: backup `reddit.db` with the SQLite backup API, apply the schema on the copy, verify, then apply migration 13 to the live DB and run `cli.py transform-quality --hours 72` once — verify-manual: OWNER: run the documented steps and confirm `mart_post_quality` row count on the live DB plus Hot Now (`/api/today`) returns 200.

## Risk tier

- **R1** — reversible changes inside this repo; all automated verification runs on
  copy databases. The only production-touching action (migration 13 on live
  `reddit.db`) is a manual human step outside the automated criteria.

## Constraints

- Must not change the crawl/enrich/analysis/roundup pipeline; only `analytics.trending_posts`, `marts.py`, `cli.py`, `storage.py` and `schema.sql` are touched.
- `trending_posts` must stay backward compatible for callers (`/api/trending`, `/api/buzz`, `/api/today`, `jobs/enrich.py`); the mart is optional input — queries still work when the mart is empty.
- All existing tests keep passing (214 today); existing pipeline tests that mock `trending_posts` are untouched.
- No new external dependencies.
- Idempotency: re-running the job on the same window overwrites rows (`UPSERT`), never duplicates.

## Stop if

- More than the listed files (analytics.py, marts.py, cli.py, storage.py, schema.sql, web/app.py, tests) need editing.
- Any currently-passing test starts failing without the new formula being the cause.
- The mart job requires writes to the live `reddit.db` during automated verification.

## Interfaces

- `marts.py`: `def build_post_quality_mart(db_path: str | Path, *, hours: float = 72) -> dict` — returns `{"posts": n, "subreddits": n, "window_hours": hours}`; upserts `mart_post_quality` rows; read-only for everything else.
- `analytics.py`: `trending_posts` gains optional `use_mart: bool = True` and each item gains keys `quality_score, score_ratio, comments_ratio, engagement_ratio, upvote_ratio, score_percentile, comments_percentile`.
- `cli.py`: subcommand `transform-quality` with `--hours` (default 72) calling `build_post_quality_mart`.
- `storage.py`: `upsert_mart_post_quality(row)` raising `ValueError` when `post_id` is missing; `mart_post_quality` added to `_PRIMARY_KEYS`.

## Plan (filled at Plan stage)

1. `schema.sql`: add `mart_post_quality` DDL + migration 13 record.
2. `storage.py`: `_PRIMARY_KEYS` entry + `upsert_mart_post_quality`.
3. `marts.py`: implement `build_post_quality_mart` (per-subreddit window stats via one SQL aggregation, per-post ratios/percentiles, composite formula).
4. `analytics.py`: load mart in `trending_posts`, compute new `composite_value_score` from quality + AI/resource bonuses, keep raw fallback when mart empty.
5. `cli.py`: `transform-quality` subcommand.
6. Tests: `test_quality_mart.py` (mart + job idempotency + formula), extend `test_analytics` (relative-heat ranking + outlier guard), extend `test_web_api` (Hot Now fields).
7. Verify: compileall, full suite, frontend build unaffected (no UI change beyond sort), manual migration 13 + live `transform-quality` with backup.

## Decisions log (append during Build)

## Outcome (filled at Ship)
