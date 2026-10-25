from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from support import Sandbox

from dv import evidence, redgreen
from dv.runners import instrument, red_assertion

HAS_PYTEST = importlib.util.find_spec("pytest") is not None


class RunnerPhaseTests(unittest.TestCase):
    def run_report(self, runner, source, *, probed=True, extra=()):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "test_value.py").write_text(textwrap.dedent(source))
            command = [sys.executable, "-m", runner]
            command += ["-q", "--tb=long", "-p", "no:cacheprovider"] if runner == "pytest" else []
            command += list(extra)
            env = {
                **os.environ,
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            if probed:
                command = instrument(command, env)
            proc = subprocess.run(
                command,
                cwd=directory,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=30,
            )
        self.assertEqual(proc.returncode, 1, proc.stdout.decode())
        return proc.stdout

    def assert_report(self, runner, source, expected, **kwargs):
        content = self.run_report(runner, source, **kwargs)
        valid, detail = red_assertion({"status": "normal", "exit_code": 1}, content, runner)
        self.assertEqual(valid, expected, detail + "\n" + content.decode())
        return content

    def unittest_cases(self, runner):
        for base, prefix in (("TestCase", ""), ("IsolatedAsyncioTestCase", "async ")):
            for phase in ("body", "setup", "teardown", "cleanup", "async_cleanup", "same_method"):
                with self.subTest(runner=runner, base=base, phase=phase):
                    if phase == "async_cleanup" and not prefix:
                        continue
                    source = "import unittest\nclass Example(unittest." + base + "):\n"
                    source += "    " + prefix + "def test_value(self):\n"
                    if phase == "body":
                        source += "        self.assertEqual(1, 2)\n"
                    elif phase in ("setup", "teardown"):
                        name = "setUp" if phase == "setup" else "tearDown"
                        source += "        pass\n    def " + name + "(self):\n"
                        source += "        self.assertEqual(1, 2)\n"
                    elif phase == "same_method":
                        source += (
                            (
                                "        if getattr(self, 'cleaning', False):\n"
                                "            self.assertEqual(1, 2)\n"
                                "        self.cleaning = True\n"
                                "        self.addCleanup(self.test_value)\n"
                            )
                            if not prefix
                            else (
                                "        if getattr(self, 'cleaning', False):\n"
                                "            self.assertEqual(1, 2)\n"
                                "        self.cleaning = True\n"
                                "        self.addAsyncCleanup(self.test_value)\n"
                            )
                        )
                    else:
                        method = "addAsyncCleanup" if phase == "async_cleanup" else "addCleanup"
                        callback = "async " if phase == "async_cleanup" else ""
                        source += "        self." + method + "(self.check_value)\n"
                        source += "    " + callback + "def check_value(self):\n"
                        source += "        self.assertEqual(1, 2)\n"
                    self.assert_report(runner, source, phase == "body")
        for method in ("asyncSetUp", "asyncTearDown"):
            self.assert_report(
                runner,
                f"""
                import unittest
                class Example(unittest.IsolatedAsyncioTestCase):
                    async def test_value(self):
                        pass
                    async def {method}(self):
                        self.assertEqual(1, 2)
            """,
                False,
            )

    def test_unittest_lifecycle_failures(self):
        self.unittest_cases("unittest")

    @unittest.skipUnless(HAS_PYTEST, "pytest is optional runner test tooling")
    def test_pytest_unittest_lifecycle_failures(self):
        self.unittest_cases("pytest")

    def test_unittest_subtests_and_helper_assertions(self):
        for method, expected in (("test_value", True), ("setUp", False), ("tearDown", False)):
            source = f"""
                import unittest
                class Example(unittest.TestCase):
                    def test_value(self):
                        pass
                    def check_value(self):
                        self.assertEqual(1, 2)
                    def {method}(self):
                        with self.subTest(value=1):
                            self.check_value()
            """
            self.assert_report("unittest", source, expected)

    @unittest.skipUnless(HAS_PYTEST, "pytest is optional runner test tooling")
    def test_pytest_fixture_phases_and_mixed_failures(self):
        for fixture, body in (
            ("assert 1 == 2\n    yield", "pass"),
            ("yield\n    assert 1 == 2", "pass"),
            ("yield\n    assert 1 == 2", "assert 1 == 2"),
            ("yield", "assert 1 == 2"),
        ):
            with self.subTest(fixture=fixture, body=body):
                source = (
                    "import pytest\n@pytest.fixture\ndef value():\n    "
                    + fixture
                    + "\ndef test_value(value):\n    "
                    + body
                    + "\n"
                )
                self.assert_report("pytest", source, fixture == "yield")
        self.assert_report(
            "pytest",
            """
            import pytest
            @pytest.mark.skip
            def test_skipped():
                pass
            @pytest.mark.xfail
            def test_expected_failure():
                assert 1 == 2
            def test_value():
                assert 1 == 2
        """,
            True,
        )

    @unittest.skipUnless(HAS_PYTEST, "pytest is optional runner test tooling")
    def test_pytest_hidden_tracebacks_still_fail_with_phase_evidence(self):
        for style in ("short", "line", "no"):
            self.assert_report(
                "pytest",
                "def test_value():\n    assert 1 == 2\n",
                False,
                extra=("--tb=" + style,),
            )

    def test_unprobed_and_incomplete_phase_evidence_is_rejected(self):
        source = """
            import unittest
            class Example(unittest.TestCase):
                def test_value(self):
                    self.assertEqual(1, 2)
        """
        for runner in ("unittest", "pytest") if HAS_PYTEST else ("unittest",):
            self.assert_report(runner, source, False, probed=False)
            content = self.assert_report(runner, source, True).decode()
            prefix, record = content.split("DV-RUNNER-PHASES ", 1)
            _, _, suffix = record.partition("\n")
            for values in ([], [True, True], [1], [False]):
                edited = (
                    prefix
                    + "DV-RUNNER-PHASES "
                    + json.dumps({"runner": runner, "body_assertions": values})
                    + "\n"
                    + suffix
                )
                self.assertFalse(
                    red_assertion({"status": "normal", "exit_code": 1}, edited.encode(), runner)[0]
                )

    @unittest.skipUnless(HAS_PYTEST, "pytest is optional runner test tooling")
    def test_pytest_body_proof_is_accepted_again_by_the_gate(self):
        box = Sandbox(
            command=[
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--tb=long",
                "-p",
                "no:cacheprovider",
            ]
        )
        self.addCleanup(box.close)
        output = redgreen.run(box.item)
        self.assertTrue(output["ok"], output)
        checks = evidence.redgreen_checks(box.item)
        self.assertEqual(checks[0]["outcome"], "pass", checks)

    def test_cleanup_cannot_prove_a_real_redgreen_change(self):
        for runner in ("unittest", "pytest") if HAS_PYTEST else ("unittest",):
            source = """
                from pathlib import Path
                import unittest
                class Example(unittest.TestCase):
                    def test_value(self):
                        self.addCleanup(self.check_value)
                    def check_value(self):
                        self.assertEqual(Path('src/value.ts').read_text().strip(), 'fixed')
            """
            command = [sys.executable, "-m", runner]
            command += (
                ["-q", "--tb=long", "-p", "no:cacheprovider"]
                if runner == "pytest"
                else ["discover", "-s", "tests"]
            )
            box = Sandbox(new={"tests/test_value.py": textwrap.dedent(source)}, command=command)
            self.addCleanup(box.close)
            output = redgreen.run(box.item)
            self.assertEqual(output["red"]["exit_code"], 1, output)
            self.assertEqual(output["green"]["exit_code"], 0, output)
            self.assertFalse(output["ok"], output)
            self.assertEqual(evidence.redgreen_checks(box.item)[0]["outcome"], "fail")
