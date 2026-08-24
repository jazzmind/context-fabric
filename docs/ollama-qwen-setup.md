# Default local setup: Ollama MLX + Qwen3.8-27B + OpenCode

Ollama is Context Fabric's reference deployment because it provides a low-maintenance local
MLX runtime while Context Fabric owns the higher-level context semantics.

## 1. Install Ollama and OpenCode

Install them using their current macOS instructions, then verify:

```bash
ollama --version
opencode --version
```

## 2. Pull the model

The repository examples use:

```bash
ollama pull qwen3.8:27b-mlx
```

If your installed Ollama uses a different exact tag, `ollama list` is authoritative. Update both
`opencode.json` and `.context-fabric/config.json` to match.

For agentic coding, give the server enough context for normal sessions without automatically
forcing every task to the model's maximum. Context Fabric's default working target is 64K and
its default immutable-prefix target is 32K.

## 3. Point OpenCode at Ollama

Copy the repository example:

```bash
cp opencode.json.example opencode.json
```

The default provider uses Ollama's OpenAI-compatible endpoint:

```text
http://127.0.0.1:11434/v1
```

and model ref:

```text
ollama/qwen3.8:27b-mlx
```

## 4. Select the Context Fabric backend

The default `.context-fabric/config.json` has:

```json
{"backend": "auto"}
```

`auto` prefers Ollama. To make it explicit:

```bash
python3 scripts/context_backend.py set ollama
```

Inspect the resolved backend/capabilities:

```bash
python3 scripts/context_backend.py status
```

## 5. Status and cache semantics

Ollama's cache is automatic but intentionally treated as **opaque** by Context Fabric. The
public runtime metrics are useful for timing/token evaluation, but Context Fabric does not
invent a `cached_tokens` value when Ollama does not expose one.

```bash
python3 scripts/context_status.py
```

A diagnostic request can be sent with:

```bash
python3 scripts/context_status.py --probe
```

That probe may itself warm/change cache state. Use whole-task benchmark results rather than a
single probe to decide whether Context Fabric improves your coding workload.

## 6. Why `/context-warm` is a no-op on Ollama

Context Fabric cannot guarantee that a synthetic warm request uses the identical chat-template
shape OpenCode will send. Ollama already performs automatic model/prompt cache management, so
the correct default is to let the first real agent turn warm exactly the request that matters.
