---
description: Assemble and hash the immutable prefix for a finalized context pack, then load it as this session's stable prefix.
agent: build
---
Prime context pack "$ARGUMENTS".

!`python3 scripts/context_prime.py --pack "$ARGUMENTS" --emit`

Everything above (between this line and the "IMMUTABLE PREFIX" marker inside the emitted
text) is now the immutable prefix for context pack $ARGUMENTS. From this point in the
session forward:

- Do not restate, reorder, re-summarize, or ask me to repeat anything in that prefix.
- Only append new tool calls, findings, and your own turns.
- Reference this pack by name and hash in your responses so drift is easy to spot.
- If the script above printed a refusal instead of a prefix (because a prior prime's hash
  doesn't match anymore), stop and run `/context-checkpoint` instead of forcing a re-prime.
