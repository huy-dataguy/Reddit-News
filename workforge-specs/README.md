# WorkForge run-specs

This directory contains bounded, machine-verifiable implementation units derived
from the approved product specs in `../specs/`.

Product specs can contain manual R3 gates and broad dependency plans. WorkForge
run-specs cannot: every acceptance criterion must have a command whose exit status
decides pass/fail. A WorkForge lane may prepare a release candidate, but it must not
perform a live DB migration, systemd cutover, paid-provider batch or external publish.

Typical flow:

```bash
../workforge/.venv/bin/workforge validate workforge-specs/0001-wave0-spec-registry.md
../workforge/.venv/bin/workforge run workforge-specs/0001-wave0-spec-registry.md \
  --dynamic --slug wave0-spec-registry --max-usd 1.00 --max-iterations 5
../workforge/.venv/bin/workforge status --repo .
../workforge/.venv/bin/workforge conflicts --repo .
```

Review the resulting branch/worktree and rerun the repository definition of done
before merging. Nothing is auto-merged.
