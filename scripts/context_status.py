#!/usr/bin/env python3
"""/context-status — render the cache/pack status table.

Usage:
    python3 scripts/context_status.py --pack approval-flow:v3 --base-url http://localhost:8000 --model Qwen3.8-27B-8bit

If --base-url/--model are omitted, prints the pack/prefix-side fields only (no live probe).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import packs  # noqa: E402

REPO_ROOT = Path.cwd()  # project root, not this script's own location — see lib/packs.py


def find_active_pack() -> str | None:
    events_path = REPO_ROOT / ".context-fabric" / "history" / "pack-events.jsonl"
    if not events_path.exists():
        return None
    last_primed = None
    for line in events_path.read_text().splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("event") == "primed":
            last_primed = ev.get("context_pack")
    return last_primed


def session_log_size(pack_name: str) -> int:
    """Approx append-only tail size in words, from the plugin's session log, if present."""
    log_dir = REPO_ROOT / ".context-fabric" / "session-log"
    total = 0
    if not log_dir.exists():
        return 0
    for f in log_dir.glob("*.jsonl"):
        for line in f.read_text().splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("context_pack") == pack_name:
                total += len(json.dumps(ev.get("content", "")).split())
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", help="Defaults to the most recently primed pack.")
    ap.add_argument("--base-url", help="oMLX OpenAI-compatible base URL, e.g. http://localhost:8000")
    ap.add_argument("--model", help="Model id registered in oMLX for the quality lane.")
    args = ap.parse_args()

    pack_name = args.pack or find_active_pack()
    if not pack_name:
        print("No primed pack found. Run /context-prime <pack:version> first.")
        return 1

    pack = packs.load_pack(pack_name)
    prefix_path = REPO_ROOT / ".context-fabric" / "prefixes" / f"{pack_name.replace(':', '-')}.prefix.txt"
    prefix_words = len(prefix_path.read_text().split()) if prefix_path.exists() else None
    tail_words = session_log_size(pack_name)
    budget = pack.get("budget", {})
    prefill_budget = budget.get("prefill_tokens")
    threshold_pct = budget.get("compaction_threshold_pct", 70)

    row = {
        "Active context pack": pack_name,
        "Stable prefix": f"~{prefix_words} words (proxy for tokens — see live probe below for real counts)" if prefix_words else "unknown (prefix file missing)",
        "Cache state": "unknown — run with --base-url/--model for a live probe",
        "Last request reused": "unknown — run with --base-url/--model for a live probe",
        "New tokens prefetched": "unknown — run with --base-url/--model for a live probe",
        "Current task tail": f"~{tail_words} words appended since priming",
        "Compaction risk": (
            f"{min(100, round(100 * tail_words / prefill_budget))}% of task budget"
            if prefill_budget else "unknown (pack has no budget.prefill_tokens)"
        ),
        "Invalidating change": "none detected" if pack.get("prefix_hash") else "pack not primed yet",
    }

    if args.base_url and args.model and prefix_path.exists():
        from lib.omlx_client import probe_usage  # noqa: E402

        result = probe_usage(args.base_url, args.model, prefix_path.read_text())
        raw_log = REPO_ROOT / ".context-fabric" / "logs"
        raw_log.mkdir(parents=True, exist_ok=True)
        (raw_log / "last-status-raw.json").write_text(json.dumps(result, indent=2))
        if result["error"]:
            row["Cache state"] = f"probe failed: {result['error']}"
        else:
            row["Cache state"] = "hot/SSD/cold — see .context-fabric/logs/last-status-raw.json for raw fields"
            row["Last request reused"] = json.dumps(result["cache_fields"]) if result["cache_fields"] else (
                "no field containing 'cache' in usage response — check last-status-raw.json and "
                "update lib/omlx_client.py's field matching for your oMLX version"
            )
            usage = result["raw_usage"] or {}
            row["New tokens prefetched"] = usage.get("prompt_tokens", "unknown (see last-status-raw.json)")

    width = max(len(k) for k in row) + 2
    print(f"{'Field'.ljust(width)}| Value")
    print("-" * width + "|" + "-" * 40)
    for k, v in row.items():
        print(f"{k.ljust(width)}| {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
