"""Compatibility shim for older integrations.

New code should use ``lib.backends``. This module remains so installed projects that import
``probe_usage`` do not break during migration.
"""
from __future__ import annotations

from typing import Any

from .backends import BackendSpec, capabilities_for, probe_backend


def probe_usage(base_url: str, model: str, prefix_text: str, **kwargs: Any) -> dict[str, Any]:
    spec = BackendSpec(
        name="omlx",
        base_url=base_url,
        model=model,
        api_style="openai",
        capabilities=capabilities_for("omlx"),
    )
    result = probe_backend(spec, prefix_text, max_tokens=int(kwargs.get("max_tokens", 4)), timeout=float(kwargs.get("timeout", 60.0)))
    raw = result.get("raw") or {}
    usage = raw.get("usage") if isinstance(raw, dict) else None
    cached = result.get("cached_tokens")
    cache_fields = {"cached_tokens": cached} if cached is not None else {}
    return {"raw_usage": usage, "cache_fields": cache_fields, "error": result.get("error")}
