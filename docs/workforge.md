# WorkForge in Reddit Radar

Reddit Radar uses the sibling `../workforge` checkout. WorkForge produces an isolated
branch and worktree for one bounded run-spec, records observer evidence and stops on
the configured limits. It never merges or authorizes a production action.

## Local setup

From the Reddit Radar repository root:

```bash
# Install the sibling checkout in editable mode with all currently supported extras.
../workforge/.venv/bin/python -m pip install -e '../workforge[durable]'

# Read-only readiness check. This does not start agents, Temporal, Postgres or Docker.
scripts/workforge-doctor.sh
```

The doctor checks the sibling executable and Python extras, the Claude CLI, the git
and ignore boundaries, every real run-spec, and current lane status/overlap. A dirty
base repo is a warning rather than a setup failure, but do not start a lane until the
commit that should form its base exists: WorkForge creates new worktrees from `HEAD`
and does not copy uncommitted caller changes.

`AGENTS.md` is a tracked symlink to `CLAUDE.md`, so the same repository policy is read
by WorkForge dynamic prompts and by the other coding agents used here.

## Supported operating modes

| Mode | Reddit status | Requirements | Important limit |
|---|---|---|---|
| Direct dynamic | Default | authenticated `claude` CLI | process interruption is not resumable |
| Direct static | Adapter-specific | reviewed implementer and observer commands | fixed commands must carry the needed context |
| Temporal durable | Opt-in | Temporal server, worker and reviewed static commands | cannot be combined with `--dynamic` |
| Postgres queue | Opt-in | Temporal stack, separate Postgres control DB and dispatcher | `submit` only queues; dispatcher starts work |
| WorkForge dashboard | Opt-in, localhost only | initialized WorkForge Postgres DB | observes queue/run records, not direct JSONL-only runs |

The durable extras being importable does not mean the external services are enabled.
Do not add WorkForge Temporal, Postgres, dispatcher or dashboard processes to Reddit's
systemd deployment during an ordinary lane. The WorkForge control database is also
separate from the live `reddit.db`; never point WorkForge setup commands at that file.

## Default direct workflow

1. Derive one dependency-ready R1 work package from an approved product spec. Keep R3
   cutover, provider-backed batches, migration/restore and external publishing out.
2. Copy `workforge-specs/TEMPLATE.md`, then give every criterion exactly one
   backticked shell command whose exit status is authoritative. Combine multiple
   checks with `&&` inside that one command or split the criterion.
3. Confirm the intended prerequisite state is committed and the slug has never been
   used for another lane.
4. Validate, set all three limits, and start direct dynamic mode:

```bash
scripts/workforge-doctor.sh
../workforge/.venv/bin/workforge validate workforge-specs/<spec>.md
../workforge/.venv/bin/workforge run workforge-specs/<spec>.md \
  --dynamic --slug <unique-slug> --max-usd <budget> \
  --max-seconds <seconds> --max-iterations <count>
```

5. Inspect the lane and overlap evidence. WorkForge detects file overlap but does not
   resolve semantic conflicts:

```bash
../workforge/.venv/bin/workforge status --repo .
../workforge/.venv/bin/workforge conflicts --repo .
git diff HEAD workforge/<unique-slug>
```

6. Rerun the run-spec commands and the full repository definition of done against the
   candidate. Review `.workforge/runs/<slug>/events.jsonl` and the diff. Merge manually
   only after human approval; never infer production approval from an observer pass.

Dynamic mode is unattended and the isolated worktree is the primary repository
boundary. Interactive Claude permission allow-lists are not a substitute for scope:
keep production data, secrets and external effects out of every autonomous run-spec.
The ignored `.env`, `reddit.db`, raw payloads and reports are absent from a normal git
worktree, and a lane must not seek them elsewhere.

## Durable and queue opt-in

Only use this path after the operator approves the infrastructure, the static agent
commands and the budget. The current WorkForge release cannot generate dynamic
per-iteration prompts inside a Temporal workflow.

Temporal requires a server plus a continuously running worker:

```bash
temporal server start-dev --db-filename /tmp/workforge-temporal.db
../workforge/.venv/bin/workforge worker start
```

In a separate shell, a durable run uses reviewed static commands:

```bash
../workforge/.venv/bin/workforge run workforge-specs/<spec>.md \
  --durable --slug <unique-slug> \
  --implementer-cmd '<reviewed-command>' \
  --observer-cmd '<reviewed-read-only-command>' \
  --max-usd <budget> --max-iterations <count>
```

The queue additionally needs a dedicated Postgres DSN. Pass it at runtime; never put a
credential-bearing DSN or credentials inside the stored agent commands, repository,
spec, fixtures or logs.

```bash
../workforge/.venv/bin/workforge db init --dsn '<workforge-postgres-dsn>'
../workforge/.venv/bin/workforge submit workforge-specs/<spec>.md \
  --repo "$(pwd)" --slug <unique-slug> \
  --implementer-cmd '<reviewed-command>' \
  --observer-cmd '<reviewed-read-only-command>' \
  --max-usd <budget> --max-iterations <count> \
  --dsn '<workforge-postgres-dsn>'
../workforge/.venv/bin/workforge dispatcher run-once \
  --max-concurrent <count> --dsn '<workforge-postgres-dsn>'
```

`submit` is non-executing; `dispatcher run-once` starts all jobs that were queued when
it began and waits for them under the concurrency cap. Before concurrent work, ensure
packages have disjoint ownership where possible and keep `workforge conflicts` in the
review loop.

The optional dashboard must stay loopback-only:

```bash
../workforge/.venv/bin/workforge dashboard start \
  --host 127.0.0.1 --port 8420 --dsn '<workforge-postgres-dsn>'
```

Do not run any command in this section merely to prove documentation. Import checks
and WorkForge's fixture test suite are the safe default; live Temporal/Postgres tests
require intentionally provisioned local services.
