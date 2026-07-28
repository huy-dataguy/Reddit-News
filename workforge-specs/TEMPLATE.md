# Run-spec — <bounded implementation unit>

## Context

Name the approved product spec and the exact work package. State what already exists,
what this lane may change and what it must leave untouched.

## Acceptance Criteria

1. Describe one observable result.
   Verify: `<one shell command whose exit status decides this criterion>`

2. Describe the next observable result.
   Verify: `<one shell command whose exit status decides this criterion>`

## Risk Tier

R1 — reversible repository-only implementation. R2 requires explicit approval before
the lane. Move every R3 or human-only action back to the product spec; do not put a
`verify-manual:` item in an autonomous run-spec.

## Constraints

- Do not read or write `.env`, `reddit.db`, `raw/`, `reports/` or DB backups.
- Do not call real Reddit, OpenAI, Gemini, article-fetch or notification services.
- Do not deploy, publish, migrate, restore, merge or push.
- Preserve unrelated changes and keep the implementation inside this work package.

## Stop Conditions

- A required change would cross the declared scope or risk tier.
- A prerequisite interface differs from the approved product spec.
- The same verification failure repeats without new evidence.

## Plan

List the files, order of work and known risks before approving the lane.

## Decisions Log

Append non-obvious decisions and verification evidence during the lane.

## Outcome

Record what changed, deviations and the command/result for each criterion before
manual integration.
