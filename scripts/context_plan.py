#!/usr/bin/env python3
"""/context-plan — draft or refresh a task-scoped, budgeted context pack from deterministic repo signals."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import packs  # noqa: E402
from lib.backends import load_config  # noqa: E402
from lib.graph import select_task_context, suggest_line_range  # noqa: E402


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "task"


def previous_discoveries(name: str, version: int) -> tuple[list[str], list[str]]:
    """Return file boosts and verified facts from the prior pack in this lineage."""
    if version <= 1:
        return [], []
    try:
        prior = packs.load_pack(f"{name}:v{version - 1}")
    except FileNotFoundError:
        return [], []
    checkpoint = prior.get("checkpoint") or {}
    files = list(checkpoint.get("changed_files") or [])
    files.extend(s.get("path") for s in prior.get("base", {}).get("source_slices", []) if s.get("path"))
    facts = list(checkpoint.get("verified_facts") or [])
    return list(dict.fromkeys(files)), facts


def discovery_files_for_pack(pack: dict) -> list[str]:
    """Use current checkpoint discoveries and parent context as relevance boosts."""
    checkpoint = pack.get("checkpoint") or {}
    files = list(checkpoint.get("changed_files") or [])
    parent_id = pack.get("parent_pack")
    if parent_id:
        try:
            parent = packs.load_pack(parent_id)
        except FileNotFoundError:
            parent = {}
        files.extend(
            s.get("path")
            for s in parent.get("base", {}).get("source_slices", [])
            if s.get("path")
        )
        parent_checkpoint = parent.get("checkpoint") or {}
        files.extend(parent_checkpoint.get("changed_files") or [])
    return list(dict.fromkeys(f for f in files if f))


def select_entries(
    graph: dict,
    task: str,
    *,
    seeds: list[str],
    depth: int,
    cone_limit: int,
    source_budget: int,
    discovery_files: list[str],
) -> list[dict]:
    selected = select_task_context(
        graph,
        task,
        seeds=seeds,
        depth=depth,
        limit=cone_limit,
        source_budget_tokens=source_budget,
        discoveries=discovery_files,
    )
    for entry in selected:
        line_range = suggest_line_range(Path.cwd(), entry["path"], task)
        if line_range:
            entry["lines"] = line_range
            entry["reason"] += "; large file narrowed to task-relevant window"
        # Score/token estimates are planner telemetry, not immutable-pack schema fields.
        entry.pop("score", None)
        entry.pop("approx_tokens", None)
    return selected


def print_selection(selected: list[dict]) -> None:
    for entry in selected:
        lines = f" lines {entry['lines']}" if entry.get("lines") else ""
        print(f"  - {entry['path']}{lines} — {entry.get('reason', '')}")


def refresh_pack(
    pack_id: str,
    *,
    graph: dict,
    source_budget: int,
    seeds: list[str],
    depth: int,
    cone_limit: int,
) -> dict:
    """Refresh selection in an existing unfrozen draft after checkpoint discoveries are recorded."""
    pack = packs.load_pack(pack_id)
    if pack.get("prefix_hash") or pack.get("status") in {"frozen", "active", "primed"}:
        raise ValueError(f"{pack_id} is frozen/active; checkpoint to a new version before refreshing context")

    task = str(pack.get("task") or "").strip()
    if not task:
        raise ValueError(f"{pack_id} has no task")

    discovery_files = discovery_files_for_pack(pack)
    existing_selection = pack.get("selection") or {}
    effective_seeds = list(dict.fromkeys(seeds or existing_selection.get("seeds") or discovery_files[:4]))
    selected = select_entries(
        graph,
        task,
        seeds=effective_seeds,
        depth=depth,
        cone_limit=cone_limit,
        source_budget=source_budget,
        discovery_files=discovery_files,
    )
    pack.setdefault("base", {})["source_slices"] = selected
    pack["selection"] = {
        "strategy": "task-semantic+imports+symbols+tests+git+checkpoint",
        "seeds": effective_seeds or [s["path"] for s in selected[:3]],
        "source_budget_tokens": source_budget,
        "selected_files": [s["path"] for s in selected],
        "prior_discovery_files": discovery_files[:30],
    }

    unknowns = list(pack["base"].get("unknowns") or [])
    unknowns = [u for u in unknowns if not str(u).startswith("Carried forward from")]
    if not pack["base"].get("invariants") or not pack["base"].get("acceptance_tests"):
        reminder = "Finalize invariants and acceptance tests before freezing this refreshed pack."
        if reminder not in unknowns:
            unknowns.append(reminder)
    pack["base"]["unknowns"] = unknowns

    path = packs.save_pack(pack, allow_overwrite_if_unfrozen=True)
    packs.append_history({
        "event": "refreshed",
        "context_pack": pack_id,
        "seeds": pack["selection"]["seeds"],
        "selected_files": len(selected),
        "source_budget_tokens": source_budget,
        "discovery_files": discovery_files[:30],
    })
    return {"pack": pack, "path": path, "selected": selected, "discovery_files": discovery_files}


def main() -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--task", help="Task for a new pack lineage/version.")
    mode.add_argument("--refresh-pack", help="Refresh source selection in an existing unfrozen draft.")
    ap.add_argument("--name", help="Pack lineage name; defaults to a slug of --task.")
    ap.add_argument("--seed", action="append", default=[], help="Seed file(s). Repeatable.")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--graph", default=".context-fabric/graph.json")
    ap.add_argument("--prefill-budget", type=int, help="Target immutable-prefix budget.")
    ap.add_argument("--source-budget", type=int, help="Budget reserved for selected source slices.")
    ap.add_argument("--reserve-output", type=int, default=16000)
    ap.add_argument("--cone-limit", type=int, default=18)
    args = ap.parse_args()

    graph_path = Path(args.graph)
    if not graph_path.exists():
        packs.eprint(f"No graph at {graph_path} — run /context-index first.")
        return 1
    graph = json.loads(graph_path.read_text())
    config = load_config()
    context_cfg = config.get("context", {})
    prefill_budget = args.prefill_budget or int(context_cfg.get("target_prefix_tokens", 32000))
    source_budget = args.source_budget or int(context_cfg.get("source_budget_tokens", 18000))

    if args.refresh_pack:
        try:
            result = refresh_pack(
                args.refresh_pack,
                graph=graph,
                source_budget=source_budget,
                seeds=args.seed,
                depth=args.depth,
                cone_limit=args.cone_limit,
            )
        except (FileNotFoundError, ValueError) as exc:
            packs.eprint(str(exc))
            return 1
        print(f"Refreshed {args.refresh_pack} -> {result['path']}")
        print(
            f"Task-scoped context: {len(result['selected'])} files "
            f"(source budget ~{source_budget} tokens, {len(result['discovery_files'])} discovery boosts)"
        )
        print_selection(result["selected"])
        print(
            "\nNext: review the refreshed cone, finalize invariants/acceptance_tests and unknowns, "
            f"then freeze+activate {args.refresh_pack}."
        )
        return 0

    assert args.task is not None
    name = slugify(args.name) if args.name else slugify(args.task)
    version = packs.latest_version(name) + 1
    context_pack_id = f"{name}:v{version}"
    discovery_files, verified_facts = previous_discoveries(name, version)

    selected = select_entries(
        graph,
        args.task,
        seeds=args.seed,
        depth=args.depth,
        cone_limit=args.cone_limit,
        source_budget=source_budget,
        discovery_files=discovery_files,
    )

    seeds = args.seed or [s["path"] for s in selected[:3]]
    source_paths = [s["path"] for s in selected]
    unknowns = [
        "Invariants and acceptance tests are empty — the quality-lane model must finalize them before freezing this pack."
    ]
    if verified_facts:
        unknowns.append(
            f"Prior checkpoint contains {len(verified_facts)} verified fact(s); preserve only those still relevant to this task boundary."
        )

    pack = {
        "task": args.task,
        "context_pack": context_pack_id,
        "parent_pack": f"{name}:v{version - 1}" if version > 1 else None,
        "base": {
            "system_prompt": ".opencode/prompts/system.md",
            "tool_contract": ".opencode/tool-schema.json",
            "architecture_summary": ".context-fabric/summaries/architecture.md",
            "dependency_graph": f".context-fabric/graph.json#{name}",
            "source_slices": selected,
            "invariants": [],
            "acceptance_tests": [],
            "unknowns": unknowns,
        },
        "selection": {
            "strategy": "task-semantic+imports+symbols+tests+git+checkpoint",
            "seeds": seeds,
            "source_budget_tokens": source_budget,
            "selected_files": source_paths,
            "prior_discovery_files": discovery_files[:30],
        },
        "budget": {
            "prefill_tokens": prefill_budget,
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
    path = packs.save_pack(pack, allow_overwrite_if_unfrozen=False)
    packs.append_history({
        "event": "drafted",
        "context_pack": context_pack_id,
        "seeds": seeds,
        "selected_files": len(selected),
        "source_budget_tokens": source_budget,
    })

    print(f"Drafted {context_pack_id} -> {path}")
    print(f"Task-scoped context: {len(selected)} files (source budget ~{source_budget} tokens)")
    print_selection(selected)
    if errors:
        print("\nSchema validation notes (expected before finalization):")
        for error in errors:
            print(f"  - {error}")
    print(
        "\nNext: review only the selected task cone, finalize invariants/acceptance_tests, "
        f"resolve unknowns, then freeze+activate {context_pack_id} (or use /context-prime as the compatibility shortcut)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
