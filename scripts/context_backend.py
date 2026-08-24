#!/usr/bin/env python3
"""Inspect or select Context Fabric's inference-backend adapter.

Usage:
  python3 scripts/context_backend.py status
  python3 scripts/context_backend.py set auto|ollama|omlx
  python3 scripts/context_backend.py set ollama --model qwen3.8:27b-mlx --base-url http://127.0.0.1:11434
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.backends import backend_spec, load_config, resolve_backend, save_config  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="action")
    sub.add_parser("status")
    set_parser = sub.add_parser("set")
    set_parser.add_argument("backend")
    set_parser.add_argument("--base-url")
    set_parser.add_argument("--model")
    args = ap.parse_args()
    action = args.action or "status"

    if action == "status":
        cfg = load_config()
        selected = resolve_backend(probe_reachability=True)
        print(json.dumps({
            "configured": cfg.get("backend", "auto"),
            "resolved": selected.to_dict(),
            "ollama": backend_spec("ollama", cfg).to_dict(),
            "omlx": backend_spec("omlx", cfg).to_dict(),
        }, indent=2))
        return 0

    cfg = load_config()
    backend = args.backend.lower()
    cfg["backend"] = backend
    if backend != "auto":
        block = cfg.setdefault("backends", {}).setdefault(backend, {})
        if args.base_url:
            block["base_url"] = args.base_url
        if args.model:
            block["model"] = args.model
    save_config(cfg)
    print(f"Context Fabric backend set to {backend} in .context-fabric/config.json")
    if backend == "auto":
        print("auto prefers Ollama when reachable, then falls back to oMLX for live probes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
