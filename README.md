# context-fabric

**Deterministic context management for coding agents.**

Context Fabric builds a task-scoped, versioned context artifact before the coding loop becomes
large and noisy. The artifact is optimized first for **attention quality** and second for
**stable-prefix reuse**. The inference backend is deliberately replaceable.

```text
repository
   ↓
deterministic structure + task signals
   ↓
minimal task-scoped context pack
   ↓
freeze (canonical order + hash)
   ↓
activate (OpenCode system injection)
   ↓
append-only task execution
   ↓
checkpoint → next pack version
   ↓
Ollama MLX (default) | oMLX (expert) | generic OpenAI-compatible backend
```

The core thesis is not “a particular KV-cache implementation is special.” It is:

> Coding agents perform better when relevant context is deliberately selected, canonicalized,
> versioned, kept stable through a task, and checkpointed instead of silently compacted.

A cache-friendly request shape is a useful consequence of that discipline, but Context Fabric
keeps working even when the backend's cache is opaque or changes implementation.

## Default stack

| Layer | Default | Role |
|---|---|---|
| Harness | [OpenCode](https://opencode.ai) | Agent/tool loop and project-level plugin hooks |
| Local runtime | **Ollama MLX** | Reference deployment; low-maintenance local Qwen serving and automatic prompt caching |
| Model | **Qwen3.8-27B MLX** | Default example model for local coding |
| Expert runtime | **oMLX** | Optional explicit cache telemetry, persistent paged cache, DFlash/MTP/ANE/TurboQuant tuning |
| Context runtime | **Context Fabric** | Selection, freezing, activation, append-only policy, checkpointing, benchmark instrumentation |

Ollama is the default because server-side cache and inference optimizations are rapidly becoming
commodity runtime features. Context Fabric's durable value lives above that layer. oMLX remains
the expert backend when explicit cache behavior and low-level MLX tuning matter.

## What changed in 0.2

0.2 removes the project's dependency on oMLX-specific assumptions.

- **Backend-agnostic core.** `scripts/lib/backends.py` describes optional capabilities instead
  of letting cache/server details leak into context-pack logic.
- **Ollama-first configuration.** `backend: auto` resolves to Ollama by default; live probes can
  fall back to oMLX when Ollama is unavailable.
- **Freeze / activate / warm are separate operations.** `/context-prime` remains as a backwards-
  compatible freeze+activate shortcut.
- **Real task-scoped graph slices.** The immutable prefix no longer serializes the first 200
  repository graph nodes.
- **Stronger deterministic selection.** Imports, declared symbols, approximate symbol
  references, related tests, current git changes, 90-day churn, task keywords, and prior
  checkpoint discoveries all influence the selected code cone.
- **Budgeted source context.** Default immutable-prefix target is ~32K tokens, with ~18K reserved
  for source slices; large files can be narrowed to task-relevant line windows.
- **Backend-aware status.** Ollama is reported as automatic/opaque rather than inventing a
  cache-hit count; oMLX can report explicit cached-token/timing telemetry.
- **Four-lane whole-task benchmark suite.** Compare Ollama/oMLX with and without Context Fabric
  on actual coding tasks, using task success and wall-clock completion as primary metrics.

## Lifecycle

The new lifecycle makes backend concerns explicit:

```text
/context-plan "task"
      ↓
draft pack
      ↓
agent finalizes invariants / acceptance tests
      ↓
/context-freeze pack:v1
      ↓
canonical prefix + sha256 (pack is now immutable)
      ↓
/context-activate pack:v1
      ↓
OpenCode injects that exact artifact on subsequent turns
      ↓
append-only work
      ↓
/context-checkpoint pack:v1
      ↓
pack:v2 draft with structured handoff
```

`/context-warm` is optional and backend-specific:

- **Ollama:** intentional no-op. Its cache is automatic and a synthetic warm request is not
  guaranteed to match OpenCode's exact prompt template.
- **oMLX:** can issue a best-effort diagnostic prefill and retain raw telemetry.

For existing workflows:

```text
/context-prime pack:v1
```

still performs **freeze + activate** in one step and emits the prefix into the current command
turn.

## Task-scoped context selection

`/context-index` builds `.context-fabric/graph.json` without a model call. Each file node can
contain:

- resolved imports and reverse imports;
- declared classes/functions/types and source line numbers;
- approximate cross-file symbol references and reverse references;
- related tests;
- line count and approximate token count;
- git working-tree change state;
- 90-day commit churn.

`/context-plan` then combines those structural signals with:

- task keywords;
- explicit `--seed` files, when provided;
- prior checkpoint `changed_files` and prior source selections;
- a source-token budget;
- a maximum selected-file count.

The result is intended to be **minimal sufficient context**, not a repository dump. For a large
file, the planner can record a `lines: start-end` slice, and freeze-time assembly honors that
range.

The dependency-graph section in the frozen prefix is also task-scoped: only selected nodes,
their intra-selection edges, declared symbols, and a compact list of one-hop boundary files are
serialized.

## Quick start: Ollama + OpenCode

Install/pull your local model first. Then install Context Fabric into the project you want to
work on:

```bash
git clone <this-repo>
cd context-fabric
./install.sh ~/Code/my-project

cd ~/Code/my-project
python3 -m pip install -r scripts/requirements.txt
cp opencode.json.example opencode.json
```

The example OpenCode config defaults to:

```text
ollama/qwen3.8:27b-mlx
http://127.0.0.1:11434/v1
```

Start OpenCode from the project:

```bash
opencode .
```

Then inspect the runtime and build a pack:

```text
/context-backend status
/context-index
/context-plan "Implement the approval policy change"
```

After the planning agent finalizes the draft pack:

```text
/context-freeze approval-policy:v1
/context-activate approval-policy:v1
```

or:

```text
/context-prime approval-policy:v1
```

See `docs/ollama-qwen-setup.md` for the default setup and `docs/omlx-qwen-setup.md` for the
expert backend.

## Commands

| Command | Purpose |
|---|---|
| `/context-index` | Refresh deterministic repo graph and architecture summary |
| `/context-plan <task>` | Select a budgeted task cone and draft the next pack version |
| `/context-freeze <pack>` | Canonicalize + hash a finalized pack; does not make it active |
| `/context-activate <pack>` | Make a frozen pack the active OpenCode context artifact |
| `/context-prime <pack>` | Compatibility shortcut: freeze + activate |
| `/context-warm [--backend ...]` | Optional backend warmup/diagnostic; no-op on Ollama |
| `/context-checkpoint <pack>` | Create structured handoff + scaffold next version |
| `/context-refresh <pack>` | Re-select an unfrozen pack's task cone using checkpoint discoveries |
| `/context-status [--probe]` | Pack health + backend capabilities; optional diagnostic request |
| `/context-backend status|set ...` | Inspect/select backend adapter |
| `/context-auto on|off|status` | Toggle automatic index/plan/checkpoint nudges |
| `/context-research-lane ...` | Configure optional isolated lookup subagent |
| `/context-benchmark ...` | Run whole-task four-lane benchmark suite |

## Configuration

Context Fabric state lives in:

```text
.context-fabric/config.json
```

Default shape:

```json
{
  "auto": true,
  "backend": "auto",
  "backends": {
    "ollama": {
      "base_url": "http://127.0.0.1:11434",
      "model": "qwen3.8:27b-mlx"
    },
    "omlx": {
      "base_url": "http://127.0.0.1:8000",
      "model": "Qwen3.8-27B-8bit"
    }
  },
  "context": {
    "target_prefix_tokens": 32000,
    "source_budget_tokens": 18000,
    "working_context_tokens": 64000
  }
}
```

`backend: auto` is intentionally biased toward simplicity: offline commands describe Ollama as
the default; commands that need a live connection try Ollama first and may fall back to oMLX.
Set an explicit backend when benchmarking or tuning:

```bash
python3 scripts/context_backend.py set omlx
```

Backend-specific inference knobs do **not** belong in Context Fabric core. DFlash2, MTP,
TurboQuant KV, ANE split, SSD cache sizing, and similar controls remain oMLX configuration.
That separation keeps the context runtime stable when inference implementations change.

## Backend capabilities

The adapter exposes a small capability model:

```text
automatic_prefix_cache
cache_telemetry
persistent_cache
speculative_decode
explicit_warmup
```

The current defaults are intentionally conservative:

| Capability | Ollama | oMLX |
|---|---:|---:|
| Automatic prefix cache | yes | yes |
| Explicit cached-token telemetry | no / opaque | yes |
| Persistent cache assumed by Context Fabric | no | yes |
| Speculative decoding available | yes, backend-owned | yes, backend-owned |
| Explicit warmup used | no | yes |

This is a **capability description**, not an attempt to reproduce backend internals.

## `/context-status` semantics

Status no longer claims equivalent telemetry across servers.

On Ollama you should expect something like:

```text
Backend          ollama — qwen3.8:27b-mlx @ http://127.0.0.1:11434
Cache behavior   automatic / opaque telemetry
Diagnostic probe not run
```

On oMLX, `--probe` can surface `prompt_tokens_details.cached_tokens` and timing fields when the
installed version returns them.

```text
/context-status --probe
```

A diagnostic probe can itself change cache state. It is never labeled “the last OpenCode
request,” and Context Fabric does not interpret missing Ollama cache counters as cache misses.
Raw probe data is written to `.context-fabric/logs/last-status-raw.json`.

## Checkpoint instead of destructive compaction

OpenCode's compaction hook is replaced with a task checkpoint containing:

1. `changed_files`
2. `verified_facts`
3. `failed_hypotheses`
4. `test_status`
5. `next_decision`

The next context-pack version uses that handoff as a **selection signal**, not as permission to
copy the old prefix blindly. After filling the checkpoint, `/context-refresh <new-pack>`
re-ranks the unfrozen draft from the fresh graph and discoveries; invariants and tests are then
finalized against that refreshed task boundary.

That is important for smaller local models: preserving verified state is useful, but carrying
irrelevant context forever can reduce attention quality even when cache reuse makes it cheap to
prefill.

## Auto mode

Auto mode remains on by default. The plugin:

- throttles repository re-indexing;
- drafts a pack after the first substantial session message;
- injects a synthetic note asking the active agent to finalize the semantic pieces;
- replaces compaction with a structured checkpoint;
- scaffolds the next pack version after compaction.

The plugin only automates deterministic steps. The agent still decides invariants, acceptance
tests, and whether the selected task cone is semantically sufficient.

Toggle it with:

```text
/context-auto off
/context-auto on
/context-auto status
```

## Benchmarking the thesis

The benchmark suite compares:

```text
ollama-baseline
ollama-context-fabric
omlx-baseline
omlx-context-fabric
```

Each run gets a clean copied workspace and the same OpenCode harness/provider shape. Define real
coding tasks with independent verification commands, then run:

```bash
python3 scripts/context_benchmark.py \
  --repo ~/Code/my-project \
  --tasks ~/Code/my-project/context-fabric-bench.json \
  --repeat 3
```

Primary metrics:

- verified task success;
- wall-clock task completion.

Diagnostic metrics:

- first agent activity;
- input/output/cache-read token fields found in OpenCode's JSON events;
- tool-call count;
- changed files;
- Context Fabric prefix size/active pack;
- task verification output.

The suite writes `results.json`, `report.md`, and raw OpenCode JSONL event streams. See
`benchmarks/README.md` and `docs/benchmarking.md`.

## Why Context Fabric can still help with Ollama

Ollama can decide **how to reuse an identical prefix**. It cannot decide **what should be in
that prefix**.

Context Fabric owns the upstream decisions:

- which files and line ranges are relevant;
- which architecture facts are stable enough to freeze;
- which invariants and tests define correctness;
- when task state should become a new version;
- which verified discoveries should influence the next selection;
- how to prevent accidental context mutation and compaction drift.

That separation is the project's long-term bet. If Ollama, oMLX, MLX-LM, or another runtime
makes prompt caching dramatically better, Context Fabric should benefit without a rewrite.

## oMLX expert mode

Use oMLX when you specifically need its additional control/observability. For example:

```bash
python3 scripts/context_backend.py set omlx
```

and change OpenCode's active model to:

```text
omlx/Qwen3.8-27B-8bit
```

Context Fabric intentionally does not prescribe one permanent DFlash2/MTP/TurboQuant/ANE
combination. Those are fast-moving backend optimizations and should be benchmarked per oMLX
version, model quantization, context shape, and hardware. The project benchmark suite is the
preferred way to decide whether an oMLX profile beats Ollama for the coding tasks you care
about.

See `docs/omlx-qwen-setup.md`.

## Layout

```text
.opencode/plugins/context-fabric.ts   OpenCode policy/injection/checkpoint hooks
.opencode/commands/                   Slash-command wrappers
scripts/context_index.py              Repository graph builder
scripts/context_plan.py               Budgeted task-context selector + draft pack writer
scripts/context_freeze.py             Canonical prefix assembly + hash
scripts/context_activate.py           Active-pack state
scripts/context_warm.py               Optional backend warmup adapter
scripts/context_prime.py              Freeze+activate compatibility shortcut
scripts/context_checkpoint.py         Versioned task checkpoint
scripts/context_plan.py --refresh-pack Refresh selection after checkpoint discoveries
scripts/context_status.py             Backend-aware pack/runtime status
scripts/context_backend.py            Backend selection/capability inspection
scripts/context_benchmark.py          Four-lane whole-task benchmark runner
scripts/lib/graph.py                  Imports/symbols/tests/git/relevance graph logic
scripts/lib/runtime.py                Backend-independent pack runtime primitives
scripts/lib/backends.py               Ollama/oMLX/generic capability + telemetry adapters
schema/context-pack.schema.json       Pack schema
benchmarks/                            Benchmark task example and documentation
docs/                                  Setup, architecture, lifecycle, benchmarking docs
```

## Tests

Deterministic logic can be tested without a model/GPU:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
npm run typecheck
```

The unit suite covers task-cone selection, graph slicing, freeze/activation/hash invalidation,
backend capability semantics, and benchmark summary math. Live OpenCode/Ollama/oMLX behavior is
left to the whole-task benchmark suite because that is the layer where end-to-end differences
actually matter.

## Design principle

Context Fabric should avoid competing with inference servers on inference-server features.

If a future backend adds better caching, speculative decoding, attention kernels, persistent KV,
or transparent context compression, the preferred response is usually:

1. describe the capability in an adapter if Context Fabric needs to reason about it;
2. keep context selection/versioning semantics unchanged;
3. measure the whole-task effect;
4. delete backend-specific workarounds when they become unnecessary.

That keeps the project's advantage in a layer that is much less likely to be commoditized:
**deciding what an agent should remember, making that context deterministic, and preserving
verified task state without letting history turn into noise.**
