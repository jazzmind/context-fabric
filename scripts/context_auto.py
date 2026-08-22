#!/usr/bin/env python3
"""/context-auto — check, enable, or disable auto mode.

Auto mode is read by the context-fabric OpenCode plugin (.opencode/plugins/context-fabric.ts)
on every message. When on (the default), the plugin:
  - throttled-reindexes the static code graph on every message,
  - auto-drafts a context pack and nudges the agent to finalize + prime it on the first
    substantial message of a session,
  - auto-scaffolds the next pack version right after compaction and nudges the agent to
    fill in its checkpoint block and re-prime, without waiting to be asked.

This script only flips the on/off switch in .context-fabric/config.json. It does not talk to
OpenCode directly — the plugin reads the file (or the CONTEXT_FABRIC_AUTO env var, which takes
priority) on its own.

Usage:
    python3 scripts/context_auto.py            # status (default)
    python3 scripts/context_auto.py status
    python3 scripts/context_auto.py on
    python3 scripts/context_auto.py off
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path.cwd()  # project root — see scripts/lib/packs.py for why this convention holds
CONFIG_PATH = REPO_ROOT / ".context-fabric" / "config.json"
VALID_ACTIONS = {"on", "off", "status"}
DEFAULT_AUTO = True  # fail-soft default: if the file/field is missing, auto mode is ON


def read_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def read_auto_enabled() -> bool:
    cfg = read_config()
    value = cfg.get("auto", DEFAULT_AUTO)
    return bool(value)


def write_auto_enabled(enabled: bool) -> None:
    cfg = read_config()
    cfg["auto"] = enabled
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", nargs="?", default="status", help="on | off | status")
    args = ap.parse_args()

    action = (args.action or "status").strip().lower()
    if action not in VALID_ACTIONS:
        raise SystemExit(f"Unknown action: {action!r}. Use on, off, or status.")

    if action == "status":
        enabled = read_auto_enabled()
        print(f"Auto mode: {'ON' if enabled else 'OFF'} ({CONFIG_PATH} " + (
            "not present, using default." if not CONFIG_PATH.exists() else "read."
        ) + ")")
        print("Note: the CONTEXT_FABRIC_AUTO env var overrides this file if set, in the plugin.")
        return 0

    write_auto_enabled(action == "on")
    print(f"Auto mode: {'ON' if action == 'on' else 'OFF'} (wrote {CONFIG_PATH}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
