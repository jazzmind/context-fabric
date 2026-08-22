#!/usr/bin/env bash
# Installs context-fabric into an existing project so OpenCode picks it up as
# project-level plugins/commands. Run from anywhere; pass the target project root.
#
# Usage: ./install.sh /path/to/your/project
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: $0 /path/to/your/project" >&2
  exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$1"

if [ ! -d "$DEST" ]; then
  echo "Target project directory does not exist: $DEST" >&2
  exit 1
fi

# --- Research lane setup (interactive) ---------------------------------------------------
# The research lane routes tangential/non-sequitur work (web lookups, quick research) to a
# separate model via an OpenCode subagent, so it never competes for GPU/RAM with the primed
# quality-lane session. Off by default; safe to skip or change later with
# /context-research-lane (or by re-running this script).
RESEARCH_LANE_ARGS=""
if [ -t 0 ] && [ -t 1 ]; then
  echo
  echo "context-fabric can route lighter, tangential work (web lookups, quick research,"
  echo "anything that isn't the main coding task) to a separate model via an OpenCode"
  echo "subagent, so it never competes for GPU/RAM with your primed session."
  echo
  echo "  1) None   - everything runs on the quality lane, like today (default)"
  echo "  2) Local  - a second, smaller model loaded in oMLX alongside the quality lane"
  echo "  3) Cloud  - a hosted model (e.g. OpenAI or Anthropic), if you have an API key"
  read -rp "Set up a research lane? [1/2/3] (default: 1): " RESEARCH_CHOICE || true
  RESEARCH_CHOICE="${RESEARCH_CHOICE:-1}"
  case "$RESEARCH_CHOICE" in
    2)
      read -rp "  oMLX model id for the research lane (e.g. Qwen3-4B-Instruct-4bit): " RL_MODEL || true
      RL_MODEL="${RL_MODEL:-Qwen3-4B-Instruct-4bit}"
      RESEARCH_LANE_ARGS="local $RL_MODEL"
      ;;
    3)
      read -rp "  Cloud provider (e.g. openai, anthropic) (default: openai): " RL_PROVIDER || true
      RL_PROVIDER="${RL_PROVIDER:-openai}"
      if [ "$RL_PROVIDER" = "anthropic" ]; then
        RL_DEFAULT_MODEL="claude-haiku-4-5"
      else
        RL_DEFAULT_MODEL="gpt-5-mini"
      fi
      read -rp "  Model id (default: $RL_DEFAULT_MODEL): " RL_MODEL || true
      RL_MODEL="${RL_MODEL:-$RL_DEFAULT_MODEL}"
      RESEARCH_LANE_ARGS="cloud $RL_PROVIDER $RL_MODEL"
      ;;
    *)
      RESEARCH_LANE_ARGS=""
      ;;
  esac
else
  echo "Non-interactive shell detected — skipping research-lane setup (defaulting to none)."
  echo "Enable it later with /context-research-lane (see README), or by re-running this script interactively."
fi

# Plural directory names (plugins/, commands/) are OpenCode's current canonical
# form; singular is only kept for backwards compatibility on older installs.
mkdir -p "$DEST/.opencode/plugins" "$DEST/.opencode/commands"
cp -R "$SRC_DIR/.opencode/plugins/." "$DEST/.opencode/plugins/"
cp -R "$SRC_DIR/.opencode/commands/." "$DEST/.opencode/commands/"
mkdir -p "$DEST/scripts"
cp -R "$SRC_DIR/scripts/." "$DEST/scripts/"
mkdir -p "$DEST/schema"
cp -R "$SRC_DIR/schema/." "$DEST/schema/"
mkdir -p "$DEST/docs"
cp -n "$SRC_DIR/docs/context-pack-spec.md" "$DEST/docs/" 2>/dev/null || true
cp -n "$SRC_DIR/docs/omlx-qwen-setup.md" "$DEST/docs/" 2>/dev/null || true

