# Reddit Radar

Guidance for coding agents and WorkForge lanes working in this repository.

## Product

Reddit Radar continuously collects Reddit technology signals, preserves replayable
raw events, scores trends, enriches discussions and linked resources, generates
grounded Vietnamese intelligence, and serves a FastAPI + React dashboard. The
internal-production target and dependency order are defined by
`specs/2026-07-28-internal-production-program.md`.

## Environment

- Python 3 virtualenv: `.venv/`
- SQLite live database: `reddit.db`
- Backend: FastAPI in `web/app.py`
- Frontend: React 19/Vite in `web/frontend/`
- Central CLI: `cli.py`
- Services and timers: `deploy/systemd/`
- WorkForge binary: `../workforge/.venv/bin/workforge`

The agent-factory standards remain applicable:

- `../agent-factory/standards/workflow.md`
- `../agent-factory/standards/definition-of-done.md`
- `../agent-factory/standards/engineering.md`
- `../agent-factory/standards/ground-truth.md`
- `../agent-factory/standards/memory.md`
- `../agent-factory/standards/debugging.md`

## Commands

```bash
# First-time Reddit Radar environment
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# Full backend definition of done
.venv/bin/python -m compileall -q reddit_crawler jobs web cli.py && \
  .venv/bin/python -m unittest discover -s tests

# Frontend production build
npm --prefix web/frontend run build

# Local dashboard and scheduled collector entrypoints
.venv/bin/python cli.py serve
.venv/bin/python cli.py incremental

# Export the top analyzed story to the sibling MediaWorkflow project
.venv/bin/python cli.py story-export --top

# Validate one bounded WorkForge run-spec
../workforge/.venv/bin/workforge validate workforge-specs/<spec>.md

# Run only after the run-spec and budget are approved
../workforge/.venv/bin/workforge run workforge-specs/<spec>.md \
  --dynamic --slug <slug> --max-usd <budget> --max-iterations <count>

# Inspect lanes; WorkForge never auto-merges
../workforge/.venv/bin/workforge status --repo .
../workforge/.venv/bin/workforge conflicts --repo .
```

Use unittest, not pytest, for Reddit Radar tests. Default automated tests must not
make real Reddit, OpenAI, Gemini, article-fetch or notification calls.

The central CLI also contains bounded crawl/backfill/report/enrich/AI commands. Read
`cli.py --help` rather than copying a stale command list into an agent change.

## WorkForge policy

- Product specs in `specs/` describe architecture, human approval and production
  gates. They are not passed directly to WorkForge.
- WorkForge specs in `workforge-specs/` are small implementation units. They use a
  numbered `## Acceptance Criteria` list and a runnable `Verify:` command for every
  item.
- Run one dependency-ready work package at a time. Do not turn the whole production
  program into one agent run.
- Use unique slugs. Review `.workforge/worktrees/<slug>` and the observer evidence;
  merge manually only after the base-repo verification passes.
- Never run an R3 live cutover, systemd deployment, provider-backed AI batch, DB
  migration or restore from an autonomous WorkForge lane.

## Production data and secret rules

- `reddit.db` is a live production database and may have a collector writing through
  WAL. Never delete, reset, migrate, vacuum, restore over or test against it in place.
- Storage/schema changes must be verified on a fresh copy made with a SQLite-safe
  backup method, not a raw copy of a database being written.
- `.env` contains real API keys. Never print it, stage it, commit it or copy values
  into fixtures/logs/prompts.
- `raw/`, `reports/`, DB backups, `.workforge/`, `.venv/`, `node_modules/` and build
  artifacts are runtime/generated data and must stay untracked.
- Web stays read-only and bound to `127.0.0.1` for the current internal-only release.
- Web requests do not call LLMs or fetch arbitrary source URLs.
- The client ID embedded in code is only a public/testing fallback. Long-running
  production collection needs the registered Reddit app configured through `.env`.
- This project is a direct sibling of `agent-factory` and `workforge`; do not rewrite
  those relative references as if Reddit lived under another parent directory.
- Preserve unrelated user changes. Do not use destructive Git commands.

## Definition of done

- The run-spec's own verification commands pass in its isolated worktree.
- The repository-wide backend command above passes.
- Frontend changes also pass the production build and relevant browser/contract test.
- Schema/storage changes pass on a copy of the real DB and include rollback evidence.
- No secret, live DB, raw payload, report, backup or WorkForge event log is tracked.
- The observer is independent; a passing observer does not authorize auto-merge or a
  production action.

## Known external blocker

Generic `story-export` must not be published until MediaWorkflow removes the
Spotify-specific hard-coded composition text and verifies a non-Spotify golden story.
