# context-fabric

A project-local [OpenCode](https://opencode.ai) plugin + script pack that turns your local
Qwen setup into a **cache-native agent runtime**: the KV-cache prefix is treated as a named,
versioned, immutable asset rather than an accident of prompt construction.

```
Static code graph -> Context-pack planner -> Named immutable prefix -> KV-cache priming -> Append-only executor
```

This repo is the prototype described in the design doc: it does **not** replace OpenCode,
Qwen, or oMLX — it is a thin policy layer on top of them.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Cache/server | [oMLX](https://github.com/jundot/omlx) | Only local Apple-Silicon server with block-based prefix sharing + hot RAM / cold SSD KV-cache tiers that survive a restart. ([oMLX README](https://github.com/jundot/omlx/blob/main/README.md)) |
| Harness | [OpenCode](https://opencode.ai) | Project-level plugins with tool/file/session hooks and a fully replaceable compaction prompt. ([OpenCode plugin docs](https://opencode.ai/docs/plugins/)) |
| Policy layer | This repo (`context-fabric`) | Context-pack planner, immutable-prefix priming, append-only enforcement, checkpoint compaction. |
| Quality lane | `Qwen3.8-27B` (8-bit) + `Qwen3.8-27B-MTP` draft | Validates/finalizes plans, states invariants, writes acceptance tests. ([mlx-community/Qwen3.8 collection](https://huggingface.co/collections/mlx-community/qwen38)) |
| Fast lane | A small local model + static tooling | Indexing, map refresh, task slicing, compaction drafts — never the frontier model. |

> **Model naming note:** the design doc referred to "Qwen 3.8 27B oQ8e-MTP". The actual
> Hugging Face repos are `mlx-community/Qwen3.8-27B-8bit` (target model) paired with
> `mlx-community/Qwen3.8-27B-MTP-8bit` (the speculative-decoding draft/MTP head, used
> *alongside* the target, not standalone). See `docs/omlx-qwen-setup.md`.

## What's actually implemented vs. left for you to verify

This is a one-to-two-week prototype scaffold, built and tested outside of a real OpenCode/oMLX
runtime (this sandbox has no Apple Silicon). Concretely:

- **Fully implemented, deterministic, and testable without any model or GPU:**
  the static indexer (`scripts/context_index.py`), the pack schema + validator
  (`schema/context-pack.schema.json`), the pack writer/hasher (`scripts/context_prime.py`),
  and the append-only session logger in the plugin.
- **Implemented but needs validation against your installed OpenCode version:**
  the exact plugin hook names (`experimental.session.compacting`,
  `experimental.chat.system.transform`, `tool.execute.before/after`) are the best-documented
  ones as of August 2026 ([OpenCode plugin docs](https://opencode.ai/docs/plugins/)), but
  OpenCode's own docs mark some of these `experimental.*` — expect to adjust field names
  after your first real run. The plugin fails soft (logs a warning, doesn't crash the
  session) if a hook signature doesn't match.
- **Implemented but needs validation against your installed oMLX version:**
  `scripts/lib/omlx_client.py` reads cache/usage fields generically (anything with "cache"
  in the key name) from the completion response, because oMLX's exact usage-field naming
  isn't pinned in its public docs. Run `/context-status` once and check
  `.context-fabric/logs/last-status-raw.json` to see what your server actually returns, then
  tighten the field names in that file.
- **Deliberately left as a stub:** the embeddings step in the indexer. Static structure
  (imports, symbol refs, git churn, test topology) gets you most of the way for a first
  pass; wire in a local embedding model later if retrieval quality needs it.

## Layout

```
install.sh                           Copies plugin/commands/scripts/schema into an existing project.
.opencode/plugin/context-fabric.ts   OpenCode plugin: append-only enforcement, prefix
                                      injection, checkpoint-based compaction, invalidation
                                      detection (tool schema / source slice changes).
.opencode/command/*.md               The five /context-* commands.
scripts/context_index.py             Builds .context-fabric/graph.json (static code graph).
scripts/context_plan.py              Drafts a context_pack YAML for a task from the graph.
scripts/context_prime.py             Assembles + hashes the immutable prefix for a pack.
scripts/context_status.py            Renders the cache/pack status table.
scripts/context_checkpoint.py        Scaffolds the next pack version at a checkpoint.
schema/context-pack.schema.json      JSON Schema for context_pack YAML files.
docs/context-pack-spec.md            Human-readable spec + worked example.
docs/omlx-qwen-setup.md              Mac setup: oMLX, Qwen3.8-27B, OpenCode wiring.
.context-fabric/                     Generated state: graph, packs, history, session logs.
```

## Quick start (once oMLX + OpenCode are installed — see `docs/omlx-qwen-setup.md`)

This repo is a scaffold to install *into* the project you actually want cache-native agent
tooling for — it isn't meant to be `opencode .`'d on its own.

```bash
git clone <this repo> context-fabric
./context-fabric/install.sh ~/path/to/your/project
cd ~/path/to/your/project
python3 -m pip install -r scripts/requirements.txt
npm install @opencode-ai/plugin          # plugin type defs, dev-only
cp opencode.json.example opencode.json   # point OpenCode at your oMLX endpoint + models
opencode .
```

Inside OpenCode:

```
/context-index                        # build/refresh the static code graph
/context-plan Add policy-aware approval workflow
                                       # drafts .context-fabric/packs/<slug>-v1.yaml
                                       # review it, then ask the agent to finalize invariants
                                       # and acceptance tests, and save the file
/context-prime approval-flow:v1        # prime the immutable prefix for that pack
/context-status                        # see reused vs. new tokens, cache tier, compaction risk
/context-checkpoint                     # at a task boundary: scaffold approval-flow:v2
```

## The five commands

| Command | Does |
|---|---|
| `/context-index` | Rebuilds the static code graph (imports, symbol refs, git churn, test topology) into `.context-fabric/graph.json`. Cheap, no model call. |
| `/context-plan "<task>"` | Selects the relevant code cone from the graph and drafts a versioned `context_pack` YAML. You (or the Qwen3.8-27B lane) then validate/finalize invariants + acceptance tests. |
| `/context-prime <pack:version>` | Assembles system prompt + tool schema + architecture summary + selected source slices + invariants into one immutable, hashed prefix and injects it as this session's stable prefix. |
| `/context-status` | Shows the table below: active pack, stable-prefix size, cache tier, reused vs. new tokens, task-tail size, compaction risk, and whether anything invalidated the prefix. |
| `/context-checkpoint` | At a task boundary, produces a structured handoff (changed files, verified facts, failed hypotheses, test status, next decision) and writes it as a **new** named pack version instead of silently degrading the current one. |

`/context-status` output looks like:

| Field | Example |
|---|---|
| Active context pack | `approval-flow:v12` |
| Stable prefix | 84,216 tokens |
| Cache state | hot / SSD / cold |
| Last request reused | 83,968 tokens |
| New tokens prefetched | 1,142 |
| Current task tail | 9,832 tokens |
| Compaction risk | 61% of task budget |
| Invalidating change | tool schema changed / source slice changed / none |

## Two operational caveats (baked into the config, not just documented)

- **Keep TurboQuant KV and DFlash off for hybrid-attention Qwen models to start.** oMLX's own
  experimental-feature docs confirm `DFlashEngine` "does not use omlx's paged KV cache or SSD
  cache system... each request does full prefill from scratch (no prefix cache reuse across
  requests)" ([oMLX DFlash integration doc](https://github.com/jundot/omlx/blob/main/docs/experimental/dflash_mlx_integration.md)) —
  exactly the failure mode this design is trying to avoid. TurboQuant KV has also shipped
  multiple cache-conversion regressions against prefill in recent point releases
  ([oMLX v0.3.5 release notes](https://newreleases.io/project/github/jundot/omlx/release/v0.3.5)).
  `opencode.json.example` and `docs/omlx-qwen-setup.md` both default these to off.
- **Don't trust a "looks cached" conversation.** Only treat the cache as real once a response
  reports a substantial reused-token count — `/context-status` exists specifically so you
  don't have to eyeball this.

## Reference: what this borrows from prior art

- [Reasonix](https://reasonix.homes/) proves the cache-first, append-only, immutable-prefix
  loop works — at 90%+ hit rates — but it's wired specifically to DeepSeek's byte-stable
  cloud prefix cache, not a local Apple-Silicon server. Its
  [architecture doc](https://github.com/esengine/DeepSeek-Reasonix/blob/main/docs/ARCHITECTURE.md)
  is worth reading for the three-region model (immutable prefix / append-only log / volatile
  scratch) this plugin mirrors.
- OpenCode's compaction hook can [fully replace the default compaction prompt](https://opencode.ai/docs/plugins/)
  via `output.prompt`, which is what makes "compact only at task checkpoints, into a new
  named prefix" possible without forking the harness.
