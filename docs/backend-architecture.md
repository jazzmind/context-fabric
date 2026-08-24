# Backend architecture

Context Fabric separates context semantics from inference-server optimization.

```text
                  CONTEXT FABRIC CORE
                         │
        ┌────────────────┴────────────────┐
        │                                 │
 context intelligence              deterministic shape
 task semantics                    canonical order
 import/symbol graph               immutable frozen prefix
 tests + git state                 append-only tail
 checkpoint discoveries            version checkpoints
        │                                 │
        └────────────────┬────────────────┘
                         │
                     OpenCode
                         │
              backend capability adapter
                         │
             ┌───────────┴───────────┐
             │                       │
        Ollama MLX                oMLX
        reference                expert
        auto/opaque cache        explicit telemetry
        low maintenance          persistent/tunable cache
```

## Core invariants

The following must not depend on the backend:

- task-cone selection;
- source line slicing;
- canonical pack assembly;
- `prefix_hash`;
- freeze immutability;
- active-pack identity;
- append-only execution policy;
- checkpoint versioning.

## Capability adapters

`scripts/lib/backends.py` exposes only what Context Fabric needs to reason about:

- `automatic_prefix_cache`
- `cache_telemetry`
- `persistent_cache`
- `speculative_decode`
- `explicit_warmup`

Advanced runtime settings are not copied into the context-pack schema.

## Why this should age well

Inference engines will continue to absorb faster attention kernels, prefix caches, speculative
decoding, persistent caches, quantized KV, and hardware-specific accelerators. Context Fabric
should usually consume those improvements rather than recreate them.

Its durable optimization target is upstream: reduce irrelevant context, preserve verified task
state, and make the remaining prompt stable enough that any capable runtime can reuse it.
