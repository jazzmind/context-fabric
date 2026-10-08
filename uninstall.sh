#!/usr/bin/env bash
# Remove context-fabric from a project. Uses .context-fabric/install-manifest.json so only
# installer-owned files are removed. Packs, checkpoints and history are user work and are kept
# unless --purge is given.
#
#   ./uninstall.sh <project> [--purge]
set -euo pipefail

DEST="${1:?usage: uninstall.sh <project> [--purge]}"
PURGE="${2:-}"
DEST="$(cd "$DEST" && pwd)"
MANIFEST="$DEST/.context-fabric/install-manifest.json"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "$MANIFEST" ]; then
  echo "No install manifest at $MANIFEST; nothing to remove (pre-0.2 installs must be removed by hand)." >&2
  exit 1
fi

python3 "$SRC_DIR/scripts/install_claude.py" uninstall "$DEST"

python3 - "$DEST" "$MANIFEST" "$SRC_DIR" <<'PY'
import json, sys
from pathlib import Path
dest, manifest, src = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
files = json.loads(manifest.read_text()).get("runtime_files", [])
removed = 0
for rel in files:
    p = dest / rel
    if not p.is_file():
        continue
    # Docs are copied no-clobber, so a project may own a file with the same name.
    if rel.startswith("docs/") and not ((src / rel).is_file() and (src / rel).read_bytes() == p.read_bytes()):
        continue
    p.unlink()
    removed += 1
# Prune directories the installer created and that are now empty.
for rel in sorted({str(Path(f).parent) for f in files}, key=lambda s: -s.count("/")):
    d = dest / rel
    while d != dest and d.is_dir() and not any(d.iterdir()):
        d.rmdir()
        d = d.parent
print(f"Removed {removed} runtime files")
PY

AGENTS="$DEST/AGENTS.md"
if [ -f "$AGENTS" ]; then
  python3 - "$AGENTS" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
text = p.read_text()
start_marker = "<!-- context-fabric:auto-mode-instructions -->"
end_marker = "<!-- /context-fabric:auto-mode-instructions -->"
start = text.find(start_marker)
if start >= 0:
    end = text.find(end_marker, start)
    # Pre-0.2 installs wrote only the opening marker, at EOF.
    text = text[:start] + (text[end + len(end_marker):] if end >= 0 else "")
    if text.strip():
        p.write_text(text.rstrip() + "\n")
    else:
        p.unlink()
    print("Removed context-fabric block from AGENTS.md")
PY
fi

rm -f "$DEST/opencode.json.example" "$DEST/.opencode/tool-schema.json"
rm -f "$MANIFEST"

if [ "$PURGE" = "--purge" ]; then
  rm -rf "$DEST/.context-fabric"
  echo "Purged .context-fabric/ (packs, checkpoints, history)"
else
  echo "Kept .context-fabric/ (packs, checkpoints, history). Re-run with --purge to delete it."
fi
echo "Note: an agent.research block in opencode.json (if configured) is left in place."
