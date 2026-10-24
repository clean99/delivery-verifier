from __future__ import annotations

import subprocess
import sys
import unittest

from support import ROOT


class DemoTests(unittest.TestCase):
    def test_offline_demo_rejects_hollow_test_then_passes_readiness(self):
        result = subprocess.run(
            [sys.executable, "examples/demo/run.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Hollow test rejected", result.stdout)
        self.assertIn("Meaningful regression proved: red=1, green=0", result.stdout)
        self.assertIn("gate: PASS (exit 0)", result.stdout)
        self.assertIn("[FAIL] V5 human approval", result.stdout)
        self.assertIn("SYNTHETIC review fixture", result.stdout)
        self.assertIn("The throwaway repository and its logs have been removed", result.stdout)
