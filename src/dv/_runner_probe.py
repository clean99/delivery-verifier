"""Phase evidence for the real Python test runners; no third-party runtime imports."""

from __future__ import annotations

import json
import os
import runpy
import sys
import unittest

PREFIX = "DV-RUNNER-PHASES "


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
    body = {cls._callTestMethod.__code__ for cls in classes}
    lifecycle = {
        getattr(cls, method).__code__
        for cls in classes
        for method in ("_callSetUp", "_callTearDown", "_callCleanup")
    }
    relevant = [frame.f_code for frame in frames if frame.f_locals.get("self") is test]
    return bool(body.intersection(relevant)) and not lifecycle.intersection(relevant)


def emit(runner: str, failures: list[bool]) -> None:
    print("\n" + PREFIX + json.dumps({"runner": runner, "body_assertions": failures}), flush=True)


def run_unittest() -> None:
    failures = []
    original_failure = unittest.TestResult.addFailure
    original_subtest = unittest.TestResult.addSubTest

    def add_failure(result, test, error):
        failures.append(issubclass(error[0], AssertionError) and unittest_body(error, test))
        return original_failure(result, test, error)

    def add_subtest(result, test, subtest, error):
        if error is not None:
            failures.append(
                issubclass(error[0], AssertionError) and unittest_body(error, test, subtest=True)
            )
        return original_subtest(result, test, subtest, error)

    unittest.TestResult.addFailure = add_failure
    unittest.TestResult.addSubTest = add_subtest
    try:
        runpy.run_module("unittest", run_name="__main__", alter_sys=True)
    finally:
        emit("unittest", failures)


_pytest_failures: list[bool] = []
_pytest_calls: dict[tuple[str, str], bool] = {}


def pytest_sessionstart(session):
    _pytest_failures.clear()
    _pytest_calls.clear()


def pytest_runtest_makereport(item, call):
    error = call.excinfo
    valid = call.when == "call" and error is not None and isinstance(error.value, AssertionError)
    if valid and isinstance(getattr(item, "instance", None), unittest.TestCase):
        valid = unittest_body((error.type, error.value, error.value.__traceback__), item.instance)
    _pytest_calls[item.nodeid, call.when] = bool(valid)


def pytest_runtest_logreport(report):
    valid = _pytest_calls.pop((report.nodeid, report.when), False)
    if report.failed:
        _pytest_failures.append(valid)


def pytest_sessionfinish(session, exitstatus):
    emit("pytest", _pytest_failures)


if __name__ == "__main__":
    sys.argv = ["unittest", *sys.argv[1:]]
    if not sys.flags.isolated and not getattr(sys.flags, "safe_path", False):
        sys.path[0] = os.getcwd()
    run_unittest()
