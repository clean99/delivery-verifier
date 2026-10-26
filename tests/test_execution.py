"""Positive test-body counts are required independently of a zero process exit."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dv.execution import green_execution, receipt
from dv.runners import instrument


class ExecutionReceiptTests(unittest.TestCase):
    def report(self, runner, source):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "test_value.py").write_text(source)
            env = {
                **os.environ,
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            command = [sys.executable, "-m", runner]
            if runner == "pytest":
                command += ["-q", "--tb=long", "-p", "no:cacheprovider"]
            proc = subprocess.run(
                instrument(command, env),
                env=env,
                cwd=directory,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=15,
            )
            return {"status": "normal", "exit_code": proc.returncode}, proc.stdout

    def test_unittest_only_real_passes_qualify(self):
        for decorator, body, expected in (
            ("", "self.assertEqual(1, 1)", True),
            ("@unittest.skip('skip')", "pass", False),
            ("@unittest.expectedFailure", "self.assertEqual(1, 2)", False),
            ("@unittest.expectedFailure", "pass", False),
            ("", "raise RuntimeError('broken')", False),
        ):
            with self.subTest(decorator=decorator, body=body):
                source = "import unittest\nclass TestValue(unittest.TestCase):\n"
                if decorator:
                    source += "    " + decorator + "\n"
                source += "    def test_value(self):\n        " + body + "\n"
                phase, log = self.report("unittest", source)
                self.assertEqual(green_execution(phase, log, "unittest")[0], expected, log)
                if expected:
                    self.assertEqual(receipt(log, "unittest")[0]["passed"], 1)
                    for invalid in (
                        log + log,
                        log.replace(b"Ran 1 test", b"Ran 0 tests"),
                        log.replace(b"OK", b"incomplete"),
                    ):
                        self.assertFalse(green_execution(phase, invalid, "unittest")[0])

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is optional test tooling")
    def test_pytest_skip_expected_failure_and_unexpected_pass_do_not_qualify(self):
        for marker, body, expected in (
            ("", "assert 1 == 1", True),
            ("@pytest.mark.skip", "pass", False),
            ("@pytest.mark.xfail", "assert 1 == 2", False),
            ("@pytest.mark.xfail", "pass", False),
        ):
            with self.subTest(marker=marker, body=body):
                source = "import pytest\n" + marker + "\ndef test_value():\n    " + body + "\n"
                phase, log = self.report("pytest", source)
                self.assertEqual(green_execution(phase, log, "pytest")[0], expected, log)

    def test_malformed_count_record_fails_closed(self):
        log = (
            b'DV-RUNNER-PHASES {"runner": "unittest", '
            b'"tests": ["executed", "passed", "failed", "skipped"]}\n'
        )
        counts, complete = receipt(log, "unittest")
        self.assertFalse(complete)
        self.assertEqual(counts["passed"], 0)

    def test_tap_truncation_cancellation_and_all_skip_fail(self):
        log = (
            b"TAP version 13\n# Subtest: value\nok 1 - value\n1..1\n"
            b"# tests 1\n# pass 1\n# fail 0\n# cancelled 0\n# skipped 0\n"
            b"# todo 0\n# duration_ms 1.0\n"
        )
        phase = {"status": "normal", "exit_code": 0}
        self.assertTrue(green_execution(phase, log, "node-test")[0])
        for invalid in (
            log + log,
            log.replace(b"1..1", b"1..2"),
            log.replace(b"ok 1 - value", b"not ok 1 - value"),
            log.replace(b"# cancelled 0", b"# cancelled 1"),
            log.replace(b"# pass 1", b"# pass 0").replace(b"# skipped 0", b"# skipped 1"),
            log.replace(b"# duration_ms 1.0\n", b""),
        ):
            self.assertFalse(green_execution(phase, invalid, "node-test")[0])
