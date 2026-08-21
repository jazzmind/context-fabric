#!/usr/bin/env python3
"""/context-checkpoint — scaffold the next pack version at a task boundary.

Does NOT call a model. Writes a next-version draft pre-filled with:
  - parent_pack pointing at the current active pack
  - an empty `checkpoint` block (changed_files/verified_facts/failed_hypotheses/
    test_status/next_decision) for the agent to fill in, seeding this pack's
    base.unknowns/invariants for continuity
  - carried-forward base.* paths (system_prompt/tool_contract/architecture_summary),
    since those rarely change at a checkpoint — source_slices and invariants are left
    for the agent to re-derive against the new task boundary, since blindly carrying
    those forward is exactly the "silent degradation" this design avoids.

Usage:
    python3 scripts/context_checkpoint.py --pack approval-flow:v3
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import packs  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True, help="Current active pack, e.g. approval-flow:v3")
    args = ap.parse_args()

    current = packs.load_pack(args.pack)
    m = re.match(r"^([a-z0-9][a-z0-9-]*):v(\d+)$", args.pack)
    if not m:
        packs.eprint(f"Bad pack id: {args.pack}")
        return 1
    name, version = m.group(1), int(m.group(2))
    next_id = f"{name}:v{version + 1}"

    next_pack = {
        "task": current["task"],
        "context_pack": next_id,
        "parent_pack": args.pack,
        "base": {
            "system_prompt": current["base"]["system_prompt"],
            "tool_contract": current["base"]["tool_contract"],
            "architecture_summary": current["base"]["architecture_summary"],
            "dependency_graph": current["base"]["dependency_graph"],
            "source_slices": [],  # re-derive against the new task boundary, don't carry forward blindly
            "invariants": [],
            "acceptance_tests": [],
            "unknowns": [
                f"Carried forward from {args.pack} checkpoint — fill in the `checkpoint` block "
                "below, then re-derive source_slices/invariants/acceptance_tests for the next unit of work."
            ],
        },
        "budget": current.get("budget", {"prefill_tokens": 84000, "reserve_output_tokens": 16000, "compaction_threshold_pct": 70}),
        "execution": {"prefix": "immutable", "history": "append_only", "compaction": "task_checkpoint"},
        "subtasks": [
            "discover affected graph",
            "validate plan against tests and contracts",
            "implement bounded change",
            "run verification",
            "update project state",
        ],
        "status": "draft",
        "created_at": packs.now_iso(),
        "checkpoint": {
            "changed_files": [],
            "verified_facts": [],
            "failed_hypotheses": [],
            "test_status": "",
            "next_decision": "",
        },
    }

    path = packs.save_pack(next_pack)
    packs.append_history({"event": "checkpointed", "from_pack": args.pack, "to_pack": next_id})

    print(f"Scaffolded {next_id} -> {path}")
    print(
        "Fill in the `checkpoint` block (changed_files, verified_facts, failed_hypotheses, "
        "test_status, next_decision), then re-derive source_slices/invariants/acceptance_tests, "
        f"then run: python3 scripts/context_prime.py --pack {next_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
