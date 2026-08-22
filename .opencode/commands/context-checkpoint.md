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
avoids). Then, once you've finished editing the new pack file, run the prime script
yourself using your own shell/bash tool (do not ask me to run it) — substitute the actual
new version id, e.g. if the current pack above is `approval-flow:v3`, the checkpoint
script already scaffolded `approval-flow:v4`, so run:

`python3 scripts/context_prime.py --pack approval-flow:v4 --emit`

Do not reuse `$ARGUMENTS` verbatim here — that is the OLD pack id from the top of this
command, and re-priming it will simply be refused as unchanged. This step must run after
your own turn, not as part of loading this command, since the new version number only
exists once step 1's script has run.
