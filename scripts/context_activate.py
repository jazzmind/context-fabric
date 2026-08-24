#!/usr/bin/env python3
"""Activate an already-frozen context pack for OpenCode injection."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import packs  # noqa: E402
from lib.runtime import activate_pack  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True)
    ap.add_argument("--emit", action="store_true")
    args = ap.parse_args()
    try:
        pack, prefix_text, prefix_path = activate_pack(args.pack)
    except RuntimeError as exc:
        packs.eprint(str(exc))
        return 1
    packs.eprint(f"Activated {args.pack}")
    packs.eprint(f"  prefix_hash = {pack['prefix_hash']}")
    packs.eprint(f"  active state = {packs.ACTIVE_STATE_PATH}")
    packs.eprint(f"  prefix file = {prefix_path}")
    if args.emit:
        print(prefix_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
