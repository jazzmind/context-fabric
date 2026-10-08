---
description: Show the active pack, prefix size, cache state, reused vs. new tokens, task tail, and compaction risk.
agent: build
---
!`python3 scripts/context_status.py $ARGUMENTS`

(Backend, URL and model resolve from `.context-fabric/config.json` / `CONTEXT_FABRIC_BACKEND`; pass `--backend`, `--base-url`, `--model` or `--probe` to override.)

Report the table above verbatim, as a markdown table. If "Last request reused" or "Cache
state" say "unknown" or show a probe failure, say so plainly and point at
`.context-fabric/logs/last-status-raw.json` rather than guessing at a cache-hit number.
