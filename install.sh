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

mkdir -p "$DEST/.opencode/plugin" "$DEST/.opencode/command"
cp -R "$SRC_DIR/.opencode/plugin/." "$DEST/.opencode/plugin/"
cp -R "$SRC_DIR/.opencode/command/." "$DEST/.opencode/command/"
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

echo
echo "Installed context-fabric into $DEST"
echo "Next: cd $DEST && python3 -m pip install -r scripts/requirements.txt && npm install --prefix . @opencode-ai/plugin"
echo "Then: opencode .   and try /context-index"
