#!/usr/bin/env python3
"""/context-research-lane — check, enable, or disable the research lane.

The research lane is an opt-in OpenCode subagent ("research") bound to a separate model
(a smaller local oMLX model, or a hosted cloud model) for tangential, non-sequitur work —
web lookups, quick research, anything that isn't the actual coding task. OpenCode already
runs subagents in an isolated child session with no access to the parent conversation, so
this does not exist to protect the primed context pack's cached prefix from being *read* —
that isolation is structural and happens regardless of which model the subagent uses. It
exists so that lookup work doesn't compete for the same oMLX process's GPU/RAM budget as
your primed quality-lane session (which can otherwise evict the very KV-cache blocks
context-fabric exists to keep hot), or so it can run off-box on a cloud model entirely.

Usage:
    python3 scripts/context_research_lane.py                      # status (default)
    python3 scripts/context_research_lane.py status
    python3 scripts/context_research_lane.py local Qwen3-4B-Instruct-4bit
    python3 scripts/context_research_lane.py cloud openai gpt-5-mini
    python3 scripts/context_research_lane.py cloud anthropic claude-haiku-4-5
    python3 scripts/context_research_lane.py off

Also invokable as a single pre-joined string (as the /context-research-lane command does):
    python3 scripts/context_research_lane.py "local Qwen3-4B-Instruct-4bit"

Operates on opencode.json if it exists in the project root, else opencode.json.example
(so it works both at install time, before opencode.json.example has been renamed, and
afterward via the slash command).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path.cwd()  # project root — see scripts/lib/packs.py for why this convention holds
RESEARCH_PROMPT_REL = Path(".opencode/prompts/research-agent.md")

RESEARCH_PROMPT_TEXT = """\
You are the research subagent for a coding session running under context-fabric, a cache-native
context-management layer for OpenCode. The primary agent is protecting a large, primed,
cache-hot context prefix (a versioned "context pack") and delegated this single question to you
specifically so that answering it never competes for the same model/GPU slot as that session.

You have no access to the parent session's context pack, conversation history, or file-edit
history — you only know what's in the prompt you were given for this one question.

Rules:
- Answer only the question you were asked. Do not try to infer or re-derive the parent task.
- Prefer web search / tool calls appropriate to a lookup task over guessing from memory.
- Keep your final answer short and directly usable by the parent agent — a few sentences or a
  small list, with links/citations for anything you looked up externally.
- Read-only by default: don't edit files or run destructive/state-changing commands. If the
  question genuinely requires touching the repository, say so instead of doing it yourself —
  that decision belongs to the primary agent, which has the actual task context.
- If the question turns out to need deep knowledge of *this specific codebase* (not general
  web/library/API knowledge), say so plainly rather than guessing — hand it back to the primary
  agent instead of fabricating an answer.
