---
description: Show the active pack, prefix size, cache state, reused vs. new tokens, task tail, and compaction risk.
agent: build
---
!`python3 scripts/context_status.py --base-url "${OMLX_BASE_URL:-http://localhost:8000}" --model "${OMLX_QUALITY_MODEL:-Qwen3.8-27B-8bit}"`

Report the table above verbatim, as a markdown table. If "Last request reused" or "Cache
state" say "unknown" or show a probe failure, say so plainly and point at
`.context-fabric/logs/last-status-raw.json` rather than guessing at a cache-hit number.
