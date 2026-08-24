#!/usr/bin/env python3
"""Run whole-task OpenCode benchmarks across Ollama/oMLX with and without Context Fabric.

The suite intentionally measures task completion rather than isolated inference throughput.
Each run gets a fresh copy of the target repository and records wall time, first agent activity,
OpenCode usage/tool signals, changed files, verification results, and Context Fabric state.

Example:
  python3 scripts/context_benchmark.py \
    --repo ~/Code/my-project \
    --tasks benchmarks/tasks.example.json \
    --repeat 3
"""
from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.backends import backend_spec, load_config  # noqa: E402
from lib.packs import approx_tokens  # noqa: E402

SOURCE_ROOT = Path(__file__).resolve().parents[1]
CF_COMMANDS = [
    "context-activate.md", "context-auto.md", "context-backend.md", "context-checkpoint.md",
    "context-freeze.md", "context-index.md", "context-plan.md", "context-prime.md", "context-refresh.md",
    "context-research-lane.md", "context-status.md", "context-warm.md",
]


@dataclass(frozen=True)
class Lane:
    name: str
    backend: str
    context_fabric: bool


LANES = [
    Lane("ollama-baseline", "ollama", False),
    Lane("ollama-context-fabric", "ollama", True),
    Lane("omlx-baseline", "omlx", False),
    Lane("omlx-context-fabric", "omlx", True),
]


def load_tasks(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    tasks = data.get("tasks") if isinstance(data, dict) else data
    if not isinstance(tasks, list) or not tasks:
        raise SystemExit(f"{path} must contain a non-empty tasks array")
    for i, task in enumerate(tasks):
        if not isinstance(task, dict) or not task.get("id") or not task.get("prompt"):
            raise SystemExit(f"Task #{i + 1} needs id and prompt")
        task.setdefault("verify", [])
    return tasks


def copy_repo(source: Path, dest: Path) -> None:
    ignore_names = {".git", ".context-fabric", "node_modules", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache"}

    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {name for name in names if name in ignore_names}

    shutil.copytree(source, dest, ignore=ignore, dirs_exist_ok=True)


def strip_context_fabric(workspace: Path) -> None:
    # Remove harness-visible CF policy from baseline lanes. The installer appends this block
    # at EOF; pre-0.2 installs had only the opening marker, so EOF is the safe legacy bound.
    agents = workspace / "AGENTS.md"
    if agents.exists():
        text = agents.read_text()
        start_marker = "<!-- context-fabric:auto-mode-instructions -->"
        end_marker = "<!-- /context-fabric:auto-mode-instructions -->"
        start = text.find(start_marker)
        if start >= 0:
            end = text.find(end_marker, start)
            if end >= 0:
                end += len(end_marker)
                text = text[:start] + text[end:]
            else:
                text = text[:start]
            agents.write_text(text.rstrip() + ("\n" if text.strip() else ""))

    plugin = workspace / ".opencode" / "plugins" / "context-fabric.ts"
    if plugin.exists():
        plugin.unlink()
    commands = workspace / ".opencode" / "commands"
    for name in CF_COMMANDS:
        path = commands / name
        if path.exists():
            path.unlink()
    cf_dir = workspace / ".context-fabric"
    if cf_dir.exists():
        shutil.rmtree(cf_dir)


def install_context_fabric(workspace: Path) -> None:
    env = os.environ.copy()
    env["CONTEXT_FABRIC_BENCHMARK"] = "1"
    proc = subprocess.run(
        ["bash", str(SOURCE_ROOT / "install.sh"), str(workspace)],
        input="1\n",
        text=True,
        capture_output=True,
        env=env,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"context-fabric install failed:\n{proc.stdout}\n{proc.stderr}")


def write_lane_configs(workspace: Path, lane: Lane) -> tuple[str, str]:
    cfg = load_config(SOURCE_ROOT / ".context-fabric" / "config.json") if (SOURCE_ROOT / ".context-fabric" / "config.json").exists() else load_config()
    spec = backend_spec(lane.backend, cfg)
    provider_id = "localbench"
    base_url = spec.base_url.rstrip("/") + "/v1"
    opencode = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"{provider_id}/{spec.model}",
        "provider": {
            provider_id: {
                "npm": "@ai-sdk/openai-compatible",
                "name": f"Context Fabric benchmark: {lane.backend}",
                "options": {"baseURL": base_url, "timeout": 600000, "chunkTimeout": 60000},
                "models": {spec.model: {"name": spec.model}},
            }
        },
        "agent": {
            "build": {"model": f"{provider_id}/{spec.model}"},
            "plan": {"model": f"{provider_id}/{spec.model}"},
        },
        "share": "disabled",
    }
    (workspace / "opencode.json").write_text(json.dumps(opencode, indent=2) + "\n")

    if lane.context_fabric:
        cf_path = workspace / ".context-fabric" / "config.json"
        cf_cfg = json.loads(cf_path.read_text()) if cf_path.exists() else {}
        cf_cfg["backend"] = lane.backend
        cf_cfg.setdefault("backends", {}).setdefault(lane.backend, {}).update({"base_url": spec.base_url, "model": spec.model})
        cf_path.parent.mkdir(parents=True, exist_ok=True)
        cf_path.write_text(json.dumps(cf_cfg, indent=2) + "\n")
    return provider_id, spec.model


def initialize_snapshot_git(workspace: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "context-fabric-bench@local"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "Context Fabric Bench"], cwd=workspace, check=True)
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "benchmark baseline"], cwd=workspace, check=True)


