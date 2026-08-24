from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.context_benchmark import paired_effects, strip_context_fabric, summarize


class BenchmarkSummaryTests(unittest.TestCase):
    def test_paired_effect_reports_context_fabric_delta(self) -> None:
        results = [
            {"lane": "ollama-baseline", "success": True, "wall_s": 100, "first_agent_activity_s": 10, "usage": {"input_tokens": 10000, "output_tokens": 1000, "cache_read_tokens": 0}, "tool_calls": 10},
            {"lane": "ollama-context-fabric", "success": True, "wall_s": 75, "first_agent_activity_s": 6, "usage": {"input_tokens": 7000, "output_tokens": 1000, "cache_read_tokens": 0}, "tool_calls": 8},
        ]
        summary = summarize(results)
        effects = paired_effects(summary)
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0]["backend"], "ollama")
        self.assertEqual(effects[0]["wall_time_improvement_pct"], 25.0)
        self.assertEqual(effects[0]["input_token_reduction_pct"], 30.0)

    def test_baseline_strips_context_fabric_agents_policy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            agents = root / "AGENTS.md"
            agents.write_text(
                "# Project policy\nkeep me\n\n"
                "<!-- context-fabric:auto-mode-instructions -->\n"
                "## context-fabric auto mode\nremove me\n"
                "<!-- /context-fabric:auto-mode-instructions -->\n"
            )
            strip_context_fabric(root)
            text = agents.read_text()
            self.assertIn("keep me", text)
            self.assertNotIn("context-fabric auto mode", text)
            self.assertNotIn("remove me", text)


if __name__ == "__main__":
    unittest.main()
