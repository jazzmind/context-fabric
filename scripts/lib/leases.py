"""GPU/context lease broker: mutual exclusion between the persistent local
agent (headlong, in a Lima VM, on qwen3.8:27b-mlx) and interactive coding
(opencode, on the host, on a qwen3.6 coding model) for the one shared GPU.

Design constraints, deliberately different from a normal context-fabric pack:

- The lease is machine-global, not per-project, so it does NOT live under
  the cwd's `.context-fabric/` the way pack_events do (see packs.py's
  PROJECT_ROOT = Path.cwd()). It lives under state_dir (default
  ~/.local/state/gpu-lease), independent of what directory a caller is in.
- The lock is HOST-LOCAL ONLY. File locks are not assumed coherent across
  the virtiofs host<->guest boundary, and only the host can act anyway:
  `ollama stop` / keep_alive=0 and `limactl shell` are both host-only
  operations. The guest gets a read-only mount of state.json and never
  takes the lock.
- Staleness is heartbeat-based, not PID-liveness-based: a `coding` lease
  held by a process inside a *suspended* Lima VM has a perfectly live PID
  and a frozen clock, so `os.kill(pid, 0)` would say "still alive" through
  an entire sleep cycle. We use elapsed wall-clock heartbeat age.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import signal
import socket
import time
from pathlib import Path
from typing import Any, Optional

from . import packs

VALID_STATES = ("cos", "coding", "shared-degraded")


def _state_dir(config: dict[str, Any]) -> Path:
    raw = config.get("gpu_lease", {}).get("state_dir", "~/.local/state/gpu-lease")
    override = os.environ.get("EA_LEASE_HOME")
    return Path(override).expanduser() if override else Path(raw).expanduser()


def state_path(config: dict[str, Any]) -> Path:
    return _state_dir(config) / "state.json"


def lock_path(config: dict[str, Any]) -> Path:
    return _state_dir(config) / "gpu.lock"


def history_path(config: dict[str, Any]) -> Path:
    return _state_dir(config) / "history" / "gpu-events.jsonl"


@contextlib.contextmanager
def locked(config: dict[str, Any], *, timeout: float = 10.0):
    """Host-local exclusive critical section, kernel-released on process death.

    Uses flock(2) via fcntl -- released automatically if the holder dies,
    unlike a marker file, so a crashed broker invocation can never leave a
    stale lock behind.
    """
    path = lock_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                fh.close()
                raise TimeoutError(f"could not acquire {path} within {timeout}s")
            time.sleep(0.2)
    try:
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def append_event(config: dict[str, Any], event: dict[str, Any]) -> None:
    path = history_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": packs.now_iso(), **event}
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")
    # Best-effort mirror into the calling project's own pack history, so
    # `/context-status` in whatever repo you're in can correlate a slow
    # pack activation with a GPU handoff that was happening at the same time.
    if (Path.cwd() / ".context-fabric").is_dir():
        try:
            packs.append_history({"event": f"gpu.{event.get('event', '?')}",
                                   **{k: v for k, v in event.items() if k != "event"}})
        except OSError:
            pass


def read_state(config: dict[str, Any]) -> dict[str, Any]:
    path = state_path(config)
    if not path.exists():
        return {"state": "cos", "holder": None, "model": None, "acquired_at": None,
                 "heartbeat_at": None, "ttl_s": None, "hold": None, "generation": 0}
    return json.loads(path.read_text())


def _write_state(config: dict[str, Any], data: dict[str, Any]) -> None:
    path = state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)  # atomic within a filesystem; readers never see a torn write


def is_stale(state: dict[str, Any], config: dict[str, Any]) -> bool:
    if state.get("state") == "cos" or state.get("holder") is None:
        return False
    heartbeat_s = config.get("gpu_lease", {}).get("heartbeat_s", 60)
    now = time.time()
    hb_at = state.get("heartbeat_at")
    if hb_at:
        hb_epoch = _iso_to_epoch(hb_at)
        if hb_epoch is not None and now - hb_epoch > max(3 * heartbeat_s, 300):
            return True
    holder = state["holder"]
    if holder.get("host") == socket.gethostname():
        pid = holder.get("pid")
        if pid:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                pass  # process exists, owned by someone else -- not stale on this basis
    ttl_s = state.get("ttl_s")
    acquired_at = state.get("acquired_at")
    if ttl_s and acquired_at:
        acq_epoch = _iso_to_epoch(acquired_at)
        if acq_epoch is not None and now - acq_epoch > ttl_s:
            return True
    return False


def _iso_to_epoch(iso: str) -> Optional[float]:
    try:
        import datetime
        return datetime.datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return None


def acquire(config: dict[str, Any], *, kind: str, model: Optional[str] = None,
            ttl_s: Optional[int] = None, reason: str = "", extra: Optional[dict] = None,
            lock_timeout: float = 10.0, holder_pid: Optional[int] = None) -> dict[str, Any]:
    """Acquire the lease for `kind` ("coding" or "cos"). Steals a stale lease.
    Raises RuntimeError if a live, non-stale lease is held by someone else.

    holder_pid defaults to the parent process (os.getppid()), NOT os.getpid():
    gpu_lease.py is a one-shot CLI that exits immediately after printing its
    result, so recording its own PID would make the lease look stale the
    instant acquire() returns. The real long-lived holder is whatever invoked
    this CLI (the ea-code wrapper's shell, holding a heartbeat loop + EXIT trap).
    """
    if kind not in VALID_STATES:
        raise ValueError(f"invalid lease kind: {kind}")
    ttl_s = ttl_s or config.get("gpu_lease", {}).get("default_ttl_s", 7200)
    with locked(config, timeout=lock_timeout):
        state = read_state(config)
        if state.get("hold") and state["hold"] != kind:
            raise RuntimeError(f"lease is held pinned to {state['hold']!r} (gpu-lease hold none to clear)")
        if state.get("state") not in ("cos", None) and state.get("holder") is not None:
            if not is_stale(state, config):
                raise RuntimeError(
                    f"GPU lease already held by {state['holder']} since {state.get('acquired_at')}"
                )
            append_event(config, {"event": "lease_broken", "reason": "stale",
                                   "previous_holder": state.get("holder")})
        generation = state.get("generation", 0) + 1
        now = packs.now_iso()
        pid = holder_pid if holder_pid is not None else os.getppid()
        new_state = {
            "state": kind,
            "holder": {
                "kind": kind, "pid": pid, "host": socket.gethostname(),
                "reason": reason, **(extra or {}),
            },
            "model": model,
            "acquired_at": now,
            "heartbeat_at": now,
            "ttl_s": ttl_s,
            "hold": state.get("hold"),
            "generation": generation,
        }
        _write_state(config, new_state)
        append_event(config, {"event": "acquired", "state": kind, "generation": generation,
                               "reason": reason, "pid": os.getpid()})
        return new_state


def heartbeat(config: dict[str, Any], *, generation: Optional[int] = None) -> dict[str, Any]:
    with locked(config):
        state = read_state(config)
        if generation is not None and state.get("generation") != generation:
            raise RuntimeError("stale generation -- this lease was already released or stolen")
        state["heartbeat_at"] = packs.now_iso()
        _write_state(config, state)
        return state


def release(config: dict[str, Any], *, generation: Optional[int] = None) -> dict[str, Any]:
    with locked(config):
        state = read_state(config)
        if generation is not None and state.get("generation") != generation:
            # Already stolen/released -- releasing a lease you no longer hold is a no-op,
            # not an error, so a returning zombie holder can't clobber the next holder.
            return state
        new_state = {
            "state": "cos", "holder": None, "model": None,
            "acquired_at": None, "heartbeat_at": None, "ttl_s": None,
            "hold": state.get("hold"), "generation": state.get("generation", 0) + 1,
        }
        _write_state(config, new_state)
        append_event(config, {"event": "released", "generation": new_state["generation"]})
        return new_state


def set_hold(config: dict[str, Any], hold: Optional[str]) -> dict[str, Any]:
    if hold is not None and hold not in VALID_STATES:
        raise ValueError(f"invalid hold value: {hold}")
    with locked(config):
        state = read_state(config)
        state["hold"] = hold
        _write_state(config, state)
        append_event(config, {"event": "hold_set", "hold": hold})
        return state
