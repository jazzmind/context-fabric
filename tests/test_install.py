import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["CONTEXT_FABRIC_BENCHMARK"] = "1"  # skip the interactive research-lane prompt
    return subprocess.run(["bash", str(ROOT / script), *args], input="", text=True, capture_output=True, env=env, timeout=120)


class InstallRoundTripTest(unittest.TestCase):
    def test_fresh_install_writes_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run("install.sh", tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue((Path(tmp) / ".context-fabric" / "config.json").is_file())
            self.assertIn("context-fabric:auto-mode-instructions", (Path(tmp) / "AGENTS.md").read_text())

    def test_uninstall_keeps_project_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            (dest / "AGENTS.md").write_text("# My project\n")
            (dest / "docs").mkdir()
            (dest / "docs" / "benchmarking.md").write_text("project-owned\n")
            self.assertEqual(run("install.sh", tmp).returncode, 0)

            proc = run("uninstall.sh", tmp, "--purge")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual((dest / "AGENTS.md").read_text(), "# My project\n")
            self.assertEqual((dest / "docs" / "benchmarking.md").read_text(), "project-owned\n")
            self.assertFalse((dest / ".context-fabric").exists())
            self.assertFalse((dest / ".opencode" / "plugins" / "context-fabric.ts").exists())
            self.assertFalse((dest / "scripts").exists())


if __name__ == "__main__":
    unittest.main()
