# Benchmarking Context Fabric

The benchmark suite is designed to answer one question:

> Does deterministic task-context management improve verified coding-task completion enough to
> justify its overhead on this repository/model/backend?

## Four standard lanes

```text
ollama-baseline
ollama-context-fabric
omlx-baseline
omlx-context-fabric
```

All lanes use `opencode run --format json` and a generated OpenAI-compatible `localbench`
provider so the agent harness is held as constant as possible.

## Task design

A benchmark task must have an independent verification command that fails before the task is
implemented. Good examples are bug reproductions, missing feature tests, migration fixtures, or
specific lint/type/test failures.

```json
{
  "tasks": [
    {
      "id": "approval-timeout",
      "prompt": "Fix the approval timeout bug described in ISSUE.md, add a regression test, and run verification.",
      "verify": ["pytest -q tests/test_approval.py"]
    }
  ]
}
```

Avoid tasks whose verifier is just the repository's already-green test suite; an agent can pass
those without doing the requested work.

## Run

```bash
python3 scripts/context_benchmark.py \
  --repo ~/Code/my-project \
  --tasks ~/Code/my-project/context-fabric-bench.json \
  --repeat 4
```

Backend model/URL overrides use the same environment variables as the adapters, which makes it
easy to pin exact checkpoints without editing benchmark code:

```bash
OLLAMA_MODEL=qwen3.8:27b-mlx \
OMLX_QUALITY_MODEL=Qwen3.8-27B-8bit \
OLLAMA_BASE_URL=http://127.0.0.1:11434 \
OMLX_BASE_URL=http://127.0.0.1:8000 \
python3 scripts/context_benchmark.py --repo ~/Code/my-project --tasks bench.json --repeat 4
```

Use at least four repeats when comparing all four lanes. The runner deterministically rotates
lane order across repeats to reduce model-load/cache-warmth bias; four repeats gives every lane
each ordinal position once. Five or more is useful when runtime permits.

## Metrics

Primary:

1. task success rate;
2. wall-clock task completion.

Secondary diagnostics:

- first OpenCode agent activity;
- input/output/cache-read token fields found in JSON events;
- tool calls;
- changed files;
- verification duration/output;
- active Context Fabric pack and approximate prefix size.

Do not rank backends solely by decode tokens/sec. Agentic coding includes repeated prefill,
tool latency, bad turns, re-reading, compaction, and verification; whole-task wall time captures
those interactions.

## Ollama cache telemetry caveat

Ollama may provide excellent automatic cache behavior without exposing a stable cached-token
counter through the API used by the suite. Zero/unknown cache-read tokens in an Ollama lane are
therefore not evidence of a cache miss.

## Reproducibility

Each run gets a fresh repository copy. Context Fabric generated state and `.git` are excluded
from the source copy, Context Fabric is installed only in CF lanes, and a new local git snapshot
is created before the agent starts so changed files can be measured.

Raw OpenCode JSONL is retained under `raw/` for schema/audit troubleshooting.
