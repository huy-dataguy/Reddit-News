# Run-spec — Wave 0 production spec registry

## Context

Implement only WP0 of `specs/2026-07-28-internal-production-program.md`: create a
machine-readable registry and automated consistency checks. Do not build the data
pipeline, change runtime services, initialize external resources, call providers or
touch production data.

The registry must represent the authority/dependency table already approved in the
product spec. Use JSON and the Python standard library unless a separately approved
dependency change says otherwise. If the product spec currently names YAML, update
that interface reference to the chosen JSON path in the same change.

## Acceptance Criteria

1. A versioned `specs/production-program.json` exists, parses with Python's standard
   library, and contains every authoritative 2026-07-28 product spec with path,
   status, risk tier, release wave and dependencies.
   Verify: `.venv/bin/python -m unittest tests.test_spec_registry.SpecRegistryTests.test_registry_contract`

2. Automated validation rejects unknown spec paths, invalid statuses, dependency
   cycles, dependencies assigned to a later wave, more than one `building` spec and
   the three stale conflicting specs becoming `approved` or `building`.
   Verify: `.venv/bin/python -m unittest tests.test_spec_registry.SpecRegistryTests.test_invalid_registries`

3. The internal-production program's machine-readable interface points to the actual
   registry path and its authority/dependency rows agree with the registry.
   Verify: `.venv/bin/python -m unittest tests.test_spec_registry.SpecRegistryTests.test_program_document_matches_registry`

4. The change does not track or modify live/runtime data, and the entire Reddit Radar
   backend definition of done remains green.
   Verify: `.venv/bin/python -m compileall -q reddit_crawler jobs web cli.py && .venv/bin/python -m unittest discover -s tests`

## Constraints

- Do not read or write `.env`, `reddit.db`, `raw/`, `reports/` or DB backups.
- Do not change any product spec from `draft` to `approved` or `building`.
- Do not mark historical specs `done`; this run only makes their blocked state and
  replacement authority machine-verifiable.
- Do not add PyYAML or another dependency just to parse the registry.
- Keep implementation limited to the registry, its validator/tests and the necessary
  interface-path update in the program spec.
