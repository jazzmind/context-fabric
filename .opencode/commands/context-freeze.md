---
description: Freeze a finalized draft pack: canonicalize the prefix and record its sha256. Refuses on empty invariants/tests or over-budget prefixes.
agent: build
---
!`python3 scripts/context_freeze.py --pack $ARGUMENTS`

Report the result. If freeze refused, quote the reason and fix the draft (do not pass --force unless the user asked).
