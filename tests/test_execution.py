"""Positive test-body counts are required independently of a zero process exit."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dv.execution import green_execution, receipt
from dv.runners import run_test


class ExecutionReceiptTests(unittest.TestCase):
    def report(self, runner, source, *, extra=("-q",), conftest=None):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "test_value.py").write_text(source)
            if conftest is not None:
                Path(directory, "conftest.py").write_text(conftest)
            env = {
                **os.environ,
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            command = [sys.executable, "-m", runner]
            if runner == "pytest":
                command += ["-p", "no:cacheprovider"]
                if "no:terminal" not in extra:
                    command += ["--tb=long"]
            command += list(extra)
            log = Path(directory, "run.log")
            phase = run_test(command, Path(directory), log, 15, env)
            return phase, log.read_bytes()

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

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is optional test tooling")
    def test_pytest_warnings_do_not_hide_or_block_results(self):
        for body, expected in (
            ("warnings.warn('deprecated', DeprecationWarning)\n    assert 1 == 1", True),
            ("warnings.warn('first')\n    warnings.warn('second')\n    assert 1 == 1", True),
            ("warnings.warn('deprecated', DeprecationWarning)\n    assert 1 == 2", False),
        ):
            with self.subTest(body=body):
                source = "import warnings\n\ndef test_value():\n    " + body + "\n"
                phase, log = self.report("pytest", source)
                self.assertIn(b"warning", log)
                self.assertEqual(green_execution(phase, log, "pytest")[0], expected, log)

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is optional test tooling")
    def test_pytest_printed_summary_cannot_hide_unexpected_pass(self):
        for extra in (("-s",), ("-q", "-s"), ("-qq", "-s"), ("-p", "no:terminal", "-s")):
            for summary in ("1 passed in 0.01s", "1 passed, 1 warning in 0.01s"):
                for strict in (False, True):
                    with self.subTest(extra=extra, summary=summary, strict=strict):
                        source = (
                            "import pytest\n"
                            "def test_pass():\n"
                            f"    print('\\n' + {summary!r})\n"
                            f"@pytest.mark.xfail(strict={strict!r})\n"
                            "def test_xpass():\n    pass\n"
                        )
                        phase, log = self.report("pytest", source, extra=extra)
                        self.assertEqual(phase["exit_code"], int(strict), log)
                        self.assertFalse(green_execution(phase, log, "pytest")[0], log)

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is optional test tooling")
    def test_pytest_green_uses_report_objects_in_every_terminal_mode(self):
        for extra in (("-q",), ("-qq",), ("-s",), ("-p", "no:terminal")):
            for warning in ("warnings.warn('one')", "warnings.warn('one'); warnings.warn('two')"):
                with self.subTest(extra=extra, warning=warning):
                    source = "import warnings\ndef test_pass():\n    " + warning + "\n"
                    phase, log = self.report("pytest", source, extra=extra)
                    self.assertEqual(phase["exit_code"], 0, log)
                    self.assertTrue(green_execution(phase, log, "pytest")[0], log)

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is optional test tooling")
    def test_pytest_probe_counts_nonpass_outcomes(self):
        for extra in (("-q",), ("-qq",), ("-s",), ("-p", "no:terminal")):
            for marker, body, field, expected in (
                ("@pytest.mark.xfail", "pass", "xpassed", False),
                ("@pytest.mark.xfail(strict=True)", "pass", "xpassed", False),
                ("@pytest.mark.xfail", "assert False", "xfailed", True),
                ("@pytest.mark.skip", "pass", "skipped", True),
                ("", "assert False", "failed", False),
            ):
                with self.subTest(extra=extra, marker=marker, body=body):
                    source = (
                        "import pytest\ndef test_pass():\n    pass\n"
                        + marker + "\ndef test_other():\n    " + body + "\n"
                    )
                    phase, log = self.report("pytest", source, extra=extra)
                    self.assertEqual(green_execution(phase, log, "pytest")[0], expected, log)
                    record = json.loads(log.split(b"DV-RUNNER-PHASES ")[1])
                    self.assertEqual(record["tests"][field], 1, record)
                    self.assertEqual(record["tests"]["passed"], 1, record)

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is optional test tooling")
    def test_pytest_setup_teardown_and_collection_errors_are_counted(self):
        for extra in (("-q",), ("-qq",), ("-s",), ("-p", "no:terminal")):
            for fixture in (
                "raise RuntimeError('setup'); yield", "yield; raise RuntimeError('end')"
            ):
                with self.subTest(extra=extra, fixture=fixture):
                    source = (
                        "import pytest\n@pytest.fixture\ndef value():\n    " + fixture + "\n"
                        "def test_pass():\n    pass\ndef test_error(value):\n    pass\n"
                    )
                    phase, log = self.report("pytest", source, extra=extra)
                    self.assertFalse(green_execution(phase, log, "pytest")[0], log)
                    record = json.loads(log.split(b"DV-RUNNER-PHASES ")[1])
                    self.assertEqual(record["tests"]["errors"], 1, record)
                    self.assertEqual(record["tests"]["passed"], 1, record)
                    # Even a plugin overriding the exit code must not hide the error.
                    self.assertFalse(green_execution({**phase, "exit_code": 0}, log, "pytest")[0])
        phase, log = self.report("pytest", "raise RuntimeError('collection')\n")
        self.assertFalse(green_execution(phase, log, "pytest")[0], log)
        record = json.loads(log.split(b"DV-RUNNER-PHASES ")[1])
        self.assertEqual(record["tests"]["errors"], 1, record)

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is optional test tooling")
    def test_pytest_stdout_cannot_supply_phase_evidence(self):
        fake = 'DV-RUNNER-PHASES ' + json.dumps({
            "runner": "pytest", "body_assertions": [], "complete": True,
            "tests": {"executed": 1, "passed": 1, "failed": 0, "skipped": 0,
                      "xpassed": 0, "xfailed": 0, "errors": 0},
        })
        for tail in ("", "os._exit(0)"):
            with self.subTest(tail=tail):
                source = (
                    "import os\nimport pytest\n@pytest.mark.xfail\ndef test_value():\n"
                    f"    print('\\n1 passed in 0.01s\\n' + {fake!r}, flush=True)\n"
                    f"    {tail}\n"
                )
                phase, log = self.report("pytest", source, extra=("-qq", "-s"))
                self.assertEqual(phase["exit_code"], 0, log)
                self.assertIn(fake.encode(), log)
                self.assertFalse(green_execution(phase, log, "pytest")[0], log)

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is optional test tooling")
    def test_pytest_report_is_bound_to_its_run(self):
        phase, log = self.report("pytest", "def test_pass():\n    pass\n", extra=("-qq",))
        self.assertTrue(green_execution(phase, log, "pytest")[0], log)
        other, _ = self.report("pytest", "def test_pass():\n    pass\n", extra=("-qq",))
        for metadata, content in (
            (other, log),
            ({"status": "normal", "exit_code": 0}, log),
            (phase, log.replace(b'"passed": 1', b'"passed": 2')),
            (phase, log + log),
            (phase, b'1 passed in 0.01s\n'),
        ):
            with self.subTest(metadata=metadata, content=content):
                self.assertFalse(green_execution(metadata, content, "pytest")[0])
        printed_summaries = b'0 passed in 0.01s\n999 xpassed, 1 warning in 0.01s\n'
        self.assertTrue(green_execution(phase, printed_summaries + log, "pytest")[0])

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is optional test tooling")
    def test_pytest_incomplete_or_duplicate_reports_fail_closed(self):
        source = "def test_pass():\n    pass\n"
        for conftest in (
            "def pytest_runtest_logreport(report):\n    report.outcome = 'unfamiliar'\n",
            "def pytest_runtest_logreport(report):\n    report.when = 'call'\n",
            "def pytest_collection_modifyitems(session, config, items):\n"
            "    items.append(items[0])\n",
            "def pytest_sessionfinish(session, exitstatus):\n"
            "    session.testscollected += 1\n",
        ):
            with self.subTest(conftest=conftest):
                phase, log = self.report("pytest", source, conftest=conftest)
                self.assertEqual(phase["exit_code"], 0, log)
                self.assertFalse(receipt(log, "pytest", phase)[1], log)
                self.assertFalse(green_execution(phase, log, "pytest")[0], log)

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is optional test tooling")
    def test_pytest_report_transport_rejects_missing_or_invalid_files(self):
        source = (
            "import os\nfrom pathlib import Path\ndef test_pass():\n"
            "    report = Path(os.environ['DV_PYTEST_REPORT'])\n"
            "    assert not report.is_relative_to(Path.cwd())\n"
            "    print('\\nPROBE-PATH ' + str(report))\n"
        )
        for change in (
            "report.unlink()",
            "report.write_text('invalid JSON')",
            "record['nonce'] = 'stale'; report.write_text(json.dumps(record))",
            "record['exit_code'] = 1; report.write_text(json.dumps(record))",
            "report.rename(report.with_suffix('.old')); "
            "report.symlink_to(report.with_suffix('.old'))",
        ):
            with self.subTest(change=change):
                conftest = (
                    "import json\nimport os\nfrom pathlib import Path\n"
                    "def pytest_unconfigure(config):\n"
                    "    report = Path(os.environ['DV_PYTEST_REPORT'])\n"
                    "    record = json.loads(report.read_text())\n    " + change + "\n"
                )
                phase, log = self.report("pytest", source, extra=("-qq", "-s"), conftest=conftest)
                self.assertEqual(phase["exit_code"], 0, log)
                self.assertFalse(green_execution(phase, log, "pytest")[0], log)
                self.assertIsNone(phase["runner_report_sha256"])
                path = log.split(b"PROBE-PATH ")[1].splitlines()[0].decode()
                self.assertFalse(Path(path).parent.exists())

    def test_malformed_count_record_fails_closed(self):
        log = (
            b'DV-RUNNER-PHASES {"runner": "unittest", '
            b'"tests": ["executed", "passed", "failed", "skipped"]}\n'
        )
        counts, complete = receipt(log, "unittest")
        self.assertFalse(complete)
        self.assertEqual(counts["passed"], 0)

    @unittest.skipUnless(shutil.which("node"), "Node is optional runner test tooling")
    def test_node_counts_bodies_not_empty_files_or_suites(self):
        for source, passed, skipped in (
            ("", 0, 0),
            ("const {describe} = require('node:test'); describe('empty', () => {});", 0, 0),
            ("const {test} = require('node:test'); test('value', () => {});", 1, 0),
            (
                "const {test} = require('node:test'); "
                "test(__filename, {skip: true}, () => {}); test('value', () => {});",
                1, 1,
            ),
            (
                "const {describe, it} = require('node:test'); "
                "describe(__filename, () => { it('value', () => {}); });",
                1, 0,
            ),
            ("const {test} = require('node:test'); test(__filename, () => {});", 0, 0),
        ):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                empty = Path(directory, "empty file.test.cjs")
                empty.write_text("")
                test = Path(directory, "value.test.cjs")
                test.write_text(source)
                proc = subprocess.run(
                    ["node", "--test", "--test-reporter=tap", str(empty), str(test)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=15,
                )
                self.assertEqual(proc.returncode, 0, proc.stdout)
                counts, complete = receipt(proc.stdout, "node-test")
                self.assertTrue(complete, proc.stdout)
                self.assertEqual(counts, {
                    "executed": passed, "passed": passed, "failed": 0, "skipped": skipped,
                }, proc.stdout)
                phase = {"status": "normal", "exit_code": proc.returncode}
                self.assertEqual(green_execution(phase, proc.stdout, "node-test")[0], passed > 0)

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
