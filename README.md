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
- **Fully implemented:** the indexer isn't code-only. `.md`/`.mdx`/`.ipynb`/`.sql`/`.csv`/`.txt`/
  `.yaml`/`.yml` files are graphed alongside source files (import extraction just finds zero
  matches in them, which is harmless), so notebooks, data dictionaries, and strategy docs can
  be `--seed`ed and selected the same way code can — see "Common tasks" below.
- **Auto mode — fully implemented and typechecked, but only exercised via the underlying
  Python scripts and `tsc --noEmit`, not a live OpenCode session:** `scripts/context_auto.py`
  (config read/write) is deterministic and directly tested end-to-end. The new `chat.message`
  plugin hook, the `Part`/`TextPart` shape it constructs, and the `PluginInput["$"]` (Bun
  shell) calls were all checked against `@opencode-ai/plugin`'s and `@opencode-ai/sdk`'s
  actual shipped `.d.ts` files (not just docs) and pass `npm run typecheck` with zero errors —
  but whether OpenCode actually calls `chat.message` with exactly this shape, and whether the
  injected synthetic note reliably steers the agent the way `AGENTS.md` asks, can only be
  confirmed on your real OpenCode/oMLX install (no runtime available in this sandbox).
- **Research lane — fully implemented and tested end-to-end at the config layer, model
  routing itself unverified without a live install:** `scripts/context_research_lane.py`'s
  status/local/cloud/off transitions were run against both a fresh `opencode.json.example`
  and a stand-in `opencode.json`, producing valid JSON with the expected `agent.research`
  block each time. Whether OpenCode actually resolves `agent.research`'s `model` field the way
  its docs describe, and whether `@research` mentions or automatic delegation work as
  documented, needs a real run.

## Layout

```
install.sh                           Copies plugin/commands/scripts/schema into an existing
                                      project; interactively offers to set up a research lane.
.opencode/plugins/context-fabric.ts  OpenCode plugin: append-only enforcement, prefix
                                      injection, checkpoint-based compaction, invalidation
                                      detection (tool schema / source slice changes), and
                                      auto mode (reindex/plan/checkpoint nudges on chat.message).
.opencode/commands/*.md              The /context-* commands.
.opencode/prompts/research-agent.md  System prompt for the optional research-lane subagent.
scripts/context_index.py             Builds .context-fabric/graph.json (static code graph).
scripts/context_plan.py              Drafts a context_pack YAML for a task from the graph.
scripts/context_prime.py             Assembles + hashes the immutable prefix for a pack.
scripts/context_status.py            Renders the cache/pack status table.
scripts/context_checkpoint.py        Scaffolds the next pack version at a checkpoint.
scripts/context_auto.py              Toggles auto mode (.context-fabric/config.json).
scripts/context_research_lane.py     Toggles the research lane (edits opencode.json's agent block).
schema/context-pack.schema.json      JSON Schema for context_pack YAML files.
docs/context-pack-spec.md            Human-readable spec + worked example.
docs/omlx-qwen-setup.md              Mac setup: oMLX, Qwen3.8-27B, OpenCode wiring.
AGENTS.md                            Project-root file (auto-discovered by OpenCode) telling
                                      the agent to act on [context-fabric:auto] notes itself.
.context-fabric/                     Generated state: graph, packs, history, session logs, config.
```

## Quick start (once oMLX + OpenCode are installed — see `docs/omlx-qwen-setup.md`)

This repo is a scaffold to install *into* the project you actually want cache-native agent
tooling for — it isn't meant to be `opencode .`'d on its own.

```bash
git clone <this repo> context-fabric
./context-fabric/install.sh ~/path/to/your/project
# install.sh will interactively ask whether to set up a research lane (none/local/cloud) —
# see "Research lane (opt-in)" below. Answer 1 (none) if you're not sure yet; change it later.
cd ~/path/to/your/project
python3 -m pip install -r scripts/requirements.txt
npm install @opencode-ai/plugin          # plugin type defs, dev-only
cp opencode.json.example opencode.json   # point OpenCode at your oMLX endpoint + models
opencode .
```

