"""Read/write/validate context_pack YAML files under .context-fabric/packs/."""
from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None

# Project root = current working directory, NOT this script's own location. OpenCode runs
# command-template shell snippets from the project's root directory, and these scripts are
# meant to operate on whatever project context-fabric has been installed into (see
# install.sh) — not on the context-fabric repo itself. If you're running these scripts by
# hand, `cd` into your project root first.
PROJECT_ROOT = Path.cwd()
SCRIPT_DIR = Path(__file__).resolve().parents[1]  # .../scripts, for locating schema/ if it
# was installed alongside scripts/ rather than at the project root (install.sh keeps them
# siblings, so this is a fallback, not the primary path).
PACKS_DIR = PROJECT_ROOT / ".context-fabric" / "packs"
HISTORY_DIR = PROJECT_ROOT / ".context-fabric" / "history"
_schema_candidates = [PROJECT_ROOT / "schema" / "context-pack.schema.json", SCRIPT_DIR.parent / "schema" / "context-pack.schema.json"]
SCHEMA_PATH = next((p for p in _schema_candidates if p.exists()), _schema_candidates[0])

PACK_NAME_RE = re.compile(r"^([a-z0-9][a-z0-9-]*):v(\d+)$")


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat()


def approx_tokens(text: str) -> int:
    """Rough word->token proxy (~1.3 tokens/word for English prose+code) used ONLY
    until a real usage-based count is available from oMLX (see docs/omlx-qwen-setup.md
    and lib/omlx_client.py). Never treat this as the real prefill size — /context-status
    should prefer a live probe's usage field when one is available."""
    return round(len(text.split()) * 1.3)


def load_schema() -> Optional[dict]:
    if not SCHEMA_PATH.exists():
        return None
    return json.loads(SCHEMA_PATH.read_text())


def validate_pack(pack: dict) -> list[str]:
    """Returns a list of validation error strings (empty = valid)."""
    schema = load_schema()
    if schema is None or jsonschema is None:
        return ["jsonschema/schema unavailable — skipped structural validation"]
    validator = jsonschema.Draft7Validator(schema)
    return [f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in validator.iter_errors(pack)]


def pack_file_path(context_pack: str) -> Path:
    """context_pack like 'approval-flow:v3' -> .context-fabric/packs/approval-flow-v3.yaml"""
    m = PACK_NAME_RE.match(context_pack)
    if not m:
        raise ValueError(f"context_pack must match '<name>:v<n>', got {context_pack!r}")
    name, version = m.group(1), m.group(2)
    return PACKS_DIR / f"{name}-v{version}.yaml"


def latest_version(name: str) -> int:
    """Highest existing version number for a pack name, or 0 if none exist."""
    best = 0
    if not PACKS_DIR.exists():
        return best
    for f in PACKS_DIR.glob(f"{name}-v*.yaml"):
        m = re.match(rf"^{re.escape(name)}-v(\d+)\.yaml$", f.name)
        if m:
            best = max(best, int(m.group(1)))
    return best


def load_pack(context_pack: str) -> dict:
    path = pack_file_path(context_pack)
    if not path.exists():
        raise FileNotFoundError(f"No pack file at {path}")
    return yaml.safe_load(path.read_text())


def save_pack(pack: dict, *, allow_overwrite_if_unprimed: bool = True) -> Path:
    """Writes a pack to disk. By default (allow_overwrite_if_unprimed=True), refuses
    to overwrite only a pack that already has a prefix_hash (i.e. has been primed) —
    that would silently break the cache invariant. Passing
    allow_overwrite_if_unprimed=False refuses to overwrite ANY existing pack file,
    primed or not (used by context_plan.py so a repeated draft doesn't clobber
    in-progress quality-lane edits). Use context_checkpoint.py to move forward to a
    new version instead of overwriting either way.
    """
    path = pack_file_path(pack["context_pack"])
    if path.exists():
        existing = yaml.safe_load(path.read_text()) or {}
        if not allow_overwrite_if_unprimed:
            raise RuntimeError(
                f"{path} already exists — refusing to overwrite. Delete it first if you "
                "really want to redraft it, or bump the version and use context_checkpoint.py."
            )
        if existing.get("prefix_hash"):
            raise RuntimeError(
                f"{path} was already primed (prefix_hash={existing['prefix_hash']}) — "
                "refusing to overwrite an active/primed pack. Run context_checkpoint.py "
                "to create the next version instead."
            )
    PACKS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(pack, sort_keys=False, width=100))
    return path


def append_history(event: dict) -> None:
    """Append-only log of pack lifecycle events. Never rewritten, only appended."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    log_path = HISTORY_DIR / "pack-events.jsonl"
    event = {"ts": now_iso(), **event}
    with log_path.open("a") as f:
        f.write(json.dumps(event) + "\n")


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)
