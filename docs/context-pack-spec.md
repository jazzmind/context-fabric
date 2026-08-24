# context-pack spec

A `context_pack` is a typed, versioned task-context artifact. It is selected and reviewed before
being frozen into an immutable prefix. The artifact is backend-independent; cache reuse is an
optimization a backend may apply to the stable request shape.

## Lifecycle

```text
/context-plan "<task>"       -> draft
agent semantic review        -> finalized
/context-freeze <pack>       -> frozen (prefix_hash set)
/context-activate <pack>     -> active (.context-fabric/active.json)
... append-only execution ...
/context-checkpoint <pack>   -> next draft version
```

`/context-prime <pack>` remains a compatibility shortcut for freeze + activate.

Packs live at `.context-fabric/packs/<name>-v<version>.yaml`. Never edit a pack in place after
`prefix_hash` is set. A frozen hash is a content-addressability contract, not merely a cache key.
If the task boundary or selected source changes, checkpoint into a new version.

## Immutable prefix order

Freeze-time assembly is canonical:

1. task / pack identity
2. `system_prompt`
3. `tool_contract`
4. `architecture_summary`
5. task-scoped dependency graph slice
6. `source_slices` in pack order (honoring optional line ranges)
7. `invariants`
8. `acceptance_tests`
9. `unknowns`
10. prior `checkpoint`, when present
11. `subtasks`

The SHA-256 stored in `prefix_hash` covers the assembled text in that exact order.

## Fields

See `schema/context-pack.schema.json` for the machine-checked definition.

- **task** — one-line human description.
- **context_pack** — `<name>:v<n>` lineage/version.
- **prefix_hash** — SHA-256 of the frozen canonical prefix. Absent on drafts.
- **parent_pack** — previous pack version when checkpointing forward.
- **base.system_prompt** — path to stable agent policy text.
- **base.tool_contract** — path to frozen tool/schema snapshot.
- **base.architecture_summary** — generated architecture summary.
- **base.dependency_graph** — graph source reference. Freeze time serializes a task-scoped
  induced slice based on `source_slices`, not the whole graph.
- **base.source_slices** — selected files, reasons, and optional `lines: "start-end"` range.
- **base.invariants** — semantic constraints the implementation must preserve.
- **base.acceptance_tests** — concrete definitions of done.
- **base.unknowns** — unresolved questions; ideally empty before freezing.
- **selection** — deterministic planner telemetry: strategy, seeds, source budget, selected files,
  and prior discovery files. It explains *why* the pack was drafted but is not a backend contract.
- **budget.prefill_tokens** — target immutable-prefix budget. Default 32K.
- **budget.reserve_output_tokens** — output headroom. Default 16K.
- **budget.compaction_threshold_pct** — tail-growth warning threshold.
- **execution** — fixed policy: immutable prefix, append-only history, task-checkpoint compaction.
- **subtasks** — context-stable decomposition.
- **checkpoint** — structured prior-task handoff: changed files, verified facts, failed
  hypotheses, test state, next decision.

## Worked example

```yaml
task: Add policy-aware approval workflow
context_pack: approval-flow:v1
parent_pack: null
base:
  system_prompt: .opencode/prompts/system.md
  tool_contract: .opencode/tool-schema.json
  architecture_summary: .context-fabric/summaries/architecture.md
  dependency_graph: .context-fabric/graph.json#approval-flow
  source_slices:
    - path: src/workflows/approval.ts
      reason: task seed, task-semantic match, dependency/symbol graph
      lines: 40-220
    - path: src/policy/engine.ts
      reason: dependency/symbol graph
    - path: test/workflows/approval.test.ts
      reason: related test contract
  invariants:
    - Existing approvals without a policy behave exactly as before.
    - Policy evaluation remains side-effect free.
  acceptance_tests:
    - policy-aware approval blocks on deny
    - legacy approval remains unaffected
  unknowns: []
selection:
  strategy: task-semantic+imports+symbols+tests+git+checkpoint
  seeds:
    - src/workflows/approval.ts
  source_budget_tokens: 18000
  selected_files:
    - src/workflows/approval.ts
    - src/policy/engine.ts
    - test/workflows/approval.test.ts
  prior_discovery_files: []
budget:
  prefill_tokens: 32000
  reserve_output_tokens: 16000
  compaction_threshold_pct: 70
execution:
  prefix: immutable
  history: append_only
  compaction: task_checkpoint
subtasks:
  - discover affected graph
  - validate plan against tests and contracts
  - implement bounded change
  - run verification
  - update project state
status: finalized
created_at: '2026-08-24T12:00:00-04:00'
```

After `/context-freeze approval-flow:v1`, the pack receives `prefix_hash` and `status: frozen`.
After `/context-activate approval-flow:v1`, it becomes the active pack and is recorded in
`.context-fabric/active.json`.
