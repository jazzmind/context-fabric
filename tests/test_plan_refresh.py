from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


SOURCE_ROOT = Path(__file__).resolve().parents[1]


class PlanRefreshTests(unittest.TestCase):
    def test_checkpoint_changed_files_drive_refresh_without_new_version(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".context-fabric/packs").mkdir(parents=True)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / "src/core.py").write_text("def evaluate_policy(x):\n    return x\n")
            (root / "src/approval.py").write_text(
                "from src.core import evaluate_policy\n\ndef approve(x):\n    return evaluate_policy(x)\n"
            )
            (root / "tests/test_approval.py").write_text(
                "from src.approval import approve\n\ndef test_approve():\n    assert approve(True)\n"
            )
            (root / "src/unrelated.py").write_text("def unrelated():\n    return 1\n")

            # Build the same deterministic graph that an installed project would have.
            subprocess.run(
                [sys.executable, str(SOURCE_ROOT / "scripts/context_index.py")],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            parent = {
                "task": "make approval policy reliable",
                "context_pack": "approval:v1",
                "parent_pack": None,
                "base": {
                    "system_prompt": ".opencode/prompts/system.md",
                    "tool_contract": ".opencode/tool-schema.json",
                    "architecture_summary": ".context-fabric/summaries/architecture.md",
                    "dependency_graph": ".context-fabric/graph.json#approval",
                    "source_slices": [{"path": "src/approval.py", "reason": "seed"}],
                    "invariants": ["keep approval behavior"],
                    "acceptance_tests": ["test approval"],
                    "unknowns": [],
                },
                "status": "active",
                "prefix_hash": "sha256:old",
            }
            child = {
                "task": "make approval policy reliable",
                "context_pack": "approval:v2",
                "parent_pack": "approval:v1",
                "base": {
                    "system_prompt": ".opencode/prompts/system.md",
                    "tool_contract": ".opencode/tool-schema.json",
                    "architecture_summary": ".context-fabric/summaries/architecture.md",
                    "dependency_graph": ".context-fabric/graph.json#approval",
                    "source_slices": [],
                    "invariants": [],
                    "acceptance_tests": [],
                    "unknowns": ["Carried forward from approval:v1 checkpoint"],
                },
                "checkpoint": {
                    "changed_files": ["src/core.py"],
                    "verified_facts": ["approval delegates policy evaluation"],
                    "failed_hypotheses": [],
                    "test_status": "passing",
                    "next_decision": "harden evaluator",
                },
                "status": "draft",
            }
            (root / ".context-fabric/packs/approval-v1.yaml").write_text(yaml.safe_dump(parent, sort_keys=False))
            (root / ".context-fabric/packs/approval-v2.yaml").write_text(yaml.safe_dump(child, sort_keys=False))

            proc = subprocess.run(
                [sys.executable, str(SOURCE_ROOT / "scripts/context_plan.py"), "--refresh-pack", "approval:v2"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("Refreshed approval:v2", proc.stdout)
            refreshed = yaml.safe_load((root / ".context-fabric/packs/approval-v2.yaml").read_text())
            paths = [entry["path"] for entry in refreshed["base"]["source_slices"]]
            self.assertIn("src/core.py", paths)
            self.assertIn("src/approval.py", paths)
            self.assertNotIn("src/unrelated.py", paths)
            self.assertEqual(refreshed["context_pack"], "approval:v2")
            self.assertIn("src/core.py", refreshed["selection"]["prior_discovery_files"])


if __name__ == "__main__":
    unittest.main()
