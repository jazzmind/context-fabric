---
description: At a task boundary, produce a structured handoff and scaffold the next context_pack version instead of degrading the current one.
agent: build
---
Current pack: $ARGUMENTS

Step 1 — scaffold the next version:

!`python3 scripts/context_checkpoint.py --pack "$ARGUMENTS"`

Step 2 — fill in the `checkpoint` block of the new pack file printed above, from your own
knowledge of this session (not from re-reading the whole history — you were there):

- `changed_files` — every file you touched since $ARGUMENTS was primed
- `verified_facts` — things you confirmed true by reading code or running tests
- `failed_hypotheses` — approaches you tried and ruled out, and why
- `test_status` — current pass/fail state
- `next_decision` — the single next decision or action, stated concretely

Step 3 — using that checkpoint plus a fresh look at the code graph, re-derive
`source_slices`, `invariants`, and `acceptance_tests` for the next unit of work (don't
carry the old pack's forward blindly — that's the "silent degradation" this whole design
avoids). Then prime it:

!`python3 scripts/context_prime.py --pack "$ARGUMENTS"`

(That last line is a placeholder — replace `$ARGUMENTS` above with the new version, e.g.
if the current pack is `approval-flow:v3`, prime `approval-flow:v4`, once you've finished
step 3.)
