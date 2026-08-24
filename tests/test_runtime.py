from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path

import yaml


class RuntimeLifecycleTests(unittest.TestCase):
    def test_freeze_activate_and_detect_mutation(self) -> None:
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            os.chdir(root)
            try:
                (root / ".opencode/prompts").mkdir(parents=True)
                (root / ".opencode/prompts/system.md").write_text("Be precise.\n")
                (root / ".opencode/tool-schema.json").write_text('{"tools": []}\n')
                (root / ".context-fabric/summaries").mkdir(parents=True)
                (root / ".context-fabric/summaries/architecture.md").write_text("# Architecture\n")
                (root / "src").mkdir()
                (root / "src/main.py").write_text("def answer():\n    return 42\n")
                graph = {
                    "file_count": 1,
                    "nodes": {
                        "src/main.py": {
                            "path": "src/main.py", "is_test": False, "loc": 2,
                            "symbols": [{"name": "answer", "kind": "function", "line": 1}],
                            "imports_resolved": [], "imported_by": [], "symbol_refs": [],
                            "referenced_by": [], "related_tests": [],
                        }
                    },
                }
                (root / ".context-fabric/graph.json").write_text(json.dumps(graph))

                from scripts.lib import packs as packs_module
                from scripts.lib import runtime as runtime_module
                packs = importlib.reload(packs_module)
                runtime = importlib.reload(runtime_module)

                pack = {
                    "task": "return the answer",
                    "context_pack": "answer:v1",
                    "parent_pack": None,
                    "base": {
                        "system_prompt": ".opencode/prompts/system.md",
                        "tool_contract": ".opencode/tool-schema.json",
                        "architecture_summary": ".context-fabric/summaries/architecture.md",
                        "dependency_graph": ".context-fabric/graph.json#answer",
                        "source_slices": [{"path": "src/main.py", "reason": "task seed"}],
                        "invariants": ["answer remains deterministic"],
                        "acceptance_tests": ["answer() == 42"],
                        "unknowns": [],
                    },
                    "budget": packs.DEFAULT_BUDGET.copy(),
                    "execution": {"prefix": "immutable", "history": "append_only", "compaction": "task_checkpoint"},
                    "subtasks": ["verify"],
                    "status": "finalized",
                    "created_at": packs.now_iso(),
                }
                packs.save_pack(pack)
                frozen, prefix, prefix_path = runtime.freeze_pack("answer:v1")
                self.assertEqual(frozen["status"], "frozen")
                self.assertTrue(prefix_path.exists())
                self.assertTrue(frozen["prefix_hash"].startswith("sha256:"))

                active, _, _ = runtime.activate_pack("answer:v1")
                self.assertEqual(active["status"], "active")
                self.assertEqual(packs.get_active_pack(), "answer:v1")

                (root / "src/main.py").write_text("def answer():\n    return 43\n")
                with self.assertRaises(RuntimeError):
                    runtime.validate_frozen_pack("answer:v1")
            finally:
                os.chdir(original)
                # Restore module globals for subsequent tests/imports.
                import scripts.lib.packs as packs_module
                import scripts.lib.runtime as runtime_module
                importlib.reload(packs_module)
                importlib.reload(runtime_module)


if __name__ == "__main__":
    unittest.main()
