#!/usr/bin/env python3
"""GPU/context lease broker CLI.

Arbitrates the one shared GPU between the persistent EA agent (headlong, in
the `ea` Lima VM, qwen3.8:27b-mlx) and interactive coding (opencode, on the
host, a qwen3.6 coding model). Nobody should call this directly except the
`ea-code` / `ea-supervisor` wrapper scripts and `doctor` for troubleshooting.

    gpu_lease.py status  [--json]
    gpu_lease.py acquire coding|cos [--model M] [--ttl 7200] [--wait 60] [--reason TEXT]
    gpu_lease.py release [--generation N]
    gpu_lease.py heartbeat [--generation N]
    gpu_lease.py hold {cos|coding|shared-degraded|none}
    gpu_lease.py doctor
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import backends, leases, packs  # noqa: E402


def _config() -> dict:
    return backends.load_config()


def cmd_status(args: argparse.Namespace) -> int:
    config = _config()
    state = leases.read_state(config)
    state["stale"] = leases.is_stale(state, config)
    if args.json:
        print(json.dumps(state, indent=2))
    else:
        packs.eprint(f"state:      {state['state']}")
        packs.eprint(f"holder:     {state.get('holder')}")
        packs.eprint(f"model:      {state.get('model')}")
        packs.eprint(f"acquired:   {state.get('acquired_at')}")
        packs.eprint(f"heartbeat:  {state.get('heartbeat_at')}")
        packs.eprint(f"stale:      {state['stale']}")
        packs.eprint(f"hold:       {state.get('hold')}")
        packs.eprint(f"generation: {state.get('generation')}")
    return 0


def cmd_acquire(args: argparse.Namespace) -> int:
    config = _config()
    lease_cfg = config.get("gpu_lease", {})
    model = args.model or (lease_cfg.get("coding_model") if args.kind == "coding"
                            else lease_cfg.get("agent_model"))
    deadline = time.monotonic() + args.wait
    while True:
        try:
            state = leases.acquire(config, kind=args.kind, model=model,
                                    ttl_s=args.ttl, reason=args.reason)
            break
        except RuntimeError as exc:
            if time.monotonic() >= deadline:
                packs.eprint(f"acquire failed: {exc}")
                return 1
            time.sleep(1)
    if args.kind == "coding":
        _pause_agent(config)
        _unload(config, lease_cfg.get("agent_model"))
    if args.json:
        print(json.dumps(state, indent=2))
    else:
        packs.eprint(f"acquired {args.kind}, generation={state['generation']}")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    config = _config()
    state = leases.release(config, generation=args.generation)
    lease_cfg = config.get("gpu_lease", {})
    _resume_agent(config)
    _warm(config, lease_cfg.get("agent_model"))
    if args.json:
        print(json.dumps(state, indent=2))
    else:
        packs.eprint(f"released, generation={state['generation']}")
    return 0


def cmd_heartbeat(args: argparse.Namespace) -> int:
    config = _config()
    try:
        leases.heartbeat(config, generation=args.generation)
    except RuntimeError as exc:
        packs.eprint(str(exc))
        return 1
    return 0


def cmd_hold(args: argparse.Namespace) -> int:
    config = _config()
    hold = None if args.value == "none" else args.value
    leases.set_hold(config, hold)
    packs.eprint(f"hold set to {hold!r}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = _config()
    lease_cfg = config.get("gpu_lease", {})
    ok = True
    try:
        spec = backends.resolve_backend("ollama", probe_reachability=True)
        packs.eprint(f"ollama reachable: yes ({spec.base_url})")
    except Exception as exc:  # noqa: BLE001 -- doctor reports, never raises
        packs.eprint(f"ollama reachable: NO ({exc})")
        ok = False
        spec = None
    if spec is not None:
        loaded = backends.loaded_models(spec)
        packs.eprint(f"models resident: {[m.get('name') for m in loaded] or 'none'}")
    state = leases.read_state(config)
    packs.eprint(f"lease state: {state['state']} (stale={leases.is_stale(state, config)})")
    vm = lease_cfg.get("lima_instance", "ea")
    vm_status = subprocess.run(["limactl", "list", vm, "--format", "{{.Status}}"],
                                capture_output=True, text=True).stdout.strip()
    packs.eprint(f"lima instance {vm!r}: {vm_status or 'not found'}")
    unit = lease_cfg.get("lima_unit", "ea-thinkers")
    if vm_status == "Running":
        unit_status = subprocess.run(
            ["limactl", "shell", vm, "--", "systemctl", "--user", "is-active", unit],
            capture_output=True, text=True).stdout.strip()
        packs.eprint(f"guest unit {unit!r}: {unit_status or 'unknown'}")
    packs.eprint(f"lock: {leases.lock_path(config)}")
    packs.eprint(f"state file: {leases.state_path(config)}")
    return 0 if ok else 1


def _pause_agent(config: dict) -> None:
    lease_cfg = config.get("gpu_lease", {})
    vm, unit = lease_cfg.get("lima_instance", "ea"), lease_cfg.get("lima_unit", "ea-thinkers")
    subprocess.run(["limactl", "shell", vm, "--", "systemctl", "--user", "stop", unit],
                    capture_output=True, timeout=60)
    leases.append_event(config, {"event": "agent_paused", "unit": unit})


def _resume_agent(config: dict) -> None:
    lease_cfg = config.get("gpu_lease", {})
    vm, unit = lease_cfg.get("lima_instance", "ea"), lease_cfg.get("lima_unit", "ea-thinkers")
    subprocess.run(["limactl", "shell", vm, "--", "systemctl", "--user", "start", unit],
                    capture_output=True, timeout=60)
    leases.append_event(config, {"event": "agent_resumed", "unit": unit})


def _unload(config: dict, model: str | None) -> None:
    if not model:
        return
    try:
        spec = backends.resolve_backend("ollama")
        result = backends.unload_model(spec, model)
        leases.append_event(config, {"event": "model_unloaded", **result})
    except Exception as exc:  # noqa: BLE001 -- never let GPU handoff die on unload failure
        leases.append_event(config, {"event": "model_unload_error", "error": str(exc)})


def _warm(config: dict, model: str | None) -> None:
    if not model:
        return
    try:
        spec = backends.resolve_backend("ollama")
        import requests
        requests.post(f"{spec.base_url}/api/generate", json={"model": model}, timeout=10)
        leases.append_event(config, {"event": "model_warm_requested", "model": model})
    except Exception as exc:  # noqa: BLE001
        leases.append_event(config, {"event": "model_warm_error", "error": str(exc)})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("status"); p.add_argument("--json", action="store_true"); p.set_defaults(func=cmd_status)

    p = sub.add_parser("acquire")
    p.add_argument("kind", choices=["coding", "cos"])
    p.add_argument("--model")
    p.add_argument("--ttl", type=int)
    p.add_argument("--wait", type=float, default=60.0)
    p.add_argument("--reason", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_acquire)

    p = sub.add_parser("release")
    p.add_argument("--generation", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_release)

    p = sub.add_parser("heartbeat")
    p.add_argument("--generation", type=int)
    p.set_defaults(func=cmd_heartbeat)

    p = sub.add_parser("hold")
    p.add_argument("value", choices=["cos", "coding", "shared-degraded", "none"])
    p.set_defaults(func=cmd_hold)

    p = sub.add_parser("doctor"); p.set_defaults(func=cmd_doctor)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