Auto mode (automatic indexing/planning/priming/checkpointing) is on from first run — see
"Auto mode (on by default)" below. You can still drive the five commands by hand any time.

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

## Auto mode (on by default)

You don't have to drive the five commands above by hand. Auto mode is on by default and
runs two pieces together: a deterministic half (the plugin, on every `chat.message`) and a
reasoning half (the agent itself, steered by standing instructions in the project's root
`AGENTS.md`, which OpenCode auto-discovers with no config wiring needed).

| Trigger | What the plugin does (deterministic) | What it nudges the agent to do (reasoning) |
|---|---|---|
| Every message | Re-runs `/context-index` under the hood, throttled to at most once every 15s | Nothing — this one is silent unless it fails |
| First substantial message in a new session | Drafts a pack from the code graph (`context_plan.py`) | Finalize `invariants`/`acceptance_tests`, resolve `unknowns`, then run `/context-prime` itself |
| Right after compaction | Scaffolds the next pack version (`context_checkpoint.py`) | Fill in the new pack's `checkpoint` block from what it just did, re-derive `source_slices`, then re-prime |

Both nudges arrive as an injected note tagged `[context-fabric:auto]` — `AGENTS.md` tells the
agent to treat these as standing instructions to act on immediately, not as messages to relay
back to you, and to only pause and ask if something under `unknowns` genuinely needs your input.

Toggle it any time:

```
/context-auto status
/context-auto off
/context-auto on
```

or for one shell session without touching the config file: `CONTEXT_FABRIC_AUTO=0 opencode .`

**Known limitation:** the "first substantial message" trigger is per-*session*, not
per-*task* — a second, unrelated task started later in the same long-lived session won't get
an auto-drafted pack. Start a new session per task (matches the "Common tasks" recipes below),
or just run `/context-plan` yourself for the second task.

## Research lane (opt-in)

A second, smaller model — local (a second oMLX model loaded alongside the quality lane) or a
hosted cloud model — that the primary agent can hand tangential, non-sequitur work to: web
lookups, quick research, anything that isn't the actual coding task. Off by default.

