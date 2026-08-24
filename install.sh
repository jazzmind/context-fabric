#!/usr/bin/env bash
# Install context-fabric into an existing project.
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

RESEARCH_LANE_ARGS=""
if [ -z "${CONTEXT_FABRIC_BENCHMARK:-}" ] && [ -t 0 ] && [ -t 1 ]; then
  echo
  echo "Optional: route tangential lookup/research work to an isolated OpenCode subagent."
  echo "  1) None (default)"
  echo "  2) Local model (Ollama or oMLX)"
  echo "  3) Cloud model"
  read -rp "Set up a research lane? [1/2/3] (default: 1): " RESEARCH_CHOICE || true
  RESEARCH_CHOICE="${RESEARCH_CHOICE:-1}"
  case "$RESEARCH_CHOICE" in
    2)
      read -rp "  Local backend [ollama/omlx] (default: ollama): " RL_BACKEND || true
      RL_BACKEND="${RL_BACKEND:-ollama}"
      if [ "$RL_BACKEND" = "omlx" ]; then RL_DEFAULT_MODEL="Qwen3-8B-8bit"; else RL_DEFAULT_MODEL="qwen3:8b"; fi
      read -rp "  Model id (default: $RL_DEFAULT_MODEL): " RL_MODEL || true
      RL_MODEL="${RL_MODEL:-$RL_DEFAULT_MODEL}"
      RESEARCH_LANE_ARGS="local $RL_BACKEND $RL_MODEL"
      ;;
    3)
      read -rp "  Cloud provider (default: openai): " RL_PROVIDER || true
      RL_PROVIDER="${RL_PROVIDER:-openai}"
      if [ "$RL_PROVIDER" = "anthropic" ]; then RL_DEFAULT_MODEL="claude-haiku-4-5"; else RL_DEFAULT_MODEL="gpt-5-mini"; fi
      read -rp "  Model id (default: $RL_DEFAULT_MODEL): " RL_MODEL || true
      RL_MODEL="${RL_MODEL:-$RL_DEFAULT_MODEL}"
      RESEARCH_LANE_ARGS="cloud $RL_PROVIDER $RL_MODEL"
      ;;
  esac
fi

mkdir -p "$DEST/.opencode/plugins" "$DEST/.opencode/commands"
cp -R "$SRC_DIR/.opencode/plugins/." "$DEST/.opencode/plugins/"
cp -R "$SRC_DIR/.opencode/commands/." "$DEST/.opencode/commands/"
mkdir -p "$DEST/scripts" "$DEST/schema" "$DEST/docs"
cp -R "$SRC_DIR/scripts/." "$DEST/scripts/"
cp -R "$SRC_DIR/schema/." "$DEST/schema/"
for doc in context-pack-spec.md ollama-qwen-setup.md omlx-qwen-setup.md backend-architecture.md benchmarking.md; do
  if [ -f "$SRC_DIR/docs/$doc" ]; then cp -n "$SRC_DIR/docs/$doc" "$DEST/docs/" 2>/dev/null || true; fi
done

if [ ! -f "$DEST/opencode.json" ]; then
  cp "$SRC_DIR/opencode.json.example" "$DEST/opencode.json.example"
  echo "Copied opencode.json.example -> $DEST (Ollama MLX is the reference/default provider)."
fi

if [ ! -f "$DEST/.opencode/prompts/system.md" ]; then
  mkdir -p "$DEST/.opencode/prompts"
  cat > "$DEST/.opencode/prompts/system.md" <<'EOF'
You are a coding agent working under context-fabric. Treat the active context pack as a
versioned, immutable task artifact. Do not restate or reorder it. Keep execution append-only
and checkpoint forward into a new pack when the task boundary changes.
EOF
  echo "Wrote starter .opencode/prompts/system.md"
fi

if [ ! -f "$DEST/.opencode/tool-schema.json" ]; then
  echo '{"tools": []}' > "$DEST/.opencode/tool-schema.json"
  echo "Wrote placeholder .opencode/tool-schema.json (freeze deliberately; schema changes invalidate frozen packs)."
fi

mkdir -p "$DEST/.context-fabric/packs" "$DEST/.context-fabric/history"

# Record exactly which files belong to the installed Context Fabric runtime. The static graph
# excludes these paths so installing Context Fabric does not pollute the target repo's task cone.
python3 - "$SRC_DIR" "$DEST" <<'PYMANIFEST'
import json
import sys
from pathlib import Path

src = Path(sys.argv[1])
dest = Path(sys.argv[2])
files = []
for rel_root in (".opencode/plugins", ".opencode/commands", "scripts", "schema"):
    base = src / rel_root
    if base.exists():
        files.extend((Path(rel_root) / p.relative_to(base)).as_posix() for p in base.rglob("*") if p.is_file())
for name in ("context-pack-spec.md", "ollama-qwen-setup.md", "omlx-qwen-setup.md", "backend-architecture.md", "benchmarking.md"):
    if (src / "docs" / name).exists():
        files.append(f"docs/{name}")
manifest = {"version": 1, "runtime_files": sorted(set(files))}
path = dest / ".context-fabric" / "install-manifest.json"
path.write_text(json.dumps(manifest, indent=2) + "\n")
PYMANIFEST

if [ ! -f "$DEST/.context-fabric/config.json" ]; then
  cp "$SRC_DIR/.context-fabric.config.example.json" "$DEST/.context-fabric/config.json"
  echo "Wrote .context-fabric/config.json (backend=auto; Ollama preferred, oMLX expert fallback)."
fi

AGENTS_MARKER="<!-- context-fabric:auto-mode-instructions -->"
AGENTS_END_MARKER="<!-- /context-fabric:auto-mode-instructions -->"
if [ ! -f "$DEST/AGENTS.md" ] || ! grep -qF "$AGENTS_MARKER" "$DEST/AGENTS.md" 2>/dev/null; then
  {
    echo "$AGENTS_MARKER"
    cat <<'EOF'
## context-fabric auto mode

This repository uses context-fabric, a deterministic context runtime. Auto mode is on by
default (`/context-auto on|off|status`). Synthetic `[context-fabric:auto]` notes are standing
instructions: perform the requested static/context-management step yourself in the same turn.
Finalize draft invariants/acceptance tests, checkpoint at task boundaries, refresh the next
pack's task cone from checkpoint discoveries, and freeze+activate the next pack (or use `/context-prime` as the freeze+activate shortcut). Ask the user only for
unknowns that genuinely require information not present in the repository/session.
EOF
    echo "$AGENTS_END_MARKER"
  } >> "$DEST/AGENTS.md"
  echo "Appended context-fabric auto-mode instructions to AGENTS.md"
fi

if [ -n "$RESEARCH_LANE_ARGS" ]; then
  (cd "$DEST" && python3 scripts/context_research_lane.py "$RESEARCH_LANE_ARGS")
fi

echo
echo "Installed context-fabric into $DEST"
echo "Next: cd $DEST && python3 -m pip install -r scripts/requirements.txt"
echo "Review opencode.json.example, rename/copy it to opencode.json, then run: opencode ."
echo "Useful commands: /context-backend status, /context-index, /context-plan, /context-status"
