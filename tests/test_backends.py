from __future__ import annotations

import unittest

from scripts.lib.backends import capabilities_for


class BackendCapabilityTests(unittest.TestCase):
    def test_ollama_is_automatic_but_opaque(self) -> None:
        caps = capabilities_for("ollama")
        self.assertTrue(caps.automatic_prefix_cache)
        self.assertFalse(caps.cache_telemetry)
        self.assertFalse(caps.explicit_warmup)

    def test_omlx_exposes_expert_capabilities(self) -> None:
        caps = capabilities_for("omlx")
        self.assertTrue(caps.automatic_prefix_cache)
        self.assertTrue(caps.cache_telemetry)
        self.assertTrue(caps.persistent_cache)
        self.assertTrue(caps.explicit_warmup)


if __name__ == "__main__":
    unittest.main()
