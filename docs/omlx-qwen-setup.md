# Mac setup: oMLX + Qwen3.8-27B + OpenCode

Written for macOS 15+ (Sequoia) on Apple Silicon, per oMLX's stated requirements
([oMLX README](https://github.com/jundot/omlx/blob/main/README.md)).

## 1. Install oMLX

```bash
# oMLX is not on pip or brew yet — clone and install from source.
git clone https://github.com/jundot/omlx.git
cd omlx
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Start it (the menu-bar app is the easiest path; CLI works too):

```bash
omlx serve --paged-ssd-cache-max-size 40GB
```

`--paged-ssd-cache-max-size` matters — oMLX reserves a large default SSD cache budget
(observed ~92GB default on one 64GB Mac in community testing;
[source](https://lilting.ch/en/articles/omlx-039-dev2-m1-max-tested)). Cap it to something
that fits your disk headroom.

## 2. Pull the model pair

Quality lane: `Qwen3.8-27B` at 8-bit, plus its MTP (multi-token-prediction) draft head for
speculative decoding — these are two separate Hugging Face repos used together, not one
model:

```bash
# Target model
huggingface-cli download mlx-community/Qwen3.8-27B-8bit --local-dir ~/models/Qwen3.8-27B-8bit
# MTP draft head (NOT standalone — used alongside the target above)
huggingface-cli download mlx-community/Qwen3.8-27B-MTP-8bit --local-dir ~/models/Qwen3.8-27B-MTP-8bit
```

(Collection: [mlx-community/Qwen3.8](https://huggingface.co/collections/mlx-community/qwen38).
If disk/RAM is tight, `Qwen3.8-27B-4bit` is the smaller alternative — check the collection for
current sizes before committing.)

Fast lane (indexing, map refresh, task slicing, compaction drafts): pick something small and
already validated for tool calling, e.g. `mlx-community/Qwen3-8B-8bit` or similar — this does
**not** need to be Qwen3.8; optimize for latency, not depth.

Register both in oMLX's admin dashboard (typically `http://localhost:8000/admin`) or via its
`model_settings.json`, matching whatever your installed oMLX version expects — the dashboard
UI is the more stable way to confirm exact field names, since those have changed across point
releases.

## 3. Turn off TurboQuant KV and DFlash to start

Both are documented to interact badly with hybrid-attention models and/or to bypass oMLX's own
prefix cache entirely:

- DFlash: *"DFlashEngine does not use omlx's paged KV cache or SSD cache system... each
  request does full prefill from scratch (no prefix cache reuse across requests)"*
  ([oMLX experimental docs](https://github.com/jundot/omlx/blob/main/docs/experimental/dflash_mlx_integration.md)).
  That is the exact failure mode this whole design exists to avoid — leave it off for the
  quality lane.
- TurboQuant KV: multiple point releases have shipped fixes for cache-conversion bugs
  specifically around prefill (`Fix TurboQuant KV cache conversion after prefill`, twice —
  [v0.3.5 release notes](https://newreleases.io/project/github/jundot/omlx/release/v0.3.5)).
  It's a legitimate memory-saving feature for long context once stable on your version, but
  don't turn it on in the same session you're trying to validate cache-hit behavior.

In the admin dashboard's per-model settings, or `model_settings.json`, set:

```json
{
  "turboquant_kv_enabled": false,
  "dflash_enabled": false
}
```

## 4. Point OpenCode at oMLX

oMLX exposes an OpenAI-compatible endpoint. Copy `opencode.json.example` (in this repo) to
`opencode.json` and fill in your actual host/port and model IDs, then:

```bash
npm install -g opencode-ai   # if not already installed
cd context-fabric
opencode .
```

## 5. Verify the cache is actually hitting

Don't trust a "looks cached" conversation — run `/context-prime <pack>` then send two
requests in the same session and run `/context-status`. If "Last request reused" stays near
zero across turns, something upstream of this plugin (OpenCode's own history handling, or an
oMLX setting) is breaking byte-stability before it ever reaches oMLX. Check
`.context-fabric/logs/last-status-raw.json` for the raw usage object oMLX returned — cache/hit
field naming isn't pinned across oMLX versions, so this file is the ground truth for your
install.
