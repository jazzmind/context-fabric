"""Context-pack freeze/activation primitives shared by CLI commands."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

import yaml

from . import packs
from .graph import task_graph_slice

PROJECT_ROOT = Path.cwd()
LINE_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")


def read_or_placeholder(rel_path: str) -> str:
    path = PROJECT_ROOT / rel_path
    if path.exists():
        return path.read_text(errors="ignore")
    return f"[[missing: {rel_path} — create this file or update the pack's base.* field]]"


def read_source_slice(slice_: dict) -> str:
    rel = slice_["path"]
    path = PROJECT_ROOT / rel
    if not path.exists():
        return f"[[missing: {rel}]]"
    text = path.read_text(errors="ignore")
    line_range = slice_.get("lines")
    if not line_range:
        return text
    match = LINE_RANGE_RE.match(str(line_range).strip())
    if not match:
        return text
    start, end = int(match.group(1)), int(match.group(2))
    if start < 1 or end < start:
        return text
    lines = text.splitlines()
    selected = lines[start - 1 : end]
    return f"# lines {start}-{min(end, len(lines))} of {len(lines)}\n" + "\n".join(selected)


def read_graph_slice(pack: dict) -> str:
    dependency_graph_ref = pack["base"]["dependency_graph"]
    path_part = dependency_graph_ref.split("#", 1)[0]
    path = PROJECT_ROOT / path_part
    if not path.exists():
        return f"[[missing: {path_part}]]"
    graph = json.loads(path.read_text())
    selected = [s["path"] for s in pack["base"].get("source_slices", [])]
    return json.dumps(task_graph_slice(graph, selected), indent=2)


def assemble_prefix(pack: dict) -> str:
    base = pack["base"]
    parts = [
        f"# CONTEXT PACK: {pack['context_pack']}",
        f"# Task: {pack['task']}",
        "",
        "## System prompt",
        read_or_placeholder(base["system_prompt"]),
        "",
        "## Tool contract (frozen for this pack)",
        read_or_placeholder(base["tool_contract"]),
        "",
        "## Architecture summary",
        read_or_placeholder(base["architecture_summary"]),
        "",
        "## Dependency graph (task-scoped slice)",
        read_graph_slice(pack),
        "",
        "## Selected source slices",
    ]
    for slice_ in base.get("source_slices", []):
        reason = slice_.get("reason", "")
        lines = f" [{slice_['lines']}]" if slice_.get("lines") else ""
        parts += [
            f"### {slice_['path']}{lines}" + (f" — {reason}" if reason else ""),
            "```",
            read_source_slice(slice_),
            "```",
            "",
        ]

    parts.append("## Invariants")
    for invariant in base.get("invariants", []):
        parts.append(f"- {invariant}")
    if base.get("acceptance_tests"):
        parts.append("\n## Acceptance tests")
        for test in base["acceptance_tests"]:
            parts.append(f"- {test}")
    if base.get("unknowns"):
        parts.append("\n## Unresolved unknowns")
        for unknown in base["unknowns"]:
            parts.append(f"- {unknown}")

    if pack.get("checkpoint"):
        checkpoint = pack["checkpoint"]
        carried = {
            "changed_files": checkpoint.get("changed_files", []),
            "verified_facts": checkpoint.get("verified_facts", []),
            "failed_hypotheses": checkpoint.get("failed_hypotheses", []),
            "test_status": checkpoint.get("test_status", ""),
            "next_decision": checkpoint.get("next_decision", ""),
        }
        parts += ["\n## Prior checkpoint", json.dumps(carried, indent=2)]

    parts.append("\n## Subtasks")
    for subtask in pack.get("subtasks", []):
        parts.append(f"- {subtask}")

    parts.append(
        "\n---\nEverything above this line is the IMMUTABLE PREFIX for context_pack "
        f"{pack['context_pack']}. From here on, execution is append-only. Do not restate, "
        "reorder, or summarize away the immutable prefix; checkpoint into a new pack version "
        "when the task boundary changes."
    )
    return "\n".join(parts)


def compute_prefix_hash(prefix_text: str) -> str:
    return "sha256:" + hashlib.sha256(prefix_text.encode()).hexdigest()


def freeze_pack(context_pack: str, *, force: bool = False) -> tuple[dict, str, Path]:
    pack = packs.load_pack(context_pack)
    base = pack.get("base", {})
    if not force and not base.get("invariants") and not base.get("acceptance_tests"):
        raise RuntimeError(
            f"{context_pack} has no invariants or acceptance_tests yet — finalize the draft before freezing it."
        )
    if not force and any("Invariants and acceptance tests are empty" in str(item) for item in base.get("unknowns", [])):
        raise RuntimeError(
            f"{context_pack} still contains the planner's unresolved-finalization placeholder. "
            "Resolve/remove it before freezing."
        )

    prefix_text = assemble_prefix(pack)
    token_estimate = packs.approx_tokens(prefix_text)
    budget = int((pack.get("budget") or {}).get("prefill_tokens") or 0)
    if not force and budget and token_estimate > budget:
        raise RuntimeError(
            f"{context_pack} assembles to ~{token_estimate} tokens, above its {budget}-token "
            "immutable-prefix budget. Remove/line-slice irrelevant sources or deliberately --force."
        )
    prefix_hash = compute_prefix_hash(prefix_text)
    existing_hash = pack.get("prefix_hash")
    if existing_hash and existing_hash != prefix_hash and not force:
        packs.append_history({
            "event": "invalidated",
            "context_pack": context_pack,
            "old_hash": existing_hash,
            "new_hash": prefix_hash,
        })
        raise RuntimeError(
            f"{context_pack} was already frozen with {existing_hash}, but now assembles to {prefix_hash}. "
            "Create a checkpoint/new version instead of mutating a content-addressed pack."
        )

    prefix_path = packs.prefix_file_path(context_pack)
    prefix_path.parent.mkdir(parents=True, exist_ok=True)
    prefix_path.write_text(prefix_text)

    pack["prefix_hash"] = prefix_hash
    pack["status"] = "frozen"
    packs.pack_file_path(context_pack).write_text(yaml.safe_dump(pack, sort_keys=False, width=100))
    tokens_approx = packs.approx_tokens(prefix_text)
    packs.append_history({
        "event": "frozen",
        "context_pack": context_pack,
        "prefix_hash": prefix_hash,
        "prefix_tokens_approx": tokens_approx,
    })
    return pack, prefix_text, prefix_path


def validate_frozen_pack(context_pack: str) -> tuple[dict, str, Path]:
    pack = packs.load_pack(context_pack)
    prefix_path = packs.prefix_file_path(context_pack)
    if not pack.get("prefix_hash") or not prefix_path.exists():
        raise RuntimeError(f"{context_pack} is not frozen yet. Run context_freeze.py first.")
    prefix_text = prefix_path.read_text()
    current = compute_prefix_hash(assemble_prefix(pack))
    if current != pack["prefix_hash"]:
        raise RuntimeError(
            f"{context_pack} no longer matches its frozen hash ({pack['prefix_hash']} != {current}). "
            "Checkpoint forward rather than activating stale context."
        )
    if compute_prefix_hash(prefix_text) != pack["prefix_hash"]:
        raise RuntimeError(f"Frozen prefix file for {context_pack} does not match pack.prefix_hash.")
    return pack, prefix_text, prefix_path


def activate_pack(context_pack: str) -> tuple[dict, str, Path]:
    pack, prefix_text, prefix_path = validate_frozen_pack(context_pack)
    pack["status"] = "active"
    packs.pack_file_path(context_pack).write_text(yaml.safe_dump(pack, sort_keys=False, width=100))
    packs.set_active_pack(context_pack, pack["prefix_hash"])
    return pack, prefix_text, prefix_path
