#!/usr/bin/env python3
"""/context-index — build/refresh the static code graph.

Usage:
    python3 scripts/context_index.py [--root .] [--out .context-fabric/graph.json]

No model call. Safe to run often (e.g. on file.edited via the plugin, or manually).
"""
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

    # Cheap architecture summary: top-churn files, entrypoint-ish files (many imported_by),
    # and test coverage ratio. This is what context_pack.base.architecture_summary points at.
    nodes = graph["nodes"]
    most_imported = sorted(nodes.values(), key=lambda n: -len(n["imported_by"]))[:10]
    most_churned = sorted(nodes.values(), key=lambda n: -n["churn_90d"])[:10]

    summary_lines = [
        f"# Architecture summary (auto-generated {time.strftime('%Y-%m-%d %H:%M:%S')})",
        "",
        f"- Files indexed: {graph['file_count']} ({graph['test_file_count']} test files)",
        "",
        "## Most depended-on files (likely core/shared modules)",
    ]
    for n in most_imported:
        if len(n["imported_by"]) == 0:
            break
        summary_lines.append(f"- `{n['path']}` — imported by {len(n['imported_by'])} files")

    summary_lines += ["", "## Highest-churn files (last 90 days)"]
    for n in most_churned:
        if n["churn_90d"] == 0:
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
