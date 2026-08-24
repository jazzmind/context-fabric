#!/usr/bin/env python3
"""/context-index — build/refresh the deterministic repository graph."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.graph import build_graph  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="Project root to index.")
    ap.add_argument("--out", default=".context-fabric/graph.json")
    ap.add_argument("--summary-out", default=".context-fabric/summaries/architecture.md")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    t0 = time.time()
    graph = build_graph(root)
    elapsed = time.time() - t0

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(graph, indent=2))

    nodes = graph["nodes"]
    most_imported = sorted(nodes.values(), key=lambda n: -(len(n.get("imported_by", [])) + len(n.get("referenced_by", []))))[:12]
    most_churned = sorted(nodes.values(), key=lambda n: -n.get("churn_90d", 0))[:10]
    changed = [n for n in nodes.values() if n.get("git_changed")]

    summary_lines = [
        f"# Architecture summary (auto-generated {time.strftime('%Y-%m-%d %H:%M:%S')})",
        "",
        f"- Files indexed: {graph['file_count']} ({graph['test_file_count']} test files)",
        f"- Currently changed files: {graph.get('git_changed_file_count', 0)}",
        "- Graph signals: imports, declared symbols, approximate symbol references, test topology, git churn/current changes",
        "",
        "## Most depended-on / referenced files",
    ]
    for n in most_imported:
        degree = len(n.get("imported_by", [])) + len(n.get("referenced_by", []))
        if degree == 0:
            break
        summary_lines.append(f"- `{n['path']}` — {degree} inbound import/symbol references")

    if changed:
        summary_lines += ["", "## Current working-tree changes"]
        for n in changed[:20]:
            summary_lines.append(f"- `{n['path']}`")

    summary_lines += ["", "## Highest-churn files (last 90 days)"]
    for n in most_churned:
        if n.get("churn_90d", 0) == 0:
            break
        summary_lines.append(f"- `{n['path']}` — {n['churn_90d']} commits")

    summary_path = Path(args.summary_out)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text("\n".join(summary_lines) + "\n")

    print(f"Indexed {graph['file_count']} files ({graph['test_file_count']} test) in {elapsed:.2f}s")
    print(f"  graph      -> {out_path}")
    print(f"  summary    -> {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
