"""Minimal client for probing oMLX's cache-hit/usage reporting.

oMLX's exact usage-field naming for cache hits isn't pinned across versions in its public
docs (see README "What's actually implemented" section) — community testing confirms usage
stats only appear when streaming with `stream_options.include_usage=true`
(https://lilting.ch/en/articles/omlx-039-dev2-m1-max-tested). This client makes a tiny probe
request against your configured endpoint, and reports every field in the returned `usage`
object whose key contains "cache" (case-insensitive), plus the raw object, so you can see
exactly what your install calls it and tighten field names as needed.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import requests


def probe_usage(
    base_url: str,
    model: str,
    prefix_text: str,
    *,
    probe_suffix: str = "\n\n(status probe — reply with just: ok)",
    max_tokens: int = 4,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Sends `prefix_text + probe_suffix` as a single user message with streaming +
    include_usage on, and returns {"raw_usage": {...} | None, "cache_fields": {...}, "error": str | None}.
    """
    url = base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prefix_text + probe_suffix}],
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    try:
        resp = requests.post(url, json=payload, stream=True, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"raw_usage": None, "cache_fields": {}, "error": str(e)}

    last_usage: Optional[dict] = None
    try:
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(chunk, dict) and chunk.get("usage"):
                last_usage = chunk["usage"]
    except requests.RequestException as e:
        return {"raw_usage": last_usage, "cache_fields": {}, "error": str(e)}

    cache_fields = {}
    if last_usage:
        cache_fields = {k: v for k, v in last_usage.items() if "cache" in k.lower()}
    return {"raw_usage": last_usage, "cache_fields": cache_fields, "error": None}