"""

CLOUD_ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def config_path(root: Path) -> Path:
    real = root / "opencode.json"
    if real.exists():
        return real
    example = root / "opencode.json.example"
    if example.exists():
        return example
    raise SystemExit(
        "No opencode.json or opencode.json.example found in this project root "
        f"({root}). Run install.sh first, or cd into the installed project."
    )


def load_config(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path} is not valid JSON: {e}") from e


def save_config(path: Path, cfg: dict) -> None:
    path.write_text(json.dumps(cfg, indent=2) + "\n")


def ensure_prompt_file(root: Path) -> None:
    path = root / RESEARCH_PROMPT_REL
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RESEARCH_PROMPT_TEXT)
    print(f"Wrote {RESEARCH_PROMPT_REL} (the research subagent's system prompt).")


def research_agent_block(model_ref: str) -> dict:
    return {
        "description": (
            "Web lookups, quick research, and non-sequitur questions tangential to the "
            "current coding task. Runs as an isolated OpenCode subagent on a separate model, "
            "so it never competes for GPU/RAM with the primed context pack. Invoke with "
            "@research, or let the primary agent delegate to it automatically."
        ),
        "mode": "subagent",
        "model": model_ref,
        "prompt": str(RESEARCH_PROMPT_REL),
        "permission": {"edit": "deny", "bash": "ask"},
    }


def set_local(root: Path, path: Path, cfg: dict, model_id: str) -> None:
    provider = cfg.setdefault("provider", {}).setdefault("omlx", {})
    if not provider:
        print(
            "Warning: no provider.omlx block found yet — this config may not be pointed at "
            "your oMLX endpoint. Check opencode.json.example for the expected shape.",
            file=sys.stderr,
        )
    models = provider.setdefault("models", {})
    models["research-lane"] = {
        "name": f"Research lane ({model_id}) \u2014 lighter local model for tangential/lookup tasks"
    }
    cfg.setdefault("agent", {})["research"] = research_agent_block("omlx/research-lane")
    cfg["_comment_research_lane"] = (
        f"Register '{model_id}' as 'research-lane' in oMLX's model directory/admin dashboard "
        "so the research subagent above resolves to it. It can be a distinct small model, or "
        "point at the same underlying model file as fast-lane-small to avoid loading a third "
        "model into oMLX \u2014 your call. oMLX will auto-unload it after its idle TTL if you set one."
    )
    ensure_prompt_file(root)
    save_config(path, cfg)
    print(f"Research lane set to LOCAL: agent.research -> omlx/research-lane ({model_id}), in {path.name}.")
    print("Next: register that model in oMLX's model directory/admin dashboard if you haven't already.")


def set_cloud(root: Path, path: Path, cfg: dict, provider_name: str, model_id: str) -> None:
    model_ref = f"{provider_name}/{model_id}"
    cfg.setdefault("agent", {})["research"] = research_agent_block(model_ref)
    env_var = CLOUD_ENV_VARS.get(provider_name.lower())
    if env_var:
        note = f"Set {env_var} in your shell profile before running OpenCode."
    else:
        note = (
            f"Check OpenCode's provider docs for the API key env var '{provider_name}' expects, "
            "and set it before running OpenCode."
        )
    cfg["_comment_research_lane"] = (
        f"The research agent calls the hosted {provider_name} API using model '{model_id}'. {note}"
    )
    ensure_prompt_file(root)
    save_config(path, cfg)
    print(f"Research lane set to CLOUD: agent.research -> {model_ref}, in {path.name}.")
    print(note)


def set_off(path: Path, cfg: dict) -> None:
    removed = cfg.get("agent", {}).pop("research", None) is not None
    save_config(path, cfg)
    if removed:
        print(f"Research lane disabled: removed agent.research from {path.name}. Everything now runs on the quality lane.")
    else:
        print(f"Research lane was already off (no agent.research entry in {path.name}).")


def print_status(path: Path, cfg: dict) -> None:
    agent = cfg.get("agent", {}).get("research")
    if not agent:
        print(f"Research lane: OFF ({path.name} has no agent.research entry). Everything runs on the quality lane.")
        return
    model = agent.get("model", "?")
    mode = agent.get("mode", "?")
    print(f"Research lane: ON \u2014 model {model}, mode {mode} (in {path.name}).")
    print("Invoke it with @research <question>, or let the primary agent delegate automatically.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "argument_string",
        nargs="*",
        default=[],
        help="status | off | local <oMLX model id> | cloud <provider> <model id>",
    )
    args = ap.parse_args()
    raw = " ".join(args.argument_string).strip()
    tokens = raw.split()
    action = tokens[0].lower() if tokens else "status"
    rest = tokens[1:]

    path = config_path(REPO_ROOT)
    cfg = load_config(path)

    if action == "status":
        print_status(path, cfg)
    elif action == "off":
        set_off(path, cfg)
    elif action == "local":
        if not rest:
            raise SystemExit("Usage: context_research_lane.py local <oMLX model id>")
        set_local(REPO_ROOT, path, cfg, rest[0])
    elif action == "cloud":
        if len(rest) < 2:
            raise SystemExit("Usage: context_research_lane.py cloud <provider> <model id>")
        set_cloud(REPO_ROOT, path, cfg, rest[0], rest[1])
    else:
        raise SystemExit(f"Unknown action: {action!r}. Use status | off | local <model> | cloud <provider> <model>.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
