"""Phase evidence for the real Python test runners; no third-party runtime imports."""

from __future__ import annotations

import json
import os
import runpy
import sys
import unittest
from contextlib import contextmanager

PREFIX = "DV-RUNNER-PHASES "
_BODY_CODES = {
    cls._callTestMethod.__code__ for cls in (unittest.TestCase, unittest.IsolatedAsyncioTestCase)
}


def unittest_body(error: tuple, test: unittest.TestCase, *, subtest: bool = False) -> bool:
    frames = []
    traceback = error[2]
    while traceback is not None:
        frames.append(traceback.tb_frame)
        traceback = traceback.tb_next
    if subtest:
        frame = sys._getframe()
        while frame is not None:
            frames.append(frame)
            frame = frame.f_back
    classes = (unittest.TestCase, unittest.IsolatedAsyncioTestCase)
    body = _BODY_CODES
    lifecycle = {
        getattr(cls, method).__code__
        for cls in classes
        for method in ("_callSetUp", "_callTearDown", "_callCleanup")
    }
    relevant = [frame.f_code for frame in frames if frame.f_locals.get("self") is test]
    return bool(body.intersection(relevant)) and not lifecycle.intersection(relevant)


def emit(runner: str, failures: list[bool], execution: dict) -> None:
    record = {"runner": runner, "body_assertions": failures, **execution}
    print("\n" + PREFIX + json.dumps(record), flush=True)


def empty_execution() -> dict:
    return {
        "complete": False,
        "tests": {"executed": 0, "passed": 0, "failed": 0, "skipped": 0},
    }


def run_unittest() -> None:
    failures = []
    original_failure = unittest.TestResult.addFailure
    original_subtest = unittest.TestResult.addSubTest
    original_context = unittest.TestCase.subTest
    original_run = unittest.TextTestRunner.run
    original_success = unittest.TestResult.addSuccess
    bodies, successes = {}, set()
    execution = empty_execution()
    runs = 0

    def body_wrapper(method):
        def call(test, *args, **kwargs):
            bodies[id(test)] = test
            return method(test, *args, **kwargs)

        return call

    for cls in (unittest.TestCase, unittest.IsolatedAsyncioTestCase):
        # Keep the original frames on the stack for assertion-phase classification.
        cls._callTestMethod = body_wrapper(cls._callTestMethod)

    def add_success(result, test):
        successes.add(id(test))
        return original_success(result, test)

    def run(runner, test):
        nonlocal runs
        runs += 1
        result = original_run(runner, test)
        failed = {id(getattr(t, "test_case", t)) for t, _ in result.failures + result.errors}
        failed.update(id(t) for t in result.unexpectedSuccesses)
        skipped = len(result.skipped) + len(result.expectedFailures)
        execution.update(
            complete=runs == 1,
            tests={
                "executed": max(0, result.testsRun - skipped),
                "passed": len((successes & bodies.keys()) - failed),
                "failed": len(failed),
                "skipped": skipped,
            },
        )
        return result

    unittest.TestResult.addSuccess = add_success
    unittest.TextTestRunner.run = run

    @contextmanager
    def subtest_context(test, *args, **kwargs):
        with original_context(test, *args, **kwargs):
            try:
                yield
            except BaseException:
                if test._subtest is not None:
                    # Python 3.10 delivers results after the phase's call stack has unwound.
                    test._subtest._dv_test_body = unittest_body(sys.exc_info(), test, subtest=True)
                raise

    def add_failure(result, test, error):
        failures.append(issubclass(error[0], AssertionError) and unittest_body(error, test))
        return original_failure(result, test, error)

    def add_subtest(result, test, subtest, error):
        if error is not None:
            failures.append(
                issubclass(error[0], AssertionError) and getattr(subtest, "_dv_test_body", False)
            )
        return original_subtest(result, test, subtest, error)

    unittest.TestCase.subTest = subtest_context
    unittest.TestResult.addFailure = add_failure
    unittest.TestResult.addSubTest = add_subtest
    try:
        runpy.run_module("unittest", run_name="__main__", alter_sys=True)
    finally:
        emit("unittest", failures, execution)


_pytest_failures: list[bool] = []
_pytest_calls: dict[tuple[str, str], bool] = {}
_pytest_reports: dict[str, dict] = {}
_pytest_duplicate = False


def pytest_sessionstart(session):
    global _pytest_duplicate
    _pytest_failures.clear()
    _pytest_calls.clear()
    _pytest_reports.clear()
    _pytest_duplicate = False


def pytest_runtest_makereport(item, call):
    error = call.excinfo
    valid = call.when == "call" and error is not None and isinstance(error.value, AssertionError)
    if valid and isinstance(getattr(item, "instance", None), unittest.TestCase):
        valid = unittest_body((error.type, error.value, error.value.__traceback__), item.instance)
    _pytest_calls[item.nodeid, call.when] = bool(valid)


def pytest_runtest_logreport(report):
    global _pytest_duplicate
    valid = _pytest_calls.pop((report.nodeid, report.when), False)
    if report.failed:
        _pytest_failures.append(valid)
    phases = _pytest_reports.setdefault(report.nodeid, {})
    _pytest_duplicate |= report.when in phases
    phases[report.when] = (report.outcome, hasattr(report, "wasxfail"))


def pytest_sessionfinish(session, exitstatus):
    execution = empty_execution()
    counts = execution["tests"]
    complete = not _pytest_duplicate and int(exitstatus) in (0, 1)
    for phases in _pytest_reports.values():
        complete &= "setup" in phases and "teardown" in phases
        values = list(phases.values())
        failed = any(outcome == "failed" for outcome, _ in values)
        skipped = any(outcome == "skipped" or xfail for outcome, xfail in values)
        passed = (
            not failed
            and not skipped
            and phases.get("call") == ("passed", False)
            and phases.get("teardown") == ("passed", False)
        )
        counts["failed"] += int(failed)
        counts["skipped"] += int(skipped and not failed)
        counts["passed"] += int(passed)
        counts["executed"] += int("call" in phases and not skipped)
    execution["complete"] = bool(complete)
    emit("pytest", _pytest_failures, execution)


if __name__ == "__main__":
    sys.argv = ["unittest", *sys.argv[1:]]
    if not sys.flags.isolated and not getattr(sys.flags, "safe_path", False):
        sys.path[0] = os.getcwd()
    run_unittest()
