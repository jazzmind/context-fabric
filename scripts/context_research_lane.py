#!/usr/bin/env python3
"""Configure an optional isolated OpenCode research subagent.

The lane is backend-agnostic. ``local <model>`` uses Context Fabric's currently resolved local
backend; ``local ollama <model>`` / ``local omlx <model>`` selects explicitly.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.backends import resolve_backend  # noqa: E402

REPO_ROOT = Path.cwd()
RESEARCH_PROMPT_REL = Path(".opencode/prompts/research-agent.md")
RESEARCH_PROMPT_TEXT = """\
You are the research subagent for a coding session using context-fabric. You are intentionally
isolated from the primary task context.

Rules:
- Answer only the delegated question.
- Prefer lookup/read-only tools over guessing.
- Keep the final answer short and directly usable by the primary agent.
- Do not edit repository files or perform destructive actions.
- If the question requires deep knowledge of the current codebase that is not supplied in the
  prompt, say so and hand it back rather than fabricating context.
"""
CLOUD_ENV_VARS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


def config_path(root: Path) -> Path:
    real = root / "opencode.json"
    if real.exists():
        return real
    example = root / "opencode.json.example"
    if example.exists():
        return example
    raise SystemExit("No opencode.json or opencode.json.example found. Run install.sh first.")


def load_config(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc


def save_config(path: Path, cfg: dict) -> None:
    path.write_text(json.dumps(cfg, indent=2) + "\n")


def ensure_prompt_file(root: Path) -> None:
    path = root / RESEARCH_PROMPT_REL
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RESEARCH_PROMPT_TEXT)


def research_agent_block(model_ref: str) -> dict:
    return {
        "description": "Read-only lookup/research tasks isolated from the primary coding context.",
        "mode": "subagent",
        "model": model_ref,
        "prompt": str(RESEARCH_PROMPT_REL),
        "permission": {"edit": "deny", "bash": "ask"},
    }


def ensure_provider_model(cfg: dict, provider_name: str, model_id: str, base_url: str) -> None:
    provider = cfg.setdefault("provider", {}).setdefault(provider_name, {})
    provider.setdefault("npm", "@ai-sdk/openai-compatible")
    provider.setdefault("name", f"{provider_name} local")
    provider.setdefault("options", {}).setdefault("baseURL", base_url.rstrip("/") + "/v1")
    provider.setdefault("models", {}).setdefault(model_id, {"name": f"Research lane ({model_id})"})


def set_local(root: Path, path: Path, cfg: dict, tokens: list[str]) -> None:
    if not tokens:
        raise SystemExit("Usage: context_research_lane.py local [ollama|omlx] <model id>")
    if len(tokens) >= 2 and tokens[0].lower() in {"ollama", "omlx"}:
        backend_name, model_id = tokens[0].lower(), tokens[1]
        spec = resolve_backend(backend_name)
    else:
        model_id = tokens[0]
        spec = resolve_backend()
        backend_name = spec.name
    ensure_provider_model(cfg, backend_name, model_id, spec.base_url)
    cfg.setdefault("agent", {})["research"] = research_agent_block(f"{backend_name}/{model_id}")
    cfg["_comment_research_lane"] = (
        f"Research subagent uses local {backend_name}/{model_id}. It is context-isolated, but if "
        "it shares the same local server/device it can still compete for compute/memory."
    )
    ensure_prompt_file(root)
    save_config(path, cfg)
    print(f"Research lane set to LOCAL: {backend_name}/{model_id} ({path.name}).")


def set_cloud(root: Path, path: Path, cfg: dict, provider_name: str, model_id: str) -> None:
    model_ref = f"{provider_name}/{model_id}"
    cfg.setdefault("agent", {})["research"] = research_agent_block(model_ref)
    env_var = CLOUD_ENV_VARS.get(provider_name.lower())
    note = f"Set {env_var}." if env_var else f"Set the API credential required by {provider_name}."
    cfg["_comment_research_lane"] = f"Research subagent uses hosted {model_ref}. {note}"
    ensure_prompt_file(root)
    save_config(path, cfg)
    print(f"Research lane set to CLOUD: {model_ref} ({path.name}).")
    print(note)


def set_off(path: Path, cfg: dict) -> None:
    removed = cfg.get("agent", {}).pop("research", None) is not None
    save_config(path, cfg)
    print("Research lane disabled." if removed else "Research lane was already off.")


def print_status(path: Path, cfg: dict) -> None:
    agent = cfg.get("agent", {}).get("research")
    if not agent:
        print(f"Research lane: OFF ({path.name}).")
        return
    print(f"Research lane: ON — {agent.get('model', '?')} ({path.name}).")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("argument_string", nargs="*", default=[])
    args = ap.parse_args()
    tokens = " ".join(args.argument_string).strip().split()
    action = tokens[0].lower() if tokens else "status"
    rest = tokens[1:]
    path = config_path(REPO_ROOT)
    cfg = load_config(path)
    if action == "status":
        print_status(path, cfg)
    elif action == "off":
        set_off(path, cfg)
    elif action == "local":
        set_local(REPO_ROOT, path, cfg, rest)
    elif action == "cloud":
        if len(rest) < 2:
            raise SystemExit("Usage: context_research_lane.py cloud <provider> <model id>")
        set_cloud(REPO_ROOT, path, cfg, rest[0], rest[1])
    else:
        raise SystemExit("Use status | off | local [ollama|omlx] <model> | cloud <provider> <model>.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
