# Whole-task benchmark suite

`context_benchmark.py` compares four lanes on identical fresh workspace copies:

1. `ollama-baseline`
2. `ollama-context-fabric`
3. `omlx-baseline`
4. `omlx-context-fabric`

This is intentionally not a tokens/sec benchmark. The primary outputs are **task success** and
**wall-clock task completion**. It also captures first agent activity, OpenCode token/tool
signals, changed files, verification output, and Context Fabric pack/session metrics.

## Define tasks

Copy `benchmarks/tasks.example.json` and replace the examples with real tasks for the target
repository. Every task needs:

- `id`: stable label
- `prompt`: the exact coding task given to OpenCode
- `verify`: commands that independently decide whether the task is correct

Use tasks whose verification fails on the untouched baseline checkout. Otherwise an agent can
"succeed" by doing nothing.

## Run

```bash
python3 scripts/context_benchmark.py \
  --repo ~/Code/my-project \
  --tasks ~/Code/my-project/benchmarks/context-fabric-tasks.json \
  --repeat 3
```

The runner uses the backend endpoints/models from `.context-fabric/config.json` (or defaults),
but writes the same `localbench` OpenAI-compatible OpenCode provider in every workspace so the
harness layer is held constant.

Results land under `benchmarks/results/<timestamp>/`:

- `results.json` — machine-readable task/run metrics
- `report.md` — lane medians + paired Context Fabric effect by backend
- `raw/*.jsonl` — raw `opencode run --format json` event streams
- `workspaces/` — only retained with `--keep-workspaces`

Ollama's public API does not expose a stable cached-token counter, so a zero/unknown cache-read
metric must not be interpreted as a cache miss. Compare whole-task latency and task success.
