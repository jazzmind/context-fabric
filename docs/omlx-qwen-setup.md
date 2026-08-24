# Expert local setup: oMLX + Qwen3.8-27B + OpenCode

oMLX remains Context Fabric's expert backend for users who want explicit cache telemetry,
persistent paged-cache behavior, and direct control over fast-moving MLX features such as
DFlash2, MTP, TurboQuant KV, and ANE prefill.

Context Fabric no longer depends on any of those features for correctness.

## 1. Configure the endpoint/model

The default expert profile expects:

```text
http://127.0.0.1:8000
Qwen3.8-27B-8bit
```

Edit `.context-fabric/config.json` if yours differ, then:

```bash
python3 scripts/context_backend.py set omlx
```

In `opencode.json`, switch the active model/agents from the Ollama ref to:

```text
omlx/Qwen3.8-27B-8bit
```

The example config already includes the `omlx` OpenAI-compatible provider.

## 2. Backend tuning belongs to oMLX

Do not encode DFlash/MTP/TurboQuant/ANE decisions in context packs. They are inference-server
implementation choices and can change across oMLX versions or model quantizations without
changing Context Fabric's task-context artifact.

Treat profiles as benchmark candidates, for example:

- normal paged-cache engine + prefix reuse;
- DFlash2 with its own prefix-cache path;
- native MTP profile;
- ANE-assisted prefill;
- TurboQuant KV only when memory/context pressure justifies it.

Use the four-lane benchmark suite, plus additional oMLX profile runs if desired, to choose the
winner on whole coding tasks. Do not select a profile from isolated decode TPS alone.

## 3. Explicit telemetry

Current oMLX OpenAI-compatible responses can expose fields such as:

```text
usage.prompt_tokens_details.cached_tokens
usage.time_to_first_token
usage.prompt_eval_duration
usage.generation_duration
usage.prompt_tokens_per_second
usage.generation_tokens_per_second
```

Context Fabric's backend adapter searches the stable/structured fields first and preserves the
raw response in `.context-fabric/logs/last-status-raw.json`.

```bash
python3 scripts/context_status.py --backend omlx --probe
```

## 4. Optional warmup

Unlike Ollama, the oMLX adapter supports a best-effort explicit warm diagnostic:

```bash
python3 scripts/context_warm.py --backend omlx
```

This is an optimization/diagnostic only. Freeze/activation correctness never depends on it.
