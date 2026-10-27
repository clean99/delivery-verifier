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
from dv.runners import red_assertion, run_test

HAS_PYTEST = importlib.util.find_spec("pytest") is not None


class RunnerPhaseTests(unittest.TestCase):
    def run_report(self, runner, source, *, probed=True, extra=(), conftest=None):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "test_value.py").write_text(textwrap.dedent(source))
            if conftest is not None:
                Path(directory, "conftest.py").write_text(textwrap.dedent(conftest))
            command = [sys.executable, "-m", runner]
            command += ["-q", "--tb=long", "-p", "no:cacheprovider"] if runner == "pytest" else []
            command += list(extra)
            env = {
                **os.environ,
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            if probed:
                log = Path(directory, "run.log")
                phase = run_test(command, Path(directory), log, 30, env)
                content = log.read_bytes()
            else:
                proc = subprocess.run(
                    command,
                    cwd=directory,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=30,
                )
                phase = {"status": "normal", "exit_code": proc.returncode}
                content = proc.stdout
        self.assertEqual(phase["exit_code"], 1, content.decode())
        return phase, content

    def assert_report(self, runner, source, expected, **kwargs):
        phase, content = self.run_report(runner, source, **kwargs)
        valid, detail = red_assertion(phase, content, runner)
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

    def unittest_subtest_cases(self, *, deferred=False):
        reporter = ""
        if deferred:
            reporter = textwrap.dedent("""
                import unittest
                class DeferredResult(unittest.TextTestResult):
                    def startTest(self, test):
                        self.pending = []
                        super().startTest(test)
                    def addSubTest(self, test, subtest, error):
                        self.pending.append((test, subtest, error))
                    def stopTest(self, test):
                        for args in self.pending:
                            super().addSubTest(*args)
                        super().stopTest(test)
                unittest.TextTestRunner.resultclass = DeferredResult
            """)
        for base, prefix in (("TestCase", ""), ("IsolatedAsyncioTestCase", "async ")):
            methods = ["test_value", "setUp", "tearDown", "cleanup", "runtime", "mixed"]
            if prefix:
                methods += ["asyncSetUp", "asyncTearDown", "async_cleanup"]
            for method in methods:
                with self.subTest(base=base, method=method, deferred=deferred):
                    body, hook = "pass", ""
                    if method in ("test_value", "runtime", "mixed"):
                        body = "self.check_value()"
                    elif method == "cleanup":
                        body = "self.addCleanup(self.check_value)"
                    elif method == "async_cleanup":
                        body = "self.addAsyncCleanup(self.async_check_value)"
                        hook = (
                            "    async def async_check_value(self):\n        self.check_value()\n"
                        )
                    if method in ("setUp", "tearDown", "asyncSetUp", "asyncTearDown", "mixed"):
                        name = "tearDown" if method == "mixed" else method
                        async_hook = "async " if name.startswith("async") else ""
                        hook = f"    {async_hook}def {name}(self):\n        self.check_value()\n"
                    assertion = (
                        "raise RuntimeError('not an assertion')"
                        if method == "runtime"
                        else "self.assertEqual(1, 2)"
                    )
                    source = textwrap.dedent(f"""
                        import unittest
                        class Example(unittest.{base}):
                            {prefix}def test_value(self):
                                {body}
                            def check_value(self):
                                for value in (1, 2):
                                    with self.subTest(value=value):
                                        with self.subTest('nested'):
                                            {assertion}
                    """) + hook
                    content = self.assert_report(
                        "unittest", reporter + source, method == "test_value"
                    )
                    record = json.loads(content.split(b"DV-RUNNER-PHASES ")[1])
                    expected = [method == "test_value"] * 2
                    if method == "mixed":
                        expected = [True, True, False, False]
                    self.assertEqual(record["body_assertions"], expected)

    def test_unittest_subtests_and_helper_assertions(self):
        self.unittest_subtest_cases()

    def test_unittest_deferred_subtests_python310(self):
        # Python 3.10 reports subtests after the body and lifecycle frames have returned.
        self.unittest_subtest_cases(deferred=True)

    def test_unittest_subtest_entered_in_body_but_failed_in_cleanup(self):
        self.assert_report(
            "unittest",
            """
            import sys
            import unittest
            class Example(unittest.TestCase):
                def test_value(self):
                    self.context = self.subTest(value=1)
                    self.context.__enter__()
                    self.addCleanup(self.check_value)
                def check_value(self):
                    try:
                        self.assertEqual(1, 2)
                    except AssertionError:
                        self.context.__exit__(*sys.exc_info())
            """,
            False,
        )

    def test_unittest_python310_subtest_report_format(self):
        content = textwrap.dedent('''\
            ======================================================================
            FAIL: test_value (test_value.Example) (value=1)
            ----------------------------------------------------------------------
            Traceback (most recent call last):
              File "test_value.py", line 10, in test_value
                self.check_value()
              File "test_value.py", line 7, in check_value
                self.assertEqual(1, 2)
            AssertionError: 1 != 2

            ----------------------------------------------------------------------
            Ran 1 test in 0.001s

            FAILED (failures=1)

            DV-RUNNER-PHASES {"runner": "unittest", "body_assertions": [true]}
        ''').encode()
        phase = {"status": "normal", "exit_code": 1}
        self.assertTrue(red_assertion(phase, content, "unittest")[0])
        for invalid in (
            content.replace(b"[true]", b"[false]"),
            content.replace(b"AssertionError", b"RuntimeError"),
            content.replace(b"in check_value", b"in setUp"),
            content.replace(b"failures=1", b"failures=1, errors=1"),
        ):
            self.assertFalse(red_assertion(phase, invalid, "unittest")[0])

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
    def test_pytest_dynamic_fixture_assertion_counts_as_test_body(self):
        content = self.assert_report(
            "pytest",
            """
            import pytest
            @pytest.fixture
            def value():
                assert 1 == 2, "dynamic fixture assertion"
            def test_value(request):
                request.getfixturevalue("value")
            """,
            True,
        )
        self.assertIn(b"AssertionError: dynamic fixture assertion", content)

    @unittest.skipUnless(HAS_PYTEST, "pytest is optional runner test tooling")
    def test_pytest_call_hook_assertion_counts_as_test_body(self):
        content = self.assert_report(
            "pytest",
            """
            def test_value():
                raise RuntimeError("test function must not run")
            """,
            True,
            conftest="""
            import pytest
            @pytest.hookimpl(tryfirst=True)
            def pytest_runtest_call(item):
                assert 1 == 2, "call hook assertion"
            """,
        )
        self.assertIn(b"AssertionError: call hook assertion", content)

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
