---
description: Draft a versioned context_pack for a task from the static code graph, then finalize invariants/acceptance tests/subtasks.
agent: plan
---
Task: $ARGUMENTS

Step 1 — draft (cheap, no reasoning needed from you yet):

!`python3 scripts/context_index.py 2>/dev/null; python3 scripts/context_plan.py --task "$ARGUMENTS"`

Step 2 — you are the quality lane now. The draft pack printed above has empty
`invariants`, `acceptance_tests`, and a placeholder `unknowns` entry. Read the selected
source slices (the "Code cone" files listed above), then:

1. Resolve or restate every entry in `unknowns`.
2. Write explicit `invariants` — things the implementation must not violate.
3. Write concrete `acceptance_tests` — checkable, ideally pointing at real test names.
4. Confirm the code cone is sufficient (nothing relevant missing) and minimal (nothing
   irrelevant included) — add or drop `source_slices` entries if needed.
5. Refine `subtasks` into a context-stable decomposition for this specific task.

Then edit the draft pack file directly at the path printed above (it is schema-validated
against `schema/context-pack.schema.json` — see `docs/context-pack-spec.md` for the full
spec and a worked example) and tell me it's ready to prime.

Do not run `/context-prime` yet — that happens once you confirm the pack is finalized.
