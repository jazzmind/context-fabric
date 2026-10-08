#!/usr/bin/env python3
"""Install or remove context-fabric's Claude Code wiring in a project.

  install_claude.py install   <project>   (called by install.sh --harness claude|both)
  install_claude.py uninstall <project>   (called by uninstall.sh)

Install:
  - generates .claude/commands/context-*.md from .opencode/commands/ (single source of truth)
  - merges claude_hook.py hooks into .claude/settings.json, preserving existing settings
  - appends a CLAUDE.md block that imports .context-fabric/active-prefix.md
  - records generated files in .context-fabric/install-manifest.json
Every step is idempotent; uninstall reverses exactly what install added.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
HOOK_MARKER = "scripts/claude_hook.py"
CLAUDE_MD_START = "<!-- context-fabric:claude-instructions -->"
CLAUDE_MD_END = "<!-- /context-fabric:claude-instructions -->"
NO_ACTIVE_PACK_MD = "<!-- context-fabric: no context pack is active -->\n"

CLAUDE_MD_BLOCK = f"""{CLAUDE_MD_START}
## context-fabric

This repository uses context-fabric, a deterministic context runtime. Auto mode is on by default
(`/context-auto on|off|status`). `[context-fabric:auto]` notes injected by hooks are standing
instructions: do the requested context-management step yourself in the same turn. Finalize draft
invariants and acceptance tests, checkpoint at task boundaries, refresh the next pack's task cone
from checkpoint discoveries, and freeze + activate the next pack (`/context-prime` does both).
Ask the user only about unknowns that need information not in the repository or session.

The active context pack's frozen prefix (empty when no pack is active):

@.context-fabric/active-prefix.md
{CLAUDE_MD_END}
"""


def hook(event: str, timeout: int) -> dict:
    return {"type": "command", "command": f'cd "$CLAUDE_PROJECT_DIR" && python3 {HOOK_MARKER} {event}', "timeout": timeout}


HOOKS = {
    "SessionStart": [{"hooks": [hook("session-start", 30)]}],
    # UserPromptSubmit defaults to a 30s timeout; indexing and planning a large repo can take longer.
    "UserPromptSubmit": [{"hooks": [hook("user-prompt", 120)]}],
    "PreCompact": [{"hooks": [hook("pre-compact", 120)]}],
    "PreToolUse": [{"matcher": "*", "hooks": [hook("pre-tool", 10)]}],
    "PostToolUse": [{"matcher": "*", "hooks": [hook("post-tool", 10)]}],
}


def is_cf_entry(entry: dict) -> bool:
    return any(HOOK_MARKER in h.get("command", "") for h in entry.get("hooks", []))


def claude_command(opencode_md: str) -> str:
    """opencode command -> Claude Code command: drop `agent:`, allow the scripts it shells out to."""
    m = re.match(r"^---\n(.*?)\n---\n", opencode_md, re.S)
    if not m:
        return opencode_md
    front = [line for line in m.group(1).splitlines() if not line.startswith("agent:")]
    front.append("allowed-tools: Bash(python3 scripts/*)")
    return "---\n" + "\n".join(front) + "\n---\n" + opencode_md[m.end():]


def load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def install(dest: Path) -> None:
    generated: list[str] = []
    cmd_dir = dest / ".claude" / "commands"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted((SRC / ".opencode" / "commands").glob("context-*.md")):
        (cmd_dir / src.name).write_text(claude_command(src.read_text()))
        generated.append(f".claude/commands/{src.name}")

    settings_path = dest / ".claude" / "settings.json"
    settings = load_settings(settings_path)
    hooks = settings.setdefault("hooks", {})
    for event, entries in HOOKS.items():
        existing = [e for e in hooks.get(event, []) if not is_cf_entry(e)]
        hooks[event] = existing + entries
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")

    claude_md = dest / "CLAUDE.md"
    text = claude_md.read_text() if claude_md.exists() else ""
    if CLAUDE_MD_START not in text:
        claude_md.write_text((text.rstrip() + "\n\n" if text.strip() else "") + CLAUDE_MD_BLOCK)

    prefix_md = dest / ".context-fabric" / "active-prefix.md"
    prefix_md.parent.mkdir(parents=True, exist_ok=True)
    if not prefix_md.exists():
        prefix_md.write_text(NO_ACTIVE_PACK_MD)

    manifest_path = dest / ".context-fabric" / "install-manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"version": 1, "runtime_files": []}
    manifest["runtime_files"] = sorted(set(manifest["runtime_files"]) | set(generated))
    manifest["harnesses"] = sorted(set(manifest.get("harnesses", [])) | {"claude"})
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Installed Claude Code wiring: {len(generated)} commands, {len(HOOKS)} hook events, CLAUDE.md block")


def uninstall(dest: Path) -> None:
    settings_path = dest / ".claude" / "settings.json"
    if settings_path.exists():
        settings = load_settings(settings_path)
        hooks = settings.get("hooks", {})
        for event in list(hooks):
            hooks[event] = [e for e in hooks[event] if not is_cf_entry(e)]
            if not hooks[event]:
                del hooks[event]
        if not hooks:
            settings.pop("hooks", None)
        if settings:
            settings_path.write_text(json.dumps(settings, indent=2) + "\n")
        else:
            settings_path.unlink()

    claude_md = dest / "CLAUDE.md"
    if claude_md.exists():
        text = claude_md.read_text()
        start = text.find(CLAUDE_MD_START)
        if start >= 0:
            end = text.find(CLAUDE_MD_END, start)
            text = text[:start] + (text[end + len(CLAUDE_MD_END):] if end >= 0 else "")
            if text.strip():
                claude_md.write_text(text.rstrip() + "\n")
            else:
                claude_md.unlink()
    print("Removed Claude Code hooks and CLAUDE.md block")


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in {"install", "uninstall"}:
        print(__doc__, file=sys.stderr)
        return 2
    dest = Path(sys.argv[2]).resolve()
    (install if sys.argv[1] == "install" else uninstall)(dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
