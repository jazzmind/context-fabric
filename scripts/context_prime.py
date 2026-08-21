#!/usr/bin/env python3
"""/context-prime <pack:version> — assemble + hash the immutable prefix, then emit it.

Assembles, in fixed order (never reordered after this point):
  system_prompt -> tool_contract -> architecture_summary -> dependency_graph slice
  -> source_slices -> invariants (+ acceptance_tests, unknowns)

Writes the assembled text + its sha256 to disk, stamps prefix_hash + status:active onto the
pack file, and (with --emit) prints the assembled prefix to stdout so the
.opencode/command/context-prime.md template can inject it into the session via `!`command``.

Refuses to prime a pack whose invariants/acceptance_tests are still empty (i.e. still a raw
draft) — finalize it first (see context_plan.py's printed next-step).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import packs  # noqa: E402
import yaml  # noqa: E402

REPO_ROOT = Path.cwd()  # project root, not this script's own location — see lib/packs.py


def read_or_placeholder(rel_path: str) -> str:
    p = REPO_ROOT / rel_path
    if p.exists():
        return p.read_text()
    return f"[[missing: {rel_path} — create this file or update the pack's base.* field]]"


def read_graph_slice(dependency_graph_ref: str) -> str:
    """dependency_graph_ref like '.context-fabric/graph.json#approval-flow' — for now this
    just returns the whole graph JSON pretty-printed; narrowing to a true task-scoped slice
    is a good next enhancement once the graph format has proven itself in real sessions.
    """
    path_part = dependency_graph_ref.split("#", 1)[0]
    p = REPO_ROOT / path_part
    if not p.exists():
        return f"[[missing: {path_part}]]"
    graph = json.loads(p.read_text())
    return json.dumps(
        {"file_count": graph.get("file_count"), "nodes": {k: v for k, v in list(graph.get("nodes", {}).items())[:200]}},
        indent=2,
    )


def assemble_prefix(pack: dict) -> str:
    base = pack["base"]
    parts = [
        f"# CONTEXT PACK: {pack['context_pack']}",
        f"# Task: {pack['task']}",
        "",
        "## System prompt",
        read_or_placeholder(base["system_prompt"]),
        "",
        "## Tool contract (frozen for this pack — do not accept a schema change mid-task)",
        read_or_placeholder(base["tool_contract"]),
        "",
        "## Architecture summary",
        read_or_placeholder(base["architecture_summary"]),
        "",
        "## Dependency graph (task-scoped slice)",
        read_graph_slice(base["dependency_graph"]),
        "",
        "## Selected source slices",
    ]
    for slice_ in base.get("source_slices", []):
        p = REPO_ROOT / slice_["path"]
        content = p.read_text() if p.exists() else f"[[missing: {slice_['path']}]]"
        reason = slice_.get("reason", "")
        parts += [f"### {slice_['path']}" + (f" — {reason}" if reason else ""), "```", content, "```", ""]

    parts.append("## Invariants")
    for inv in base.get("invariants", []):
        parts.append(f"- {inv}")
    if base.get("acceptance_tests"):
        parts.append("\n## Acceptance tests")
        for t in base["acceptance_tests"]:
            parts.append(f"- {t}")
    if base.get("unknowns"):
        parts.append("\n## Unresolved unknowns (resolve before treating this pack as final)")
        for u in base["unknowns"]:
            parts.append(f"- {u}")

    parts.append("\n## Subtasks")
    for st in pack.get("subtasks", []):
        parts.append(f"- {st}")

    parts.append(
        "\n---\nEverything above this line is the IMMUTABLE PREFIX for context_pack "
        f"{pack['context_pack']}. From here on: append-only. Do not restate, reorder, "
        "summarize away, or ask to re-derive anything above. Reference this pack by name "
        "and hash in your responses."
    )
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True, help="e.g. approval-flow:v1")
    ap.add_argument("--emit", action="store_true", help="Print the assembled prefix to stdout.")
    ap.add_argument("--force", action="store_true", help="Prime even if invariants/acceptance_tests look empty.")
    args = ap.parse_args()

    pack = packs.load_pack(args.pack)
    base = pack.get("base", {})
    if not args.force and not base.get("invariants") and not base.get("acceptance_tests"):
        packs.eprint(
            f"{args.pack} has no invariants or acceptance_tests yet — this looks like an "
            "unfinalized draft. Finalize it (or pass --force if this is intentional)."
        )
        return 1

    prefix_text = assemble_prefix(pack)
    prefix_hash = "sha256:" + hashlib.sha256(prefix_text.encode()).hexdigest()

    existing_hash = pack.get("prefix_hash")
    if existing_hash and existing_hash != prefix_hash and not args.force:
        packs.eprint(
            f"{args.pack} was already primed with {existing_hash}, but the assembled prefix "
            f"now hashes to {prefix_hash} — something in base.* (source slices, tool contract, "
            "architecture summary...) changed since it was primed. This is exactly the "
            "'invalidating change' /context-status warns about. Re-priming in place would "
            "silently break the cache invariant. Run /context-checkpoint to move to a new "
            "version instead, or pass --force if you are deliberately re-priming."
        )
        packs.append_history({"event": "invalidated", "context_pack": pack["context_pack"], "old_hash": existing_hash, "new_hash": prefix_hash})
        return 1

    prefix_dir = REPO_ROOT / ".context-fabric" / "prefixes"
    prefix_dir.mkdir(parents=True, exist_ok=True)
    safe_name = pack["context_pack"].replace(":", "-")
    prefix_path = prefix_dir / f"{safe_name}.prefix.txt"
    prefix_path.write_text(prefix_text)

    pack["prefix_hash"] = prefix_hash
    pack["status"] = "active"
    path = packs.pack_file_path(pack["context_pack"])
    path.write_text(yaml.safe_dump(pack, sort_keys=False, width=100))

    packs.append_history({"event": "primed", "context_pack": pack["context_pack"], "prefix_hash": prefix_hash, "prefix_tokens_approx": len(prefix_text.split())})

    packs.eprint(f"Primed {pack['context_pack']}")
    packs.eprint(f"  prefix_hash = {prefix_hash}")
    packs.eprint(f"  prefix file = {prefix_path}")
    packs.eprint(f"  ~{len(prefix_text.split())} words (rough proxy — check /context-status for real token counts)")

    if args.emit:
        print(prefix_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
