"""End-to-end test of the Claude Code adapter: feeds scripts/claude_hook.py the JSON payloads
Claude Code sends, in an installed scratch project, and checks the context it injects."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def hook(project: Path, event: str, payload: dict) -> dict | None:
    proc = subprocess.run(
        [sys.executable, "scripts/claude_hook.py", event],
        cwd=project, input=json.dumps(payload), text=True, capture_output=True, timeout=120,
        env={**os.environ, "CONTEXT_FABRIC_AUTO": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def context_of(output: dict | None) -> str:
    return (output or {}).get("hookSpecificOutput", {}).get("additionalContext", "")


class ClaudeHookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        (self.project / "src").mkdir()
        (self.project / "src" / "answer.py").write_text("def answer():\n    return 42\n")
        (self.project / "tests").mkdir()
        (self.project / "tests" / "test_answer.py").write_text("from src.answer import answer\n\ndef test_answer():\n    assert answer() == 42\n")
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        env = {**os.environ, "CONTEXT_FABRIC_BENCHMARK": "1"}
        proc = subprocess.run(["bash", str(ROOT / "install.sh"), str(self.project), "--harness", "claude"],
                              input="", text=True, capture_output=True, env=env, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def warnings(self) -> str:
        log = self.project / ".context-fabric" / "logs" / "plugin-warnings.log"
        return log.read_text() if log.exists() else ""

    def prime_drafted_pack(self) -> str:
        drafts = sorted((self.project / ".context-fabric" / "packs").glob("*.yaml"))
        self.assertTrue(drafts, "auto-plan should have drafted a pack")
        pack = yaml.safe_load(drafts[0].read_text())
        pack["base"]["invariants"] = ["answer() stays deterministic"]
        pack["base"]["acceptance_tests"] = ["tests/test_answer.py passes"]
        pack["base"]["unknowns"] = []
        drafts[0].write_text(yaml.safe_dump(pack, sort_keys=False))
        proc = subprocess.run([sys.executable, "scripts/context_prime.py", "--pack", pack["context_pack"]],
                              cwd=self.project, text=True, capture_output=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return pack["context_pack"]

    def test_session_lifecycle(self):
        sid = {"session_id": "s1"}
        prefix_md = self.project / ".context-fabric" / "active-prefix.md"

        self.assertIsNone(hook(self.project, "session-start", {**sid, "source": "startup"}))
        self.assertIn("no context pack is active", prefix_md.read_text())

        out = hook(self.project, "user-prompt", {**sid, "prompt": "Make answer() return a configurable value"})
        self.assertIn("[context-fabric:auto] I drafted a context pack", context_of(out), self.warnings())
        # Only the first substantive prompt of a session auto-plans.
        self.assertIsNone(hook(self.project, "user-prompt", {**sid, "prompt": "Also update the tests please"}))

        name = self.prime_drafted_pack()
        self.assertIn(f'<context-fabric-pack name="{name}">', prefix_md.read_text())
        out = hook(self.project, "user-prompt", {**sid, "prompt": "continue"})
        self.assertIn(f"Context pack {name} is now active", context_of(out))

        edit = {**sid, "tool_name": "Edit", "tool_input": {"file_path": str(self.project / "src" / "answer.py")}}
        self.assertIsNone(hook(self.project, "pre-tool", edit))
        self.assertIn("is a source_slice of active pack", context_of(hook(self.project, "post-tool", edit)))
        self.assertIsNone(hook(self.project, "post-tool", {**sid, "tool_name": "Read", "tool_input": {}}))

        self.assertIsNone(hook(self.project, "pre-compact", {**sid, "trigger": "auto"}))
        after = context_of(hook(self.project, "session-start", {**sid, "source": "compact"}))
        self.assertIn("Read it now", after)
        self.assertIn("checkpoint block", after)
        self.assertIn("next_decision", after)

        log = (self.project / ".context-fabric" / "session-log" / "s1.jsonl").read_text().splitlines()
        kinds = [json.loads(line)["kind"] for line in log]
        self.assertEqual(kinds, ["tool.execute.before", "tool.execute.after", "tool.execute.after",
                                 "session.compacting", "session.compacted"])
        self.assertNotIn("failed", self.warnings())

    def test_bad_payload_fails_soft(self):
        proc = subprocess.run([sys.executable, "scripts/claude_hook.py", "user-prompt"],
                              cwd=self.project, input="not json", text=True, capture_output=True, timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("claude_hook user-prompt failed", self.warnings())


if __name__ == "__main__":
    unittest.main()
