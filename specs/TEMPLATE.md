# Spec: {{TITLE}}

- Date: {{YYYY-MM-DD}}
- Status: draft | approved | building | blocked | done
  (building = a session is on it now; approved = scope approved and Plan complete, resumable by anyone — the supervisor auto-resumes approved/building specs; blocked = needs a human)

## Problem

<!-- What hurts today, for whom. One paragraph. -->
{{PROBLEM}}

## Goal

<!-- The observable end state: what a user or system can do after this ships. -->
{{GOAL}}

## Non-goals

<!-- What this deliberately does not cover. -->
{{NON_GOALS}}

## Acceptance criteria

Each criterion is machine-verifiable: paired with a command whose exit status decides pass/fail. A human-only criterion uses `verify-manual: OWNER: procedure`; unchecked manual criteria cannot enter an autonomous run.

- [ ] {{CRITERION_1}} — verify: `{{COMMAND}}`
- [ ] {{CRITERION_2}} — verify-manual: {{OWNER}}: {{PROCEDURE}}

## Risk tier

One line. Take the **highest** tier any criterion reaches — reversibility, blast radius, data sensitivity, and whether anything outside this repo can see the effect. An autonomous run may never lower its own tier.

- **R0** — read/search/report only. Runs unattended.
- **R1** — reversible change inside this repo. Runs unattended; a human reviews before integration. *(the normal tier for a factory spec)*
- **R2** — shared test/staging, release candidate, dependency or CI changes. Needs explicit human approval before the run starts.
- **R3** — production, irreversible, secrets/identity, anything published or deployed, data migration. A named human does it; the run stops and asks.

Tier: {{R0|R1|R2|R3}} — {{one line: why this tier}}

## Constraints

<!-- Deadlines, compatibility, performance budgets, things that must not break. -->
{{CONSTRAINTS}}

## Stop if (only if a run could plausibly overreach)

Concrete tripwires that mean pause and ask a human, even mid-run — not scope description (that's Non-goals), a live circuit breaker: "touching more than N files outside {{path}}", "a test that already passes starts failing", "the same file edited by two different criteria".

- {{TRIPWIRE_CONDITION}}

## Interfaces (only if criteria depend on each other)

If one criterion's work is consumed by another (a function signature, a schema, a file format), name it exactly here — a fresh session working a later criterion sees only its own line, not the earlier ones, and needs the exact name/type/shape, not a description.

- {{PRODUCED_BY_CRITERION_N}}: {{exact signature/schema/format}}

## Plan (filled at Plan stage)

<!-- Files to touch, order of work, known risks. -->
{{PLAN}}

## Decisions log (append during Build)

- {{DECISION_DATE}} — {{DECISION_AND_RATIONALE}}

## Outcome (filled at Ship)

<!-- What shipped vs the spec, deviations and evidence per criterion. -->
{{OUTCOME_WITH_EVIDENCE}}