def meaningful_event(obj: Any) -> bool:
    text = json.dumps(obj, ensure_ascii=False).lower()
    return any(marker in text for marker in ('"tool"', '"text"', '"assistant"', '"reasoning"'))


def walk(obj: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key, value
            yield from walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk(value)


def extract_session_id(events: list[Any]) -> Optional[str]:
    for event in events:
        for key, value in walk(event):
            if key.lower() in {"sessionid", "session_id", "session"} and isinstance(value, str) and len(value) > 4:
                return value
    return None


def extract_usage(events: list[Any]) -> dict[str, int]:
    """Best-effort normalized usage from OpenCode's evolving JSON event schema.

    We deduplicate token dictionaries by JSON representation to avoid counting the same final
    message usage object several times when it is repeated in update/final events.
    Raw events are always saved next to the result for auditability.
    """
    input_total = output_total = cache_total = reasoning_total = 0
    seen: set[str] = set()
    for event in events:
        for _key, value in walk(event):
            if not isinstance(value, dict):
                continue
            keys = {str(k).lower(): k for k in value}
            relevant = set(keys) & {
                "input", "output", "reasoning", "cache", "input_tokens", "output_tokens",
                "prompt_tokens", "completion_tokens", "cached_tokens", "cache_read",
                "cache_read_input_tokens", "cache_creation_input_tokens",
            }
            if not relevant:
                continue
            canonical = json.dumps(value, sort_keys=True, default=str)
            if canonical in seen:
                continue
            seen.add(canonical)
            def n(*names: str) -> int:
                for name in names:
                    original = keys.get(name)
                    if original is not None and isinstance(value.get(original), (int, float)):
                        return int(value[original])
                return 0
            input_total += n("input", "input_tokens", "prompt_tokens")
            output_total += n("output", "output_tokens", "completion_tokens")
            reasoning_total += n("reasoning")
            cache_total += n("cached_tokens", "cache_read", "cache_read_input_tokens")
            cache_obj = value.get(keys.get("cache", "")) if "cache" in keys else None
            if isinstance(cache_obj, dict):
                cache_total += sum(int(v) for k, v in cache_obj.items() if "read" in str(k).lower() and isinstance(v, (int, float)))
    return {
        "input_tokens": input_total,
        "output_tokens": output_total,
        "reasoning_tokens": reasoning_total,
        "cache_read_tokens": cache_total,
    }


def extract_tool_calls(events: list[Any]) -> int:
    ids: set[str] = set()
    fallback = 0
    for event in events:
        event_text = json.dumps(event, ensure_ascii=False).lower()
        if '"tool"' in event_text and any(marker in event_text for marker in ("call", "execute", "running", "completed")):
            fallback += 1
        for key, value in walk(event):
            if key.lower() in {"toolcallid", "tool_call_id", "callid", "call_id"} and isinstance(value, str):
                ids.add(value)
    return len(ids) if ids else fallback


def run_opencode(workspace: Path, prompt: str, model_ref: str, timeout: int, raw_events_path: Path) -> dict[str, Any]:
    cmd = ["opencode", "run", "--format", "json", "--auto", "--model", model_ref, "--dir", str(workspace), prompt]
    started = time.perf_counter()
    first_event: Optional[float] = None
    first_activity: Optional[float] = None
    events: list[Any] = []
    timed_out = False
    stderr_file = tempfile.TemporaryFile(mode="w+t")
    try:
        proc = subprocess.Popen(cmd, cwd=workspace, stdout=subprocess.PIPE, stderr=stderr_file, text=True, bufsize=1)
    except FileNotFoundError as exc:
        stderr_file.close()
        return {"exit_code": 127, "error": str(exc), "wall_s": 0.0, "events": []}

    assert proc.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    try:
        while True:
            elapsed = time.perf_counter() - started
            if elapsed > timeout:
                timed_out = True
                proc.kill()
                break
            ready = selector.select(timeout=0.25)
            for key, _mask in ready:
                line = key.fileobj.readline()
                if not line:
                    continue
                now = time.perf_counter()
                if first_event is None:
                    first_event = now - started
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    obj = {"type": "stdout", "text": line}
                events.append(obj)
                if first_activity is None and meaningful_event(obj):
                    first_activity = now - started
            if proc.poll() is not None:
                # Drain any buffered final lines after process exit.
                for line in proc.stdout:
                    now = time.perf_counter()
                    if first_event is None:
                        first_event = now - started
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        obj = {"type": "stdout", "text": line}
                    events.append(obj)
                    if first_activity is None and meaningful_event(obj):
                        first_activity = now - started
                break
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    finally:
        selector.close()
        wall = time.perf_counter() - started
        stderr_file.seek(0)
        stderr_text = stderr_file.read()
        stderr_file.close()

    raw_events_path.parent.mkdir(parents=True, exist_ok=True)
    raw_events_path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + ("\n" if events else ""))
    return {
        "exit_code": -9 if timed_out else (proc.returncode if proc.returncode is not None else -9),
        "timed_out": timed_out,
        "wall_s": wall,
        "first_event_s": first_event,
        "first_agent_activity_s": first_activity,
        "stderr": stderr_text[-8000:],
        "events": events,
    }


