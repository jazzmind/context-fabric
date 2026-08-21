# context_pack spec

A `context_pack` is the typed, versioned asset the planner produces instead of "send 200k
tokens more intelligently." It is validated against `schema/context-pack.schema.json`.

## Lifecycle

```
/context-plan "<task>"                 draft            (v1, status: draft)
   -> quality-lane model finalizes     ->  finalized     (status: finalized, invariants set)
/context-prime <pack:version>          ->  active        (status: active, prefix_hash set)
   ... append-only execution ...
/context-checkpoint                    ->  checkpointed   (old pack), new draft (v+1)
```

Packs live at `.context-fabric/packs/<name>-v<version>.yaml`. Never edit a pack in place once
it has been primed (`prefix_hash` is set) — that would silently break the cache invariant this
whole design exists to protect. Instead, checkpoint forward to a new version.

## Fields

See `schema/context-pack.schema.json` for the authoritative, machine-checked definition. In
prose:

- **task** — one line, human-readable.
- **context_pack** — `<name>:v<n>`. The name should describe the feature/area of work, not the
  literal task text, so related tasks over time can share a naming lineage
  (`approval-flow:v1`, `approval-flow:v2`, ...).
- **prefix_hash** — `sha256:...` of the assembled prefix text. Computed by
  `scripts/context_prime.py`. This is the thing `/context-status` compares turn to turn to
  detect an invalidating change.
- **base** — the ordered content of the immutable prefix. Order matters: it is assembled in
  the field order below and never reordered afterward.
  1. `system_prompt`
  2. `tool_contract`
  3. `architecture_summary`
  4. `dependency_graph` (the task-relevant slice, not the whole repo graph)
  5. `source_slices` (the selected code cone)
  6. `invariants` (+ `acceptance_tests`, `unknowns`)
- **budget** — `prefill_tokens` is the target size of the immutable prefix;
  `reserve_output_tokens` is held back so a long tool result doesn't starve the model's own
  response. `compaction_threshold_pct` is when `/context-status` should start flagging
  compaction risk (default 70% of `prefill_tokens` consumed by append-only tail).
- **execution** — always `{prefix: immutable, history: append_only, compaction:
  task_checkpoint}` in this design. These are constants, not knobs, because relaxing any one
  of them is what breaks cache reuse.
- **subtasks** — the context-stable decomposition the quality-lane model produces: discover
  affected graph -> validate plan against tests/contracts -> implement bounded change -> run
  verification -> update project state (or whatever the task actually needs — this is the
  default shape, not a requirement).
- **checkpoint** — only present on packs created by `/context-checkpoint`: the structured
  handoff (changed files, verified facts, failed hypotheses, test status, next decision) that
  becomes the seed for the next version's `base.invariants`/`unknowns`.

## Worked example

```yaml
task: "Add policy-aware approval workflow"
context_pack: "approval-flow:v1"
parent_pack: null
base:
  system_prompt: ".opencode/prompts/system.md"
  tool_contract: ".opencode/tool-schema.json"
  architecture_summary: ".context-fabric/summaries/architecture.md"
  dependency_graph: ".context-fabric/graph.json#approval-flow"
  source_slices:
    - path: "src/workflows/approval.ts"
      reason: "Primary file being extended."
    - path: "src/policy/engine.ts"
      reason: "Policy evaluation the new workflow must call into."
    - path: "test/workflows/approval.test.ts"
      reason: "Existing test contract to preserve."
  invariants:
    - "Existing approval requests with no policy attached must behave exactly as before."
    - "Policy evaluation must be synchronous and side-effect free."
  acceptance_tests:
    - "test/workflows/approval.test.ts::policy-aware approval blocks on deny"
    - "test/workflows/approval.test.ts::legacy approval unaffected"
  unknowns:
    - "Should a policy evaluation timeout fail open or fail closed?"
budget:
  prefill_tokens: 84000
  reserve_output_tokens: 16000
  compaction_threshold_pct: 70
execution:
  prefix: immutable
  history: append_only
  compaction: task_checkpoint
subtasks:
  - "discover affected graph"
  - "validate plan against tests and contracts"
  - "implement bounded change"
  - "run verification"
  - "update project state"
status: draft
created_at: "2026-08-21T18:47:00-04:00"
```
