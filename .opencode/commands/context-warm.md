---
description: Warm the backend's prefix cache with the active pack (no-op on backends without explicit warmup, e.g. Ollama).
agent: build
---
!`python3 scripts/context_warm.py $ARGUMENTS`

Report what the script printed. A no-op on Ollama is expected, not a failure.
