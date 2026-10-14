---
description: Build the whole project from its specs, with SpecGuard tracking coverage.
---

<!-- specguard-managed: true -->

# /goal — Build everything from the specs

You are building this typescript project from its Living Specifications.

## Steps

1. Run `specguard gap-analysis` (or the `specguard_gap_analysis` MCP tool). Read
   every plan it writes under `.specguard/plans/*.md`.
2. Read `specs/**` — the specs are the source of truth. Read a module's spec
   before implementing it.
3. Build an ordered module list from the plans' suggested files / steps.
4. For each module, in dependency order:
   - Implement the code (language: typescript).
   - Run `specguard gap-analysis` and confirm the spec is no longer "unimplemented".
   - Generate tests: `specguard generate --spec <key>`, then `specguard heal --all`
     (tests run via `npm test`).
5. Stop when `specguard status` reports the target coverage and all plans are
   implemented.

## Rules

- NEVER edit a spec to make a gap disappear — implement the code.
- A `heal` `app-bug` is a real defect: fix the code, do not weaken the test.
- Keep going module-by-module; report progress after each.