def run_verification(workspace: Path, commands: list[str], timeout: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for command in commands:
        started = time.perf_counter()
        try:
            proc = subprocess.run(command, cwd=workspace, shell=True, text=True, capture_output=True, timeout=timeout)
            results.append({
                "command": command,
                "exit_code": proc.returncode,
                "wall_s": time.perf_counter() - started,
                "stdout": proc.stdout[-4000:],
                "stderr": proc.stderr[-4000:],
            })
        except subprocess.TimeoutExpired as exc:
            results.append({"command": command, "exit_code": -9, "wall_s": timeout, "stdout": str(exc.stdout or ""), "stderr": "verification timeout"})
    return results


def changed_files(workspace: Path) -> list[str]:
    proc = subprocess.run(["git", "status", "--porcelain=v1"], cwd=workspace, text=True, capture_output=True)
    result: list[str] = []
    for line in proc.stdout.splitlines():
        if len(line) >= 4:
            path = line[3:].strip()
            if path.startswith(".context-fabric/") or path == "opencode.json":
                continue
            result.append(path)
    return result


def cf_metrics(workspace: Path) -> dict[str, Any]:
    active = workspace / ".context-fabric" / "active.json"
    state: dict[str, Any] = {}
    if active.exists():
        try:
            state = json.loads(active.read_text())
        except json.JSONDecodeError:
            state = {}
    pack_name = state.get("context_pack")
    prefix_tokens = 0
    if pack_name:
        prefix = workspace / ".context-fabric" / "prefixes" / f"{str(pack_name).replace(':', '-')}.prefix.txt"
        if prefix.exists():
            prefix_tokens = approx_tokens(prefix.read_text())
    tool_log_events = 0
    log_dir = workspace / ".context-fabric" / "session-log"
    if log_dir.exists():
        for path in log_dir.glob("*.jsonl"):
            tool_log_events += sum(1 for line in path.read_text().splitlines() if '"kind": "tool.execute.before"' in line)
    return {
        "active_pack": pack_name,
        "prefix_tokens_approx": prefix_tokens,
        "logged_tool_calls": tool_log_events,
    }


def run_one(repo: Path, lane: Lane, task: dict[str, Any], repeat_index: int, run_root: Path, timeout: int, verify_timeout: int, keep: bool) -> dict[str, Any]:
    work_dir = run_root / "workspaces" / f"{task['id']}__{lane.name}__r{repeat_index + 1}"
    copy_repo(repo, work_dir)
    strip_context_fabric(work_dir)
    if lane.context_fabric:
        install_context_fabric(work_dir)
    provider, model = write_lane_configs(work_dir, lane)
    initialize_snapshot_git(work_dir)

    raw_path = run_root / "raw" / f"{task['id']}__{lane.name}__r{repeat_index + 1}.jsonl"
    oc = run_opencode(work_dir, task["prompt"], f"{provider}/{model}", timeout, raw_path)
    usage = extract_usage(oc.pop("events", []))
    # Re-read raw only for tool/session extraction, keeping result JSON compact.
    events = [json.loads(line) for line in raw_path.read_text().splitlines() if line.strip()] if raw_path.exists() else []
    tool_calls = extract_tool_calls(events)
    session_id = extract_session_id(events)
    verification = run_verification(work_dir, list(task.get("verify") or []), verify_timeout)
    verification_ok = all(v["exit_code"] == 0 for v in verification) if verification else True
    result = {
        "task_id": task["id"],
        "lane": lane.name,
        "backend": lane.backend,
        "context_fabric": lane.context_fabric,
        "repeat": repeat_index + 1,
        "success": oc["exit_code"] == 0 and verification_ok,
        "opencode_exit_code": oc["exit_code"],
        "wall_s": round(float(oc["wall_s"]), 4),
        "first_event_s": oc.get("first_event_s"),
        "first_agent_activity_s": oc.get("first_agent_activity_s"),
        "session_id": session_id,
        "tool_calls": tool_calls,
        "changed_files": changed_files(work_dir),
        "usage": usage,
        "verification": verification,
        "context_fabric_metrics": cf_metrics(work_dir) if lane.context_fabric else {},
        "stderr_tail": oc.get("stderr", ""),
        "raw_events": str(raw_path.relative_to(run_root)),
    }
    if not keep:
        shutil.rmtree(work_dir, ignore_errors=True)
    return result


def median(values: list[float]) -> Optional[float]:
    clean = [float(v) for v in values if isinstance(v, (int, float))]
    return round(statistics.median(clean), 3) if clean else None


def summarize(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for lane in LANES:
        rows = [r for r in results if r["lane"] == lane.name]
        if not rows:
            continue
        summaries.append({
            "lane": lane.name,
            "runs": len(rows),
            "success_rate": round(sum(1 for r in rows if r.get("success")) / len(rows), 3),
            "median_wall_s": median([r.get("wall_s") for r in rows]),
            "median_first_activity_s": median([r.get("first_agent_activity_s") for r in rows]),
            "median_input_tokens": median([(r.get("usage") or {}).get("input_tokens", 0) for r in rows]),
            "median_output_tokens": median([(r.get("usage") or {}).get("output_tokens", 0) for r in rows]),
            "median_cache_read_tokens": median([(r.get("usage") or {}).get("cache_read_tokens", 0) for r in rows]),
            "median_tool_calls": median([r.get("tool_calls", 0) for r in rows]),
        })
    return summaries


def paired_effects(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by = {row["lane"]: row for row in summaries}
    effects: list[dict[str, Any]] = []
    for backend in ("ollama", "omlx"):
        base = by.get(f"{backend}-baseline")
        cf = by.get(f"{backend}-context-fabric")
        if not base or not cf:
            continue
        def pct(metric: str, lower_is_better: bool = True) -> Optional[float]:
            b, c = base.get(metric), cf.get(metric)
            if not isinstance(b, (int, float)) or not isinstance(c, (int, float)) or b == 0:
                return None
            raw = (b - c) / b * 100 if lower_is_better else (c - b) / b * 100
            return round(raw, 1)
        effects.append({
            "backend": backend,
            "wall_time_improvement_pct": pct("median_wall_s"),
            "first_activity_improvement_pct": pct("median_first_activity_s"),
            "input_token_reduction_pct": pct("median_input_tokens"),
            "success_rate_delta": round(cf["success_rate"] - base["success_rate"], 3),
        })
    return effects


def write_report(run_root: Path, metadata: dict[str, Any], results: list[dict[str, Any]]) -> None:
    summaries = summarize(results)
    effects = paired_effects(summaries)
    payload = {"metadata": metadata, "summary": summaries, "paired_effects": effects, "results": results}
    (run_root / "results.json").write_text(json.dumps(payload, indent=2) + "\n")

    lines = [
        "# Context Fabric benchmark", "",
        f"- Target repo: `{metadata['repo']}`",
        f"- Tasks: {metadata['task_count']}",
        f"- Repeats: {metadata['repeat']}", "",
        "## Lane summary", "",
        "| Lane | Runs | Success | Median wall | First activity | Input tokens | Output tokens | Cache-read tokens | Tool calls |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            f"| {row['lane']} | {row['runs']} | {row['success_rate']:.0%} | {row['median_wall_s']}s | "
            f"{row['median_first_activity_s']}s | {row['median_input_tokens']} | {row['median_output_tokens']} | "
            f"{row['median_cache_read_tokens']} | {row['median_tool_calls']} |"
        )
    lines += ["", "## Paired Context Fabric effect", "", "Positive percentages are improvements/reductions.", "",
              "| Backend | Wall time | First activity | Input tokens | Success-rate delta |", "|---|---:|---:|---:|---:|"]
    for row in effects:
        lines.append(
            f"| {row['backend']} | {row['wall_time_improvement_pct']}% | {row['first_activity_improvement_pct']}% | "
            f"{row['input_token_reduction_pct']}% | {row['success_rate_delta']:+.1%} |"
        )
    lines += ["", "## Interpretation", "", (
        "Use task success and wall-clock completion as primary metrics. Token counts and first-activity latency are diagnostics. "
        "Ollama cache-read tokens may remain zero/unknown because its public API does not expose a stable cached-token counter; "
        "that is telemetry opacity, not evidence of a cache miss. Raw OpenCode JSON event streams are retained under `raw/`."
    ), ""]
    (run_root / "report.md").write_text("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--tasks", required=True, type=Path)
    ap.add_argument("--lanes", default="all", help="Comma-separated lane names or 'all'.")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=1800, help="Seconds per OpenCode run.")
    ap.add_argument("--verify-timeout", type=int, default=300)
    ap.add_argument("--output", type=Path, help="Output directory; defaults to benchmarks/results/<timestamp>.")
    ap.add_argument("--keep-workspaces", action="store_true")
    args = ap.parse_args()

    repo = args.repo.expanduser().resolve()
    tasks_path = args.tasks.expanduser().resolve()
    if not repo.is_dir():
        raise SystemExit(f"Repo not found: {repo}")
    if shutil.which("opencode") is None:
        raise SystemExit("opencode CLI is not on PATH")
    tasks = load_tasks(tasks_path)
    selected_names = {lane.name for lane in LANES} if args.lanes == "all" else {name.strip() for name in args.lanes.split(",") if name.strip()}
    lanes = [lane for lane in LANES if lane.name in selected_names]
    unknown = selected_names - {lane.name for lane in lanes}
    if unknown:
        raise SystemExit(f"Unknown lane(s): {', '.join(sorted(unknown))}")

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    run_root = (args.output or (SOURCE_ROOT / "benchmarks" / "results" / timestamp)).resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    total = len(tasks) * len(lanes) * args.repeat
    index = 0
    # Rotate lane order across task/repeat pairs. Fixed baseline->CF ordering can make CF look
    # artificially faster because the previous lane loaded model weights or warmed shared
    # backend state. Rotation is deterministic and balances that effect over >= len(lanes) runs.
    run_plan: list[tuple[dict[str, Any], Lane, int]] = []
    for task_index, task in enumerate(tasks):
        for repeat_index in range(args.repeat):
            if lanes:
                offset = (task_index + repeat_index) % len(lanes)
                ordered = lanes[offset:] + lanes[:offset]
            else:
                ordered = []
            run_plan.extend((task, lane, repeat_index) for lane in ordered)

    for task, lane, repeat_index in run_plan:
        index += 1
        print(f"[{index}/{total}] {task['id']} :: {lane.name} :: repeat {repeat_index + 1}", flush=True)
        try:
            result = run_one(repo, lane, task, repeat_index, run_root, args.timeout, args.verify_timeout, args.keep_workspaces)
        except Exception as exc:  # benchmark should preserve failures as data
            result = {
                "task_id": task["id"], "lane": lane.name, "backend": lane.backend,
                "context_fabric": lane.context_fabric, "repeat": repeat_index + 1,
                "success": False, "error": f"{type(exc).__name__}: {exc}",
            }
        results.append(result)
        (run_root / "partial-results.json").write_text(json.dumps(results, indent=2) + "\n")

    metadata = {
        "repo": str(repo), "tasks": str(tasks_path), "task_count": len(tasks), "repeat": args.repeat,
        "lanes": [lane.name for lane in lanes], "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    write_report(run_root, metadata, results)
    print(f"Results: {run_root / 'results.json'}")
    print(f"Report:  {run_root / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
