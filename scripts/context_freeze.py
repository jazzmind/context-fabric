#!/usr/bin/env python3
"""Freeze a finalized context pack into a deterministic, content-addressed prefix."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import packs  # noqa: E402
from lib.runtime import freeze_pack  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True)
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    try:
        pack, prefix_text, prefix_path = freeze_pack(args.pack, force=args.force)
    except RuntimeError as exc:
        packs.eprint(str(exc))
        return 1
    packs.eprint(f"Frozen {args.pack}")
    packs.eprint(f"  prefix_hash = {pack['prefix_hash']}")
    packs.eprint(f"  prefix file = {prefix_path}")
    packs.eprint(f"  ~{packs.approx_tokens(prefix_text)} tokens (deterministic proxy)")
    if args.emit:
        print(prefix_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
