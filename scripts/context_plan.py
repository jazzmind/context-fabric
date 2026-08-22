#!/usr/bin/env python3
"""/context-plan "<task>" — draft a versioned context_pack from the code graph.

This script does the CHEAP part only: selects a candidate code cone and writes a draft
pack. It deliberately does not call any model — per the design, the fast model does
selection/synthesis and the quality-lane model (Qwen3.8-27B) validates/finalizes invariants,
acceptance tests, and subtask decomposition. Both of those happen in the OpenCode agent turn
that follows this script's output (see .opencode/commands/context-plan.md).

Usage:
    python3 scripts/context_plan.py --task "Add policy-aware approval workflow" \\
        --seed src/workflows/approval.ts --name approval-flow
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import packs  # noqa: E402
from lib.graph import code_cone, rank_by_relevance  # noqa: E402
import json  # noqa: E402


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "task"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--name", help="Pack name lineage, e.g. 'approval-flow'. Defaults to a slug of --task.")
    ap.add_argument("--seed", action="append", default=[], help="Seed file(s) to start the code-cone search from. Repeatable.")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--graph", default=".context-fabric/graph.json")
    ap.add_argument("--prefill-budget", type=int, default=84000)
    ap.add_argument("--reserve-output", type=int, default=16000)
    ap.add_argument("--cone-limit", type=int, default=25, help="Max files to include before truncating (keeps the pack honest about size).")
    args = ap.parse_args()

    graph_path = Path(args.graph)
    if not graph_path.exists():
        packs.eprint(f"No graph at {graph_path} — run /context-index first.")
        return 1
    graph = json.loads(graph_path.read_text())

    # Sanitize --name too, not just the fallback slug: pack_file_path()/PACK_NAME_RE
    # require a lowercase-hyphen slug, and an unsanitized explicit --name would crash
    # deep inside save_pack() instead of failing with a clear message here.
    name = slugify(args.name) if args.name else slugify(args.task)
    version = packs.latest_version(name) + 1
    context_pack_id = f"{name}:v{version}"

    seeds = args.seed
    if not seeds:
        # No seed given: fall back to the most keyword-relevant files as seeds.
        keywords = re.findall(r"[a-zA-Z]{4,}", args.task)
        candidates = rank_by_relevance(graph, list(graph["nodes"].keys()), keywords)
        seeds = candidates[:3]

    cone = code_cone(graph, seeds, depth=args.depth)
    keywords = re.findall(r"[a-zA-Z]{4,}", args.task)
    cone = rank_by_relevance(graph, cone, keywords)
    truncated = len(cone) > args.cone_limit
    cone = cone[: args.cone_limit]

    pack = {
        "task": args.task,
        "context_pack": context_pack_id,
        "parent_pack": f"{name}:v{version - 1}" if version > 1 else None,
        "base": {
            "system_prompt": ".opencode/prompts/system.md",
            "tool_contract": ".opencode/tool-schema.json",
            "architecture_summary": ".context-fabric/summaries/architecture.md",
            "dependency_graph": f".context-fabric/graph.json#{name}",
            "source_slices": [{"path": p, "reason": "selected by static code-cone search"} for p in cone],
            "invariants": [],
            "acceptance_tests": [],
            "unknowns": [
                "Invariants and acceptance tests are empty — the quality-lane model must fill "
                "these in before this pack is primed."
            ],
        },
        "budget": {
            "prefill_tokens": args.prefill_budget,
            "reserve_output_tokens": args.reserve_output,
            "compaction_threshold_pct": 70,
        },
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
    }

    errors = packs.validate_pack(pack)
    path = packs.save_pack(pack)
    packs.append_history({"event": "drafted", "context_pack": context_pack_id, "seeds": seeds})

    print(f"Drafted {context_pack_id} -> {path}")
    print(f"Code cone: {len(cone)} files" + (" (truncated, raise --cone-limit if needed)" if truncated else ""))
    for p in cone:
        print(f"  - {p}")
    if errors:
        print("\nSchema validation notes (expected pre-finalization — invariants are empty):")
        for e in errors:
            print(f"  - {e}")
    print(
        "\nNext: have the quality-lane model review this draft, state explicit invariants and "
        "acceptance tests, resolve the 'unknowns' entry above, and rewrite this file "
        f"({path}) directly (it is still unprimed, so it's safe to edit)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
