"""Phase evidence for the real Python test runners; no third-party runtime imports."""

from __future__ import annotations

import hashlib
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
_pytest_collection: list[str] = []
_pytest_errors = 0
# Pytest imports this explicit plugin before repository conftests. Do not expose
# the receipt channel through their inherited environment.
_pytest_report_fd = os.environ.pop("DV_PYTEST_REPORT_FD", None)


def pytest_sessionstart(session):
    global _pytest_duplicate, _pytest_errors
    _pytest_failures.clear()
    _pytest_calls.clear()
    _pytest_reports.clear()
    _pytest_collection.clear()
    _pytest_duplicate = False
    _pytest_errors = 0


def pytest_collectreport(report):
    _pytest_collection.append(report.outcome)


def pytest_internalerror(excrepr, excinfo):
    global _pytest_errors
    _pytest_errors += 1


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
    # Strict XPASS is a failed report without wasxfail in pytest 8.4.2.
    strict_xpass = (
        report.when == "call" and report.failed and isinstance(report.longrepr, str)
        and report.longrepr.startswith("[XPASS(strict)] ")
    )
    phases[report.when] = (report.outcome, hasattr(report, "wasxfail"), strict_xpass)


def pytest_sessionfinish(session, exitstatus):
    execution = empty_execution()
    counts = execution["tests"]
    counts.update(xpassed=0, xfailed=0, errors=_pytest_errors + _pytest_collection.count("failed"))
    counts["skipped"] = _pytest_collection.count("skipped")
    outcomes = {"passed", "failed", "skipped"}
    complete = (
        not _pytest_duplicate and int(exitstatus) in (0, 1)
        and all(outcome in outcomes for outcome in _pytest_collection)
    )
    if int(exitstatus) == 0:
        complete &= len(_pytest_reports) == session.testscollected
    for phases in _pytest_reports.values():
        complete &= {"setup", "teardown"} <= phases.keys() <= {"setup", "call", "teardown"}
        complete &= phases.get("setup") != ("passed", False, False) or "call" in phases
        values = list(phases.values())
        complete &= all(outcome in outcomes for outcome, _, _ in values)
        failed = any(outcome == "failed" for outcome, _, _ in values)
        skipped = any(outcome == "skipped" for outcome, _, _ in values)
        xpassed = any(strict or (outcome == "passed" and xfail)
                      for outcome, xfail, strict in values)
        counts["xpassed"] += int(xpassed)
        counts["xfailed"] += int(any(outcome == "skipped" and xfail
                                     for outcome, xfail, _ in values))
        counts["errors"] += sum(outcome == "failed" for when, (outcome, _, _) in phases.items()
                                if when != "call")
        passed = (
            not failed and not skipped and not xpassed
            and phases.get("call") == ("passed", False, False)
            and phases.get("teardown") == ("passed", False, False)
        )
        counts["failed"] += int(failed)
        counts["skipped"] += int(skipped and not failed)
        counts["passed"] += int(passed)
        counts["executed"] += int("call" in phases and not skipped)
    execution["complete"] = bool(complete)
    report = os.environ.get("DV_PYTEST_REPORT")
    if report:
        record = {
            "runner": "pytest", "body_assertions": _pytest_failures, **execution,
            "nonce": os.environ.get("DV_PYTEST_NONCE"), "exit_code": int(exitstatus),
        }
        encoded = json.dumps(record).encode()
        # Seal the bytes we produced, not a later read of the mutable report file.
        with open(report, "xb") as stream:
            stream.write(encoded)
        if _pytest_report_fd is not None:
            with os.fdopen(int(_pytest_report_fd), "wb") as stream:
                stream.write(hashlib.sha256(encoded).hexdigest().encode())


if __name__ == "__main__":
    sys.argv = ["unittest", *sys.argv[1:]]
    if not sys.flags.isolated and not getattr(sys.flags, "safe_path", False):
        sys.path[0] = os.getcwd()
    run_unittest()
