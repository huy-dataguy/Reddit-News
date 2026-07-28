# WorkForge run-specs

This directory contains bounded, machine-verifiable implementation units derived
from the approved product specs in `../specs/`.

Product specs can contain manual R3 gates and broad dependency plans. WorkForge
run-specs cannot: every acceptance criterion must have a command whose exit status
decides pass/fail. A WorkForge lane may prepare a release candidate, but it must not
perform a live DB migration, systemd cutover, paid-provider batch or external publish.

Start from [`TEMPLATE.md`](TEMPLATE.md). The current validator accepts numbered items
or checkboxes and case-insensitive `Verify:`, but Reddit Radar keeps one backticked
shell command per criterion. A manual gate stays in the product spec; copying
`verify-manual:` into a run-spec intentionally makes validation fail.

Typical flow:

```bash
scripts/workforge-doctor.sh
../workforge/.venv/bin/workforge validate workforge-specs/0001-wave0-spec-registry.md
../workforge/.venv/bin/workforge run workforge-specs/0001-wave0-spec-registry.md \
  --dynamic --slug wave0-spec-registry --max-usd 1.00 --max-seconds 3600 \
  --max-iterations 5
../workforge/.venv/bin/workforge status --repo .
../workforge/.venv/bin/workforge conflicts --repo .
```

Review the resulting branch/worktree and rerun the repository definition of done
before merging. Nothing is auto-merged. WorkForge creates the lane from committed
`HEAD`, not from uncommitted files in the caller's working tree.

Use direct `--dynamic` mode by default. Temporal durable runs and the Postgres-backed
queue/dashboard are installed-capable but deliberately not initialized for Reddit
Radar; they require separate operator approval and static implementer/observer
commands. See [`../docs/workforge.md`](../docs/workforge.md) for the mode matrix and
safe opt-in procedure.