if [ ! -f "$DEST/opencode.json" ]; then
  cp "$SRC_DIR/opencode.json.example" "$DEST/opencode.json.example"
  echo "Copied opencode.json.example -> $DEST (review + rename to opencode.json)."
fi

if [ ! -f "$DEST/.opencode/prompts/system.md" ]; then
  mkdir -p "$DEST/.opencode/prompts"
  cat > "$DEST/.opencode/prompts/system.md" <<'EOF'
You are a coding agent working in this repository under context-fabric's cache-native
runtime. Treat the active context pack's immutable prefix as fixed: never restate, reorder,
or summarize it away. Only append new tool calls and findings.
EOF
  echo "Wrote a starter .opencode/prompts/system.md -> $DEST (edit to taste)."
fi

if [ ! -f "$DEST/.opencode/tool-schema.json" ]; then
  echo '{"tools": []}' > "$DEST/.opencode/tool-schema.json"
  echo "Wrote a placeholder .opencode/tool-schema.json -> $DEST (this is a frozen snapshot; update deliberately, not automatically, since changing it invalidates every primed pack)."
fi

mkdir -p "$DEST/.context-fabric/packs" "$DEST/.context-fabric/history"

if [ ! -f "$DEST/.context-fabric/config.json" ]; then
  mkdir -p "$DEST/.context-fabric"
  echo '{"auto": true}' > "$DEST/.context-fabric/config.json"
  echo "Wrote .context-fabric/config.json -> $DEST (auto mode ON by default)."
fi

AGENTS_MARKER="<!-- context-fabric:auto-mode-instructions -->"
if [ ! -f "$DEST/AGENTS.md" ] || ! grep -qF "$AGENTS_MARKER" "$DEST/AGENTS.md" 2>/dev/null; then
  {
    echo "$AGENTS_MARKER"
    cat <<'EOF'
## context-fabric auto mode

This project uses context-fabric (a cache-native context-management layer). Auto mode is on
by default (toggle with `/context-auto on|off|status`, or the `CONTEXT_FABRIC_AUTO` env var).

When it's on, the context-fabric plugin injects synthetic notes into your context tagged
`[context-fabric:auto]`, right after a static-analysis step it already ran for you
(reindexing the code graph, drafting a context pack, or scaffolding the next pack version
after compaction). Treat these notes as standing instructions, not suggestions to relay back
to the user:

- Act on them yourself, immediately, in the same turn you see them.
- Do the reasoning work they ask for (finalizing invariants, writing a checkpoint block,
  deciding what belongs in a code cone) using your own judgment and by reading the actual
  files involved — the plugin only did the deterministic/static part.
- Only pause to ask the user something if the note's `unknowns` section contains a question
  that genuinely requires information only they have. Everything else, just do.
- Follow the specific next command each note tells you to run (e.g. `/context-prime
  <pack>:v<N>`) once you've finished the reasoning step — run it yourself, don't ask first.

EOF
  } >> "$DEST/AGENTS.md"
  echo "Appended auto-mode instructions to $DEST/AGENTS.md (created if missing) — OpenCode auto-discovers this file from the project root."
fi

if [ -n "$RESEARCH_LANE_ARGS" ]; then
  ( cd "$DEST" && python3 scripts/context_research_lane.py "$RESEARCH_LANE_ARGS" )
fi

echo
echo "Installed context-fabric into $DEST"
echo "Next: cd $DEST && python3 -m pip install -r scripts/requirements.txt && npm install --prefix . @opencode-ai/plugin"
echo "Then: opencode .   and try /context-index"
if [ -z "$RESEARCH_LANE_ARGS" ]; then
  echo "Research lane is off. Enable it any time with /context-research-lane local <model> or /context-research-lane cloud <provider> <model>."
else
  echo "Change or disable the research lane any time with /context-research-lane status|local <model>|cloud <provider> <model>|off."
fi
