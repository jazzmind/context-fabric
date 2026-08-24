#!/usr/bin/env python3
"""Best-effort backend warmup for an active/frozen context pack.

Warmup is optional. Ollama's MLX cache is automatic, so this command intentionally no-ops for
Ollama rather than pretending a synthetic request matches OpenCode's exact prompt shape.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import packs  # noqa: E402
from lib.backends import resolve_backend, warm_backend  # noqa: E402
from lib.runtime import validate_frozen_pack  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", help="Defaults to the active pack.")
    ap.add_argument("--backend", default=None, help="auto | ollama | omlx | configured backend name")
    args = ap.parse_args()
    pack_name = args.pack or packs.get_active_pack()
    if not pack_name:
        packs.eprint("No active pack. Activate one first.")
        return 1
    try:
        _pack, prefix_text, _path = validate_frozen_pack(pack_name)
    except RuntimeError as exc:
        packs.eprint(str(exc))
        return 1
    spec = resolve_backend(args.backend, probe_reachability=True)
    result = warm_backend(spec, prefix_text)
    log_dir = Path(".context-fabric/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "last-warm.json").write_text(json.dumps({"backend": spec.to_dict(), "result": result}, indent=2) + "\n")
    print(json.dumps({"pack": pack_name, "backend": spec.name, "result": result}, indent=2))
    return 0 if result.get("performed") is not False or spec.name == "ollama" else 1


if __name__ == "__main__":
    raise SystemExit(main())
