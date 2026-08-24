"""Read/write/validate context-pack YAML files and active-pack state."""
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

PROJECT_ROOT = Path.cwd()
SCRIPT_DIR = Path(__file__).resolve().parents[1]
CF_DIR = PROJECT_ROOT / ".context-fabric"
PACKS_DIR = CF_DIR / "packs"
HISTORY_DIR = CF_DIR / "history"
ACTIVE_STATE_PATH = CF_DIR / "active.json"
_schema_candidates = [
    PROJECT_ROOT / "schema" / "context-pack.schema.json",
    SCRIPT_DIR.parent / "schema" / "context-pack.schema.json",
]
SCHEMA_PATH = next((p for p in _schema_candidates if p.exists()), _schema_candidates[0])

PACK_NAME_RE = re.compile(r"^([a-z0-9][a-z0-9-]*):v(\d+)$")
DEFAULT_BUDGET = {
    "prefill_tokens": 32000,
    "reserve_output_tokens": 16000,
    "compaction_threshold_pct": 70,
}


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat()


def approx_tokens(text: str) -> int:
    """Cheap, deterministic size proxy used when the backend exposes no tokenizer count."""
    return round(len(text.split()) * 1.3)


def load_schema() -> Optional[dict]:
    if not SCHEMA_PATH.exists():
        return None
    return json.loads(SCHEMA_PATH.read_text())


def validate_pack(pack: dict) -> list[str]:
    schema = load_schema()
    if schema is None or jsonschema is None:
        return ["jsonschema/schema unavailable — skipped structural validation"]
    validator = jsonschema.Draft7Validator(schema)
    return [f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in validator.iter_errors(pack)]


def pack_file_path(context_pack: str) -> Path:
    m = PACK_NAME_RE.match(context_pack)
    if not m:
        raise ValueError(f"context_pack must match '<name>:v<n>', got {context_pack!r}")
    name, version = m.group(1), m.group(2)
    return PACKS_DIR / f"{name}-v{version}.yaml"


def latest_version(name: str) -> int:
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


def save_pack(pack: dict, *, allow_overwrite_if_unfrozen: bool = True, **legacy_kwargs: Any) -> Path:
    """Write a pack without allowing an already-frozen prefix to mutate in place.

    ``allow_overwrite_if_unprimed`` is accepted as a compatibility alias for older callers.
    """
    if "allow_overwrite_if_unprimed" in legacy_kwargs:
        allow_overwrite_if_unfrozen = bool(legacy_kwargs["allow_overwrite_if_unprimed"])

    path = pack_file_path(pack["context_pack"])
    if path.exists():
        existing = yaml.safe_load(path.read_text()) or {}
        if not allow_overwrite_if_unfrozen:
            raise RuntimeError(
                f"{path} already exists — refusing to overwrite. Delete it first if you "
                "really want to redraft it, or checkpoint/bump the version."
            )
        if existing.get("prefix_hash"):
            raise RuntimeError(
                f"{path} was already frozen (prefix_hash={existing['prefix_hash']}) — "
                "refusing to mutate a content-addressed context pack. Create the next "
                "version with context_checkpoint.py instead."
            )
    PACKS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(pack, sort_keys=False, width=100))
    return path


def append_history(event: dict) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    log_path = HISTORY_DIR / "pack-events.jsonl"
    event = {"ts": now_iso(), **event}
    with log_path.open("a") as f:
        f.write(json.dumps(event) + "\n")


def set_active_pack(context_pack: str, prefix_hash: str) -> Path:
    CF_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "context_pack": context_pack,
        "prefix_hash": prefix_hash,
        "activated_at": now_iso(),
    }
    ACTIVE_STATE_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    append_history({"event": "activated", **payload})
    return ACTIVE_STATE_PATH


def clear_active_pack() -> None:
    if ACTIVE_STATE_PATH.exists():
        try:
            previous = json.loads(ACTIVE_STATE_PATH.read_text())
        except json.JSONDecodeError:
            previous = {}
        ACTIVE_STATE_PATH.unlink()
        append_history({"event": "deactivated", "context_pack": previous.get("context_pack")})


def get_active_state() -> Optional[dict]:
    if ACTIVE_STATE_PATH.exists():
        try:
            state = json.loads(ACTIVE_STATE_PATH.read_text())
            if state.get("context_pack"):
                return state
        except (json.JSONDecodeError, OSError):
            pass

    # Compatibility with packs created before active.json existed.
    log_path = HISTORY_DIR / "pack-events.jsonl"
    if not log_path.exists():
        return None
    last: Optional[dict] = None
    for line in log_path.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") in {"activated", "primed"} and event.get("context_pack"):
            last = {
                "context_pack": event["context_pack"],
                "prefix_hash": event.get("prefix_hash"),
                "activated_at": event.get("ts"),
            }
        elif event.get("event") == "deactivated":
            last = None
    return last


def get_active_pack() -> Optional[str]:
    state = get_active_state()
    return state.get("context_pack") if state else None


def prefix_file_path(context_pack: str) -> Path:
    return CF_DIR / "prefixes" / f"{context_pack.replace(':', '-')}.prefix.txt"


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)
