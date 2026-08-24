"""Backend capability abstraction for local inference servers.

Context Fabric's correctness lives above the inference server: deterministic pack assembly,
versioning, activation, and checkpointing. Backend adapters only describe optional runtime
capabilities and telemetry. Core context management must continue to work when every backend
method in this module is a no-op.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

import requests

PROJECT_ROOT = Path.cwd()
CONFIG_PATH = PROJECT_ROOT / ".context-fabric" / "config.json"


@dataclass(frozen=True)
class BackendCapabilities:
    automatic_prefix_cache: bool
    cache_telemetry: bool
    persistent_cache: bool
    speculative_decode: bool
    explicit_warmup: bool
    notes: str = ""


@dataclass(frozen=True)
class BackendSpec:
    name: str
    base_url: str
    model: str
    api_style: str
    capabilities: BackendCapabilities

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "api_style": self.api_style,
            "capabilities": asdict(self.capabilities),
        }


DEFAULT_CONFIG: dict[str, Any] = {
    "auto": True,
    "backend": "auto",
    "backends": {
        "ollama": {
            "base_url": "http://127.0.0.1:11434",
            "model": "qwen3.8:27b-mlx",
        },
        "omlx": {
            "base_url": "http://127.0.0.1:8000",
            "model": "Qwen3.8-27B-8bit",
        },
    },
    "context": {
        "target_prefix_tokens": 32000,
        "source_budget_tokens": 18000,
        "working_context_tokens": 64000,
    },
}


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if path.exists():
        try:
            user = json.loads(path.read_text())
        except json.JSONDecodeError:
            user = {}
        _deep_merge(config, user)
    return config


def save_config(config: dict[str, Any], path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n")


def ensure_default_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = load_config(path)
    if not path.exists():
        save_config(config, path)
    return config


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def capabilities_for(name: str) -> BackendCapabilities:
    normalized = name.lower()
    if normalized == "ollama":
        return BackendCapabilities(
            automatic_prefix_cache=True,
            cache_telemetry=False,
            persistent_cache=False,
            speculative_decode=True,
            explicit_warmup=False,
            notes=(
                "Ollama manages MLX prompt snapshots/cache automatically; public APIs expose "
                "timing/token counts but not a stable cached-token counter."
            ),
        )
    if normalized == "omlx":
        return BackendCapabilities(
            automatic_prefix_cache=True,
            cache_telemetry=True,
            persistent_cache=True,
            speculative_decode=True,
            explicit_warmup=True,
            notes=(
                "oMLX exposes OpenAI-compatible cached-token/timing fields and configurable "
                "paged/DFlash cache behavior; exact advanced settings remain backend-owned."
            ),
        )
    return BackendCapabilities(
        automatic_prefix_cache=False,
        cache_telemetry=False,
        persistent_cache=False,
        speculative_decode=False,
        explicit_warmup=False,
        notes="Generic OpenAI-compatible backend; Context Fabric makes no cache assumptions.",
    )


def _env_or(config: dict[str, Any], backend: str, key: str, env: str, fallback: str) -> str:
    return os.getenv(env) or str(config.get("backends", {}).get(backend, {}).get(key) or fallback)


def backend_spec(name: str, config: Optional[dict[str, Any]] = None) -> BackendSpec:
    config = config or load_config()
    normalized = name.lower()
    if normalized == "ollama":
        return BackendSpec(
            name="ollama",
            base_url=_env_or(config, "ollama", "base_url", "OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            model=_env_or(config, "ollama", "model", "OLLAMA_MODEL", "qwen3.8:27b-mlx"),
            api_style="ollama",
            capabilities=capabilities_for("ollama"),
        )
    if normalized == "omlx":
        return BackendSpec(
            name="omlx",
            base_url=_env_or(config, "omlx", "base_url", "OMLX_BASE_URL", "http://127.0.0.1:8000"),
            model=_env_or(config, "omlx", "model", "OMLX_QUALITY_MODEL", "Qwen3.8-27B-8bit"),
            api_style="openai",
            capabilities=capabilities_for("omlx"),
        )
    generic = config.get("backends", {}).get(normalized, {})
    return BackendSpec(
        name=normalized,
        base_url=str(generic.get("base_url", "http://127.0.0.1:8000")),
        model=str(generic.get("model", "local-model")),
        api_style=str(generic.get("api_style", "openai")),
        capabilities=capabilities_for(normalized),
    )


def _reachable(spec: BackendSpec, timeout: float = 0.6) -> bool:
    try:
        if spec.name == "ollama":
            response = requests.get(spec.base_url.rstrip("/") + "/api/tags", timeout=timeout)
        else:
            response = requests.get(spec.base_url.rstrip("/") + "/v1/models", timeout=timeout)
        return response.ok
    except requests.RequestException:
        return False


def resolve_backend(requested: Optional[str] = None, *, probe_reachability: bool = False) -> BackendSpec:
    config = load_config()
    requested = (requested or os.getenv("CONTEXT_FABRIC_BACKEND") or config.get("backend") or "auto").lower()
    if requested != "auto":
        return backend_spec(requested, config)

    # Ollama is the reference/default deployment. When a command explicitly asks for a live
    # probe we fail over to oMLX if Ollama is not running; normal offline commands stay
    # deterministic and simply describe the default without touching the network.
    ollama = backend_spec("ollama", config)
    if not probe_reachability or _reachable(ollama):
        return ollama
    omlx = backend_spec("omlx", config)
    if _reachable(omlx):
        return omlx
    return ollama


def probe_backend(spec: BackendSpec, prefix_text: str, *, max_tokens: int = 4, timeout: float = 60.0) -> dict[str, Any]:
    if spec.name == "ollama":
        return _probe_ollama(spec, prefix_text, max_tokens=max_tokens, timeout=timeout)
    return _probe_openai(spec, prefix_text, max_tokens=max_tokens, timeout=timeout)


def _probe_ollama(spec: BackendSpec, prefix_text: str, *, max_tokens: int, timeout: float) -> dict[str, Any]:
    url = spec.base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": spec.model,
        "messages": [
            {"role": "system", "content": prefix_text},
            {"role": "user", "content": "Context Fabric diagnostic probe. Reply only: ok"},
        ],
        "stream": False,
        "options": {"num_predict": max_tokens},
        "keep_alive": "10m",
    }
    started = time.perf_counter()
    try:
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        return {"backend": spec.name, "error": str(exc), "raw": None}
    wall = time.perf_counter() - started
    return {
        "backend": spec.name,
        "error": None,
        "cache_visibility": "opaque",
        "cached_tokens": None,
        "prompt_tokens": body.get("prompt_eval_count"),
        "completion_tokens": body.get("eval_count"),
        "prompt_eval_duration_s": _ns_to_s(body.get("prompt_eval_duration")),
        "generation_duration_s": _ns_to_s(body.get("eval_duration")),
        "total_duration_s": _ns_to_s(body.get("total_duration")) or wall,
        "raw": body,
    }


def _probe_openai(spec: BackendSpec, prefix_text: str, *, max_tokens: int, timeout: float) -> dict[str, Any]:
    url = spec.base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": spec.model,
        "messages": [
            {"role": "system", "content": prefix_text},
            {"role": "user", "content": "Context Fabric diagnostic probe. Reply only: ok"},
        ],
        "max_tokens": max_tokens,
        "stream": False,
    }
    started = time.perf_counter()
    try:
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        return {"backend": spec.name, "error": str(exc), "raw": None}
    wall = time.perf_counter() - started
    usage = body.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens")
    if cached is None:
        cached = _find_cached_tokens(usage)
    return {
        "backend": spec.name,
        "error": None,
        "cache_visibility": "explicit" if cached is not None else "unknown",
        "cached_tokens": cached,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "prompt_eval_duration_s": usage.get("prompt_eval_duration"),
        "generation_duration_s": usage.get("generation_duration"),
        "time_to_first_token_s": usage.get("time_to_first_token"),
        "total_duration_s": usage.get("total_time") or wall,
        "raw": body,
    }


def warm_backend(spec: BackendSpec, prefix_text: str, *, timeout: float = 120.0) -> dict[str, Any]:
    """Best-effort runtime warmup.

    Ollama deliberately returns a no-op: its agent-oriented MLX snapshot/cache behavior is
    automatic and a synthetic warm request is not guaranteed to match OpenCode's exact chat
    template. oMLX supports an explicit diagnostic prefill and reports whether it reused
    cached tokens.
    """
    if not spec.capabilities.explicit_warmup:
        return {
            "backend": spec.name,
            "performed": False,
            "reason": "backend owns cache warming automatically; first real agent turn will warm the exact request shape",
        }
    result = probe_backend(spec, prefix_text, max_tokens=1, timeout=timeout)
    return {"backend": spec.name, "performed": result.get("error") is None, "probe": result}


def _find_cached_tokens(value: Any) -> Optional[int]:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = key.lower()
            if "cache" in lowered and "token" in lowered and isinstance(child, (int, float)):
                return int(child)
        for child in value.values():
            found = _find_cached_tokens(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_cached_tokens(child)
            if found is not None:
                return found
    return None


def _ns_to_s(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value) / 1_000_000_000
    return None
