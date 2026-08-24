from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.lib.graph import build_graph, select_task_context, task_graph_slice


class GraphSelectionTests(unittest.TestCase):
    def test_task_context_uses_imports_symbols_and_related_tests(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / "src/core.py").write_text(
                "def evaluate_policy(request):\n    return request.get('allow', False)\n"
            )
            (root / "src/approval.py").write_text(
                "from src.core import evaluate_policy\n\n"
                "def approve_request(request):\n    return evaluate_policy(request)\n"
            )
            (root / "tests/test_approval.py").write_text(
                "from src.approval import approve_request\n\n"
                "def test_policy_approval():\n    assert approve_request({'allow': True})\n"
            )
            (root / "src/unrelated.py").write_text("def unrelated_widget():\n    return 1\n")

            graph = build_graph(root)
            selected = select_task_context(
                graph,
                "make approval policy evaluation reliable",
                seeds=["src/approval.py"],
                source_budget_tokens=10000,
            )
            paths = {entry["path"] for entry in selected}
            self.assertIn("src/approval.py", paths)
            self.assertIn("src/core.py", paths)
            self.assertIn("tests/test_approval.py", paths)
            self.assertNotIn("src/unrelated.py", paths)

            sliced = task_graph_slice(graph, paths)
            self.assertEqual(set(sliced["nodes"]), paths)
            self.assertNotIn("src/unrelated.py", sliced["nodes"])

    def test_installed_context_fabric_runtime_is_not_indexed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            (root / "scripts").mkdir()
            (root / ".context-fabric").mkdir()
            (root / "src/app.py").write_text("def app_feature():\n    return True\n")
            (root / "scripts/context_plan.py").write_text("def runtime_helper():\n    return True\n")
            import json
            (root / ".context-fabric/install-manifest.json").write_text(
                json.dumps({"runtime_files": ["scripts/context_plan.py"]})
            )
            graph = build_graph(root)
            self.assertIn("src/app.py", graph["nodes"])
            self.assertNotIn("scripts/context_plan.py", graph["nodes"])


if __name__ == "__main__":
    unittest.main()
