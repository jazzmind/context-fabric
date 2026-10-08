#!/usr/bin/env python3
"""Claude Code adapter for context-fabric: the hook-based twin of .opencode/plugins/context-fabric.ts.

Registered in .claude/settings.json as `python3 scripts/claude_hook.py <event>`, run from the
project root. Reads the hook payload as JSON on stdin. Fails soft: every error is logged to
.context-fabric/logs/plugin-warnings.log and the hook exits 0, so a broken context runtime
never blocks the session.

Mapping from the opencode plugin:
  system.transform (pack prefix in system prompt) -> CLAUDE.md `@.context-fabric/active-prefix.md`
      import, kept in sync by lib.packs on activate/deactivate. Hook output is capped at 10k
      chars, far below a pack prefix, so hooks only carry short notices.
  chat.message (reindex, auto-plan)               -> user-prompt  (UserPromptSubmit)
  session.compacting (checkpoint prompt)          -> pre-compact (PreCompact) scaffolds the next
      pack version; session-start with source=compact then asks for the 5-field checkpoint.
      Claude Code hooks cannot rewrite the compaction prompt itself.
  tool.execute.before/after, file.edited          -> pre-tool / post-tool
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

ROOT = Path.cwd()
CF_DIR = ROOT / ".context-fabric"
LOG_DIR = CF_DIR / "logs"
SESSION_LOG_DIR = CF_DIR / "session-log"
STATE_DIR = CF_DIR / "claude-sessions"
INDEX_STAMP = CF_DIR / "claude-last-index"
AUTO_INDEX_MIN_INTERVAL_S = 15
AUTO_PLAN_MIN_PROMPT_CHARS = 12
SCRIPT_TIMEOUT_S = 90
NOTE_MAX_CHARS = 8000  # stay under Claude Code's 10k additionalContext cap
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

CHECKPOINT_FIELDS = (
    "1. changed_files: every file touched since the pack was primed\n"
    "2. verified_facts: things you confirmed true by reading code or running tests\n"
    "3. failed_hypotheses: approaches you tried and ruled out, and why\n"
    "4. test_status: current pass/fail state, verbatim if you have it\n"
    "5. next_decision: the single next decision or action, stated concretely"
)


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def warn(message: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / "plugin-warnings.log").open("a") as fh:
            fh.write(f"[{now_iso()}] [claude] {message}\n")
    except OSError:
        pass


def session_log(session_id: str, record: dict) -> None:
    try:
        SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (SESSION_LOG_DIR / f"{session_id}.jsonl").open("a") as fh:
            fh.write(json.dumps({"ts": now_iso(), "harness": "claude", **record}) + "\n")
    except OSError as e:
        warn(f"session log write failed: {e}")


def load_state(session_id: str) -> dict:
    path = STATE_DIR / f"{session_id}.json"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(session_id: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{session_id}.json").write_text(json.dumps(state) + "\n")


def auto_enabled() -> bool:
    """Same precedence as the opencode plugin: CONTEXT_FABRIC_AUTO, then config `auto`, else on."""
    env = os.environ.get("CONTEXT_FABRIC_AUTO")
    if env is not None:
        v = env.strip().lower()
        if v in {"0", "false", "off", "no"}:
            return False
        if v in {"1", "true", "on", "yes"}:
            return True
    try:
        raw = json.loads((CF_DIR / "config.json").read_text())
        if isinstance(raw.get("auto"), bool):
            return raw["auto"]
    except (OSError, json.JSONDecodeError):
        pass
    return True


def active_pack() -> str | None:
    from lib import packs

    return packs.get_active_pack()


def run_script(*args: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / args[0]), *args[1:]],
            cwd=ROOT, capture_output=True, text=True, timeout=SCRIPT_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    text = "\n".join(s for s in (proc.stdout, proc.stderr) if s.strip())
    return proc.returncode == 0, text


def clip(text: str, limit: int = NOTE_MAX_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…(truncated)"


def emit_context(event_name: str, notes: list[str]) -> None:
    if notes:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": clip("\n\n".join(notes))}}))


# --- event handlers ------------------------------------------------------------------------


def on_session_start(payload: dict) -> None:
    from lib import packs

    session_id = payload.get("session_id", "unknown-session")
    packs.sync_active_prefix_md()  # repair a stale import file before CLAUDE.md is next read
    state = load_state(session_id)
    pack = active_pack()
    notes: list[str] = []
    if payload.get("source") == "compact":
        session_log(session_id, {"kind": "session.compacted", "context_pack": pack})
        if pack:
            notes.append(
                f"[context-fabric] Active context pack {pack}; its frozen prefix is in "
                ".context-fabric/active-prefix.md. Read it now: it was not part of the compaction summary."
            )
        if auto_enabled() and state.pop("pending_checkpoint", None):
            notes.append(
                "[context-fabric:auto] Compaction just finished and the next context pack version was "
                "scaffolded (see .context-fabric/packs and .context-fabric/history for its name). Fill in its "
                "checkpoint block from what you know of the work so far, as exactly these five sections:\n"
                f"{CHECKPOINT_FIELDS}\n"
                "Then re-derive source_slices, invariants and acceptance_tests for the next unit of work and "
                "run /context-prime on it yourself. Don't wait to be asked."
            )
    state["seen_pack"] = pack
    save_state(session_id, state)
    emit_context("SessionStart", notes)


def on_user_prompt(payload: dict) -> None:
    session_id = payload.get("session_id", "unknown-session")
    prompt = payload.get("prompt") or ""
    state = load_state(session_id)
    notes: list[str] = []

    if auto_enabled():
        last = float(INDEX_STAMP.read_text()) if INDEX_STAMP.exists() else 0.0
        if time.time() - last > AUTO_INDEX_MIN_INTERVAL_S:
            CF_DIR.mkdir(parents=True, exist_ok=True)
            INDEX_STAMP.write_text(str(time.time()))
            ok, text = run_script("context_index.py")
            if not ok:
                warn(f"auto-mode autoIndex failed: {text}")

        if not state.get("planned") and len(prompt) >= AUTO_PLAN_MIN_PROMPT_CHARS and not prompt.startswith("/"):
            state["planned"] = True
            ok, text = run_script("context_plan.py", "--task", prompt)
            if ok:
                notes.append(
                    "[context-fabric:auto] I drafted a context pack for this task from the static code graph:\n"
                    f"{clip(text, 4000)}\n"
                    "Review the code cone, finalize invariants and acceptance_tests, resolve unknowns, refine "
                    "subtasks, then run /context-prime <name>:v1 yourself. Only ask if an unknown genuinely "
                    "needs the user."
                )
            else:
                warn(f"auto-mode autoPlan failed for session {session_id}: {text}")

    # CLAUDE.md imports are read at session start, so a pack activated mid-session needs a pointer.
    pack = active_pack()
    if pack and pack != state.get("seen_pack"):
        notes.append(
            f"[context-fabric] Context pack {pack} is now active. Read .context-fabric/active-prefix.md "
            "and treat it as the stable task context for the rest of this session."
        )
    state["seen_pack"] = pack
    save_state(session_id, state)
    emit_context("UserPromptSubmit", notes)


def on_pre_compact(payload: dict) -> None:
    session_id = payload.get("session_id", "unknown-session")
    pack = active_pack()
    session_log(session_id, {"kind": "session.compacting", "trigger": payload.get("trigger"), "context_pack": pack})
    if not (pack and auto_enabled()):
        return
    ok, text = run_script("context_checkpoint.py", "--pack", pack)
    if ok:
        state = load_state(session_id)
        state["pending_checkpoint"] = True
        save_state(session_id, state)
    else:
        warn(f"auto-mode autoCheckpoint failed for pack {pack}: {text}")


def on_pre_tool(payload: dict) -> None:
    session_log(payload.get("session_id", "unknown-session"),
                {"kind": "tool.execute.before", "tool": payload.get("tool_name"), "context_pack": active_pack()})


def on_post_tool(payload: dict) -> None:
    from lib import packs

    session_id = payload.get("session_id", "unknown-session")
    tool = payload.get("tool_name")
    pack = active_pack()
    session_log(session_id, {"kind": "tool.execute.after", "tool": tool, "context_pack": pack})
    if tool not in EDIT_TOOLS or not pack:
        return
    raw = (payload.get("tool_input") or {}).get("file_path") or (payload.get("tool_input") or {}).get("notebook_path")
    pack_file = packs.pack_file_path(pack)
    if not raw or not pack_file.exists():
        return
    # Resolve both sides: the tool path and cwd can differ by symlinks (macOS /var -> /private/var).
    path, root = Path(raw).resolve(), ROOT.resolve()
    rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else raw
    if rel in pack_file.read_text():
        message = (
            f"{rel} is a source_slice of active pack {pack} and was just edited. The pack's prefix_hash is now "
            "stale. Run /context-checkpoint at the next task boundary to roll a new pack version."
        )
        warn(message)
        emit_context("PostToolUse", [f"[context-fabric] {message}"])


HANDLERS = {
    "session-start": on_session_start,
    "user-prompt": on_user_prompt,
    "pre-compact": on_pre_compact,
    "pre-tool": on_pre_tool,
    "post-tool": on_post_tool,
}


def main() -> int:
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    handler = HANDLERS.get(event)
    if handler is None:
        warn(f"unknown claude_hook event {event!r}")
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        handler(payload)
    except Exception as e:  # fail soft: never block the session
        warn(f"claude_hook {event} failed: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