**What it's *not* for:** OpenCode subagents already run in a fully isolated child session with
no access to the parent conversation history ([OpenCode Agents docs](https://opencode.ai/docs/agents/)),
so a subagent gets context isolation from your primed pack's cached prefix for free, regardless
of which model it runs on. You do not need a second model just to keep a lookup out of the
main context — a subagent on the *same* model already does that.

**What it's actually for:** resource isolation and cost/privacy tradeoffs.
- **Local:** oMLX serves multiple models from one process with an LRU-evicted, capped memory
  budget ([oMLX README](https://github.com/jundot/omlx/blob/main/README.md)). Every token the
  quality lane's primed prefix needs to stay cache-hot competes for that same budget. Routing
  lookups to a second, smaller oMLX model keeps that traffic from evicting the quality lane's
  KV-cache blocks.
- **Cloud:** offloads lookups entirely off your box — trades local-first privacy for
  convenience/cost, your call per project.

Enable it at install time (interactive prompt in `install.sh`) or later:

```
/context-research-lane status
/context-research-lane local Qwen3-4B-Instruct-4bit
/context-research-lane cloud openai gpt-5-mini
/context-research-lane cloud anthropic claude-haiku-4-5
/context-research-lane off
```

"Local" registers a `research-lane` model in `provider.omlx.models` (you still need to load
that model file into oMLX's own model directory/admin dashboard) and points `agent.research` at
it. "Cloud" points `agent.research` straight at the hosted provider/model — set
`OPENAI_API_KEY` or `ANTHROPIC_API_KEY` in your shell profile first. Either way it writes
`.opencode/prompts/research-agent.md` as that subagent's system prompt (read-only permissions,
no file edits) if it doesn't already exist.

Invoke it with `@research <question>` in a message, or let the primary agent delegate to it
automatically based on its `description` in `opencode.json`.

## Common tasks

Every recipe below is the same five commands in the same order; only the `--seed`, `--depth`,
and what you write into `invariants`/`acceptance_tests` change. If you only read one section,
read this one.

### New project

A brand-new repo has nothing to graph yet, so the payoff starts small and grows fast.

1. Commit a minimal skeleton by hand first (entrypoint stub, package manifest, one test file) —
   context-fabric can only select files that already exist.
2. `/context-index` — builds a graph even if it's 3 files.
3. `/context-plan "Bootstrap the project skeleton: layout, entrypoint, test harness"` — no
   `--seed` needed yet; the code cone will be small or empty, and that's expected for genesis
   work.
4. Finalize `invariants` (e.g. "must match the language/framework in the design doc") and
   `acceptance_tests` (e.g. "test runner executes with zero failures"), then `/context-prime
   scaffold:v1`.
5. Build the skeleton, then `/context-checkpoint`. From `scaffold:v2` onward there's a real
   graph to search, which is where this tool starts pulling its weight.

### New feature

1. `/context-index` (the plugin also reindexes automatically on `file.edited`, so this is often
   already fresh).
2. `/context-plan "Add policy-aware approval workflow" --seed src/workflows/approval.ts` — seed
   with the file(s) you already know matter; the code cone expands outward through imports and
   importers from there.
3. Check the printed code cone before finalizing — add or drop `source_slices` if it under- or
   over-selected.
4. Write concrete `invariants`/`acceptance_tests`, then `/context-prime approval-flow:v1` and
   build. Run `/context-status` any time you're unsure whether you're still cache-hot.
5. `/context-checkpoint` at the natural task boundary (tests passing, or before a materially
   different sub-task) — this hands off into `approval-flow:v2` instead of quietly compacting.

### Security review / fixes

1. `/context-index`.
2. `/context-plan "Audit session handling for injection and privilege-escalation risk" --seed
   src/auth/session.ts --depth 3` — go past the default `--depth 2` for security work, since
   the exploitable path is often several imports away from the obvious entry point.
3. Write `invariants` as hard security constraints (e.g. "no user-controlled input reaches a
   raw SQL string unparameterized"), and `acceptance_tests` as the specific checks you want
   verified.
4. `/context-prime`, then let the quality-lane model do the review pass at full prefix-cache
   reuse instead of resending the whole subsystem on every follow-up question.
5. `/context-checkpoint` **per vulnerability class fixed**, not once at the end of the whole
   audit — each finding becomes its own named handoff (`changed_files`, `verified_facts`,
   `failed_hypotheses`) instead of one large diff that's easy to lose track of.

### Data analysis

1. `/context-index` — now indexes `.ipynb`/`.sql`/`.md`/`.csv` alongside code, so notebooks and
   data-dictionary docs are seedable and selectable like source files.
2. `/context-plan "Explain the drop in week-over-week retention and propose a fix" --seed
   notebooks/retention.ipynb --seed docs/data-dictionary.md`.
3. Keep raw data (large CSVs/parquet) **out of `source_slices`** — reference a data-dictionary
   or schema doc instead. Large data belongs in the append-only tail (tool output), not the
   immutable prefix; pinning a big static blob into the cache burns budget once and forever.
4. Write `invariants` like "must not mutate the raw source tables", `acceptance_tests` like
   "recomputed metric matches the dashboard within 1%".
5. `/context-prime`, analyze, then `/context-checkpoint` once you've settled on a root cause
   before starting "propose a fix" as its own sub-task.

### Business strategy

1. `/context-index` — the same doc/data support picks up strategy memos and planning docs
   (`.md`) alongside any code or data they reference.
2. `/context-plan "Draft a Q3 enterprise upsell GTM plan" --seed docs/gtm-strategy.md`.
3. Write `invariants` around what must **not** change (e.g. "must not contradict the pricing
   commitments in docs/pricing-2026.md"), and `acceptance_tests` as the concrete questions the
   memo has to answer.
4. `/context-prime`, then iterate on the memo across many sessions. `/context-checkpoint` at
   each revision milestone preserves *why* earlier decisions were made (`verified_facts` /
   `failed_hypotheses`) instead of the next session silently forgetting rejected options.
5. Same caveat as data analysis: keep bulky supporting material (full market reports, raw
   spreadsheets) out of the immutable prefix — reference a summary doc instead.

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

## How this compares

Most coding-agent context tooling — including OpenCode's own defaults — treats context as
something to regenerate or summarize per turn. context-fabric treats it as a build artifact:
named, versioned, hashed, and checked for invalidation before it's ever reused. The table below
was built from each project's own docs/README as of August 2026 (every cell is cited; “n.a.”
means the fetched docs didn't confirm it either way).

| Tool | Named, versioned, hashed context artifact? | Cache-hit visibility | Local-model-first | Compaction behavior | File selection |
|---|---|---|---|---|---|
| **context-fabric** (this repo) | **Yes** — `context_pack` YAML at `name:vN`, sha256 `prefix_hash`, invalidation-checked before reuse | **Yes** — `/context-status` reports reused vs. new tokens and cache tier from a live oMLX probe | **Yes** — built around a local Apple Silicon oMLX server + Qwen | **Checkpoint, not compact** — `/context-checkpoint` writes a *new* named version; nothing is silently summarized in place | Static dependency-graph “code cone” (imports + churn + test topology), ranked and budget-capped |
| [OpenCode](https://opencode.ai) (native) | Partial — auto-compaction produces an unnamed, unversioned “checkpoint” ([Compaction](https://opencode.ai/v2/docs/compaction)) | Minimal — only a `setCacheKey` provider option, no hit-rate reporting ([Config](https://opencode.ai/docs/config/)) | No — model-agnostic across 75+ providers, no local-server optimization ([Models](https://opencode.ai/docs/models/)) | Auto-compaction on by default, lossy and in-place ([Compaction](https://opencode.ai/v2/docs/compaction)) | None documented — files enter context only via tool calls |
| [Aider](https://aider.chat) | No — the repo map is regenerated per request; `/clear` discards history ([Repo map](https://aider.chat/docs/repomap.html)) | Closest of the field — explicit prefix ordering for `--cache-prompts`, but no stats while streaming ([Prompt caching](https://aider.chat/docs/usage/caching.html)) | Partial — connects to Ollama, but silently truncates past its default 2k window ([Ollama](https://aider.chat/docs/llms/ollama.html)) | n.a. — only manual `/clear`/`/drop` | Same idea, ungraduated: dependency-graph ranking of the repo map, but resized/regenerated every request instead of frozen + hashed ([Repo map](https://aider.chat/docs/repomap.html)) |
| [Claude Code](https://code.claude.com) | No — persistent memory is hand-edited `CLAUDE.md`/`MEMORY.md`, not versioned ([Memory](https://code.claude.com/docs/en/memory)) | Best in class — real `cache_creation_input_tokens`/`cache_read_input_tokens` metrics ([Prompt caching](https://code.claude.com/docs/en/prompt-caching)) | No — hosted-API first (Anthropic/Bedrock/Foundry) ([Prompt caching](https://code.claude.com/docs/en/prompt-caching)) | Destructive — `/compact` replaces message history in place ([Context window](https://code.claude.com/docs/en/context-window)) | None — manual file reads / memory files |
| [Cursor](https://cursor.com) | No — “Checkpoints” snapshot code state, not context ([Codebase indexing](https://cursor.com/docs/context/codebase-indexing)) | n.a. — no cache-hit reporting found in current docs | No — hosted only (OpenAI/Anthropic/Google/Azure/Bedrock) ([BYOK](https://cursor.com/help/models-and-usage/api-keys)) | n.a. from current docs | Embeddings + grep, no dependency graph ([Codebase indexing](https://cursor.com/docs/context/codebase-indexing)) |
| [Continue.dev](https://continue.dev) | No — context assembled per turn from `@` providers ([Context providers](https://docs.continue.dev/customize/deep-dives/custom-providers)) | n.a. — only autocomplete caching documented ([Config reference](https://docs.continue.dev/reference)) | Yes for models, not caching — first-class local Ollama/OpenAI-compatible support ([Models](https://docs.continue.dev/customize/models)) | n.a. — not documented | Manual `@File`/`@Code`/`@Repository Map` providers |
| [Cline](https://cline.bot) / [Roo Code](https://roocode.com) | Named but hand-maintained — `memory-bank/*.md` files, no hash or version ([Cline Memory Bank](https://docs.cline.bot/best-practices/memory-bank)) | No hit-rate telemetry; Roo reports condensing token counts instead ([Roo condensing](https://docs.roocode.com/features/intelligent-context-condensing)) | Yes — real Ollama/LM Studio support ([Cline local models](https://docs.cline.bot/running-models-locally/overview)) | In-conversation summarization (destructive) | Manual markdown discipline, re-read in full each task |
| [Repomix](https://github.com/yamadashy/repomix) / [Serena](https://github.com/oraios/serena) | No — unversioned `repomix-output.xml` dump; Serena's memory has no stated format | No — token counts only, no cache stats | No — neither optimizes for local inference | n.a. — packs are compressed, not compacted mid-conversation | Manual glob/git selection (Repomix) or symbol/LSP retrieval (Serena) |

What that means in practice:

- **context-fabric is the only one in this table with a hashed, versioned, invalidation-checked
  context artifact.** Everyone else either regenerates context per turn (Aider, Cursor,
  Continue.dev), destroys it in place on compaction (OpenCode, Claude Code, Cline/Roo), or
  hands you an unversioned one-shot dump (Repomix, Serena).
- It pairs Aider's dependency-graph file selection with Claude Code's cache-metrics rigor —
  but locally, on your own oMLX/Qwen box, instead of a hosted API.
- It replaces “compact when you run out of room” (destructive everywhere else above) with
  “checkpoint into a new named version,” so nothing already-verified gets silently
  summarized away.

**Is “cache-native context management” a recognized category?** The exact phrase doesn't
appear in any of the docs above, but the underlying practice is well established under other
names. Manus argues the KV-cache hit rate is “the single most important metric for a
production-stage AI agent” and prescribes stable prefixes, append-only context, and explicit
cache breakpoints ([Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)).
Other write-ups formalize “stable-prefix caching” and “cache-aware context design” as named
patterns ([mnemoverse](https://mnemoverse.com/docs/research/agents/kv-cache-context-engineering),
[Agent Patterns Catalog](https://github.com/agentpatternscatalog/patterns/blob/main/patterns/prompt-caching.md)).
A handful of adjacent open-source projects live in this space too — [agentcache](https://github.com/MasterAgentCoder/agentcache)
(shared-prefix forking with `cache_status()`/hit-rate reporting), [LMCache](https://github.com/lmcache/lmcache)
(a serving-side KV-cache reuse layer), and several small OpenCode-specific cache plugins under
GitHub's [`prompt-cache` topic](https://github.com/topics/prompt-cache) — but none of them
combine context-fabric's three specific moves: dependency-graph-based pack drafting, named +
versioned hashed prefixes with invalidation hooks, and checkpoint-instead-of-compact.

## Reference: what this borrows from prior art

- [Reasonix](https://reasonix.homes/) proves the cache-first, append-only, immutable-prefix
  loop works — at 90%+ hit rates — but it's wired specifically to DeepSeek's byte-stable
  cloud prefix cache, not a local Apple-Silicon server. Its
  [architecture doc](https://github.com/esengine/DeepSeek-Reasonix/blob/main/docs/ARCHITECTURE.md)
  is worth reading for the three-region model (immutable prefix / append-only log / volatile
  scratch) this plugin mirrors.
- OpenCode's compaction hook can [fully replace the default compaction prompt](https://opencode.ai/docs/plugins/)
  via `output.prompt`, which is what makes “compact only at task checkpoints, into a new
  named prefix” possible without forking the harness.
