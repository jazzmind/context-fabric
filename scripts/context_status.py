#!/usr/bin/env python3
"""Render backend-aware context-pack status without pretending all caches expose telemetry."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import packs  # noqa: E402
from lib.backends import probe_backend, resolve_backend  # noqa: E402

REPO_ROOT = Path.cwd()


def session_log_size(pack_name: str) -> int:
    log_dir = REPO_ROOT / ".context-fabric" / "session-log"
    total_words = 0
    if not log_dir.exists():
        return 0
    for file in log_dir.glob("*.jsonl"):
        for line in file.read_text().splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("context_pack") == pack_name:
                if isinstance(event.get("content_words_approx"), (int, float)):
                    total_words += int(event["content_words_approx"])
                else:
                    total_words += len(json.dumps(event.get("content", "")).split())
    return total_words


def last_invalidating_warning(pack_name: str) -> str | None:
    path = REPO_ROOT / ".context-fabric" / "logs" / "plugin-warnings.log"
    if not path.exists():
        return None
    matching = [line for line in path.read_text().splitlines() if pack_name in line]
    return matching[-1] if matching else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", help="Defaults to the currently activated pack.")
    ap.add_argument("--backend", help="auto | ollama | omlx | configured backend")
    ap.add_argument("--probe", action="store_true", help="Send a small diagnostic request. This can warm/perturb backend cache state.")
    ap.add_argument("--base-url", help="Override resolved backend URL for this probe.")
    ap.add_argument("--model", help="Override resolved backend model for this probe.")
    args = ap.parse_args()

    pack_name = args.pack or packs.get_active_pack()
    if not pack_name:
        print("No active pack. Run /context-activate <pack> (or /context-prime <pack>) first.")
        return 1

    pack = packs.load_pack(pack_name)
    prefix_path = packs.prefix_file_path(pack_name)
    prefix_tokens = packs.approx_tokens(prefix_path.read_text()) if prefix_path.exists() else None
    tail_tokens = round(session_log_size(pack_name) * 1.3)
    budget = pack.get("budget", {})
    prefill_budget = budget.get("prefill_tokens")
    warning = last_invalidating_warning(pack_name)
    spec = resolve_backend(args.backend, probe_reachability=args.probe)
    if args.base_url or args.model:
        spec = replace(spec, base_url=args.base_url or spec.base_url, model=args.model or spec.model)

    row: dict[str, str] = {
        "Active context pack": pack_name,
        "Prefix hash": pack.get("prefix_hash", "not frozen"),
        "Stable prefix": f"~{prefix_tokens} tokens (deterministic proxy)" if prefix_tokens else "unknown (prefix file missing)",
        "Logged task tail": f"~{tail_tokens} tokens from user/tool events since activation (lower-bound proxy)",
        "Compaction risk": (
            f"{min(100, round(100 * tail_tokens / prefill_budget))}% of prefix budget"
            if prefill_budget else "unknown (no budget.prefill_tokens)"
        ),
        "Invalidating change": warning or "none detected",
        "Backend": f"{spec.name} — {spec.model} @ {spec.base_url}",
        "Cache behavior": (
            "automatic / opaque telemetry" if spec.name == "ollama" else
            "automatic / explicit cached-token telemetry" if spec.capabilities.cache_telemetry else
            "backend-specific / unknown"
        ),
        "Persistent cache": "yes" if spec.capabilities.persistent_cache else "not assumed",
        "Speculative decode": "supported by backend; configured outside Context Fabric" if spec.capabilities.speculative_decode else "not assumed",
    }

    if args.probe and prefix_path.exists():
        result = probe_backend(spec, prefix_path.read_text())
        log_dir = REPO_ROOT / ".context-fabric" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "last-status-raw.json").write_text(json.dumps({"backend": spec.to_dict(), "probe": result}, indent=2) + "\n")
        if result.get("error"):
            row["Diagnostic probe"] = f"failed: {result['error']}"
        else:
            cached = result.get("cached_tokens")
            row["Diagnostic probe"] = (
                f"cached_tokens={cached}, prompt_tokens={result.get('prompt_tokens')}, total={result.get('total_duration_s')}s"
                if cached is not None else
                f"prompt_tokens={result.get('prompt_tokens')}, total={result.get('total_duration_s')}s; backend does not expose cached-token count"
            )
    else:
        row["Diagnostic probe"] = "not run (use --probe; it may alter cache state)"

    width = max(len(key) for key in row) + 2
    print(f"{'Field'.ljust(width)}| Value")
    print("-" * width + "|" + "-" * 40)
    for key, value in row.items():
        print(f"{key.ljust(width)}| {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
