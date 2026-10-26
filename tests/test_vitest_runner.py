from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import ROOT, Sandbox, git
from test_build_artifacts import build_distributions

from dv import __version__, evidence, prove, redgreen
from dv.checker import checker_digest
from dv.common import DVError, digest, write_json
from dv.execution import green_execution, receipt
from dv.runners import instrument, red_assertion, run_test, runner_for
from dv.vitest import PREFIX, harness_inputs, harness_parity, parse_report, snapshot_parity

FIXTURES = ROOT / "tests/fixtures/runners"
VITEST = os.environ.get("DV_VITEST") or shutil.which("vitest")
IMPORTS = (
    "import {test,expect,beforeAll,afterAll,beforeEach,afterEach,onTestFinished} from 'vitest';\n"
)
CASES = {
    "assertion": "test('value', () => expect(1).toBe(2));",
    "pass": "test('value', () => expect(2).toBe(2));",
    "import": "import 'nonexistent_regression_dependency'; test('value', () => expect(1).toBe(2));",
    "hook": "beforeEach(() => { throw new Error('setup failed'); }); "
            "test('value', () => expect(1).toBe(2));",
    "after-each": "afterEach(() => expect(1).toBe(2)); test('value', () => expect(2).toBe(2));",
    "before-all": "beforeAll(() => expect(1).toBe(2)); test('value', () => expect(1).toBe(2));",
    "after-all": "afterAll(() => expect(1).toBe(2)); test('value', () => expect(1).toBe(2));",
    "cleanup": "beforeEach(() => () => expect(1).toBe(2)); "
               "test('value', () => expect(2).toBe(2));",
    "callback": "test('value', () => { onTestFinished(() => expect(1).toBe(2)); "
                "expect(2).toBe(2); });",
    "soft-hook": "beforeEach(() => expect.soft(1).toBe(2)); "
                 "test('value', () => expect(2).toBe(2));",
    "with-hooks": "beforeEach(() => expect(2).toBe(2)); afterEach(() => expect(2).toBe(2)); "
                  "test('value', () => expect(1).toBe(2));",
    "soft": "test('value', () => { expect.soft(1).toBe(2); expect.soft(3).toBe(4); });",
    "skipped": "test.skip('value', () => expect(1).toBe(2));",
    "todo": "test.todo('value');",
    "zero": "export const value = 1;",
    "runtime": "test('value', () => { throw new Error('broken'); });",
    "mixed": "test('runtime', () => { throw new Error('broken'); }); "
             "test('assertion', () => expect(1).toBe(2));",
    "timeout": "test('value', async () => { await new Promise(() => {}); }, 10);",
    "unhandled": "test('value', async () => { Promise.reject(new Error('unhandled')); "
                 "await new Promise(resolve => setTimeout(resolve, 20)); expect(2).toBe(2); });",
    "expected-failure": "test.fails('value', () => expect(1).toBe(2));",
    "retry": "test('value', {retry: 1}, () => expect(1).toBe(2));",
    "chai": "import {assert} from 'vitest'; test('value', () => assert.equal(1, 2));",
    "collection": "test('value', () => { invalid syntax });",
    "suite": "import {describe} from 'vitest'; describe('suite', () => { expect(1).toBe(2); });",
    "fixtures": "const extended = test.extend({value: async ({}, use) => { "
                "expect(1).toBe(2); await use(2); }}); "
                "extended('value', ({value}) => expect(value).toBe(2));",
    "failed-callback": "test('value', ({onTestFailed}) => { "
                       "onTestFailed(() => expect(3).toBe(4)); expect(1).toBe(2); });",
}
CONFIG_FILES = {
    "coverage-disabled": {
        "vitest.config.mjs": "export default {test: {coverage: {enabled: true}}};",
    },
    "global-setup-error": {
        "setup.mjs": "export default () => { throw new Error('setup failed'); };",
    },
    "global-setup-ok": {"setup.mjs": "export default () => {};"},
    "global-teardown-error": {
        "setup.mjs": "export default () => () => { throw new Error('teardown failed'); };",
    },
}
for name in CONFIG_FILES:
    CASES[name] = CASES["pass" if name == "coverage-disabled" else "assertion"]
    if name.startswith("global-"):
        CONFIG_FILES[name]["vitest.config.mjs"] = (
            "export default {test: {globalSetup: ['./setup.mjs']}};"
        )
CASES.update({
    "root-global-teardown": CASES["assertion"],
    "late-rejection": CASES["assertion"],
    "late-close-error": CASES["assertion"],
    "late-exit-error": CASES["assertion"],
    "renamed-runtime": "test('value', () => { throw Object.assign("
                       "new TypeError('runtime configuration failed'), "
                       "{name: 'AssertionError'}); });",
    "plain-assertion": "test('value', () => { throw "
                       "{name: 'AssertionError', message: 'broken'}; });",
    "node-assertion": "import assert from 'node:assert'; test('value', () => assert.equal(1, 2));",
    "snapshot-inline": "test('value', () => expect(1).toMatchInlineSnapshot('2'));",
    "snapshot-external": "test('value', () => expect(1).toMatchSnapshot());",
    "snapshot-runtime": "expect.addSnapshotSerializer({test: () => true, serialize: () => { "
                        "throw Object.assign(new Error('Snapshot mismatched'), "
                        "{actual: 1, expected: 2}); }}); "
                        "test('value', () => expect(1).toMatchInlineSnapshot('2'));",
    "concurrent-soft": "for (const name of ['one', 'two']) test.concurrent(name, "
                       "async ({expect}) => { await Promise.resolve(); expect.soft(1).toBe(2); });",
    "filter-watch": "test('watch', () => expect(1).toBe(2)); test('dev', () => expect(1).toBe(2));",
    "filter-dev": "test('watch', () => expect(1).toBe(2)); test('dev', () => expect(1).toBe(2));",
})
CONFIG_FILES.update({
    "root-global-teardown": {
        "vitest.config.mjs": "export default {test: {globalSetup: ['./setup.mjs'], "
                             "projects: [{test: {name: 'one', include: ['value.test.js']}}]}};",
        "setup.mjs": "export default () => () => { throw new Error('root teardown failed'); };",
    },
    "late-rejection": {
        "vitest.config.mjs": "export default {plugins: [{name: 'late', closeBundle() { "
                             "setTimeout(() => Promise.reject(new Error('late rejection')), 0); "
                             "}}]};",
    },
    "late-close-error": {
        "vitest.config.mjs": "export default {plugins: [{name: 'late', closeBundle() { "
                             "throw new Error('close failed'); }}]};",
    },
    "late-exit-error": {
        "vitest.config.mjs": "export default {plugins: [{name: 'late', closeBundle() { "
                             "process.on('exit', () => { throw new Error('late exit'); }); }}]};",
    },
    "snapshot-external": {
        "__snapshots__/value.test.js.snap": "// Vitest Snapshot v1, https://vitest.dev/guide/snapshot.html\n"
                                            "\nexports[`value 1`] = `2`;\n",
    },
})
CASE_ARGS = {"filter-watch": ["-t", "watch"], "filter-dev": ["--testNamePattern", "dev"]}
ACCEPTED_RED = {"assertion", "with-hooks", "soft", "chai", "projects", "node-assertion",
                "snapshot-inline", "snapshot-external", "concurrent-soft", "filter-watch",
                "filter-dev"}
ACCEPTED_GREEN = {"pass", "coverage-disabled"}


def normalize_vitest_fixture(content: str) -> str:
    return re.sub(r"(?:[^\s\"'()=<>]*[/\\])?node_modules[/\\]dv-probe[/\\]"
                  r"(_vitest_(?:runner|reporter)\.mjs)",
                  r"<tmp>/node_modules/dv-probe/\1", content)


def report_text(record):
    return (PREFIX + json.dumps(record) + "\n").encode()


def captured(name):
    return (FIXTURES / f"vitest-{name}.log").read_bytes()


def record_from(content):
    return json.loads(next(line[len(PREFIX):] for line in content.decode().splitlines()
                           if line.startswith(PREFIX)))


class VitestParserTests(unittest.TestCase):
    def test_fixture_capture_normalizes_absolute_and_relative_probe_frames(self):
        for prefix in (str(Path(tempfile.gettempdir()) / "sample"), "../../sample",
                       "C:" + chr(92) + "Users" + chr(92) + "sample"):
            for filename in ("_vitest_runner.mjs", "_vitest_reporter.mjs"):
                frame = "at " + prefix + "/node_modules/dv-probe/" + filename + ":24:6"
                self.assertEqual(normalize_vitest_fixture(frame),
                                 "at <tmp>/node_modules/dv-probe/" + filename + ":24:6")

    def test_real_fixtures_classify_red_and_green(self):
        for name in (*CASES, "projects"):
            with self.subTest(case=name):
                content = captured(name)
                red = red_assertion({"status": "normal", "exit_code": 1}, content, "vitest")
                self.assertEqual(red[0], name in ACCEPTED_RED, red)
                green = green_execution({"status": "normal", "exit_code": 0}, content, "vitest")
                self.assertEqual(green[0], name in ACCEPTED_GREEN, green)
        self.assertEqual(receipt(captured("pass"), "vitest")[0],
                         {"executed": 1, "passed": 1, "failed": 0, "skipped": 0})

    def test_harness_identity_requires_complete_paths_and_raw_hashes(self):
        project = ("root", "", ".")
        one, two = "1" * 64, "2" * 64
        def harness(files):
            return {project: {"files": files, "config": {"env": one}}}

        self.assertTrue(harness_parity(harness({".env": None}), harness({".env": None}))[0])
        for red, green in (({"helper.js": one}, {"helper.js": two}),
                           ({"helper.js": one}, {}), ({"helper.js": None}, {})):
            valid, detail = harness_parity(harness(red), harness(green))
            self.assertFalse(valid)
            self.assertIn("helper.js", detail)
            self.assertIn("cannot attribute the red result", detail)
        for record in ({}, {"schema_version": 7, "harness": []},
                       {"schema_version": 7, "harness": [{"inputs": []}]}):
            self.assertFalse(harness_inputs(report_text(record), Path("/sample"), None, {})[0])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            record = {"schema_version": 7, "harness": [
                {"kind": "root", "project": "", "root": ".", "configHashes": {"env": one},
                 "inputs": [
                    {"path": ".env", "sha256": None, "external": False}]}]}
            self.assertTrue(harness_inputs(report_text(record), root, None, {})[0])
            for name, value in (("../env", None), ("/outside", None), (".env", "bad")):
                record["harness"][0]["inputs"] = [{"path": name, "sha256": value,
                                                    "external": False}]
                self.assertFalse(harness_inputs(report_text(record), root, None, {})[0])

    def test_external_harness_hashes_require_redacted_stable_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for prefix in ("<home>", "<absolute>"):
                name = prefix + "/" + "1" * 64 + "/offset.mjs"
                records = []
                for value in (None, "2" * 64):
                    record = {"schema_version": 7, "harness": [{
                        "kind": "root", "project": "", "root": ".",
                        "configHashes": {"env": "3" * 64},
                        "inputs": [{"path": name, "sha256": value, "external": True}]}]}
                    valid, inputs, detail = harness_inputs(report_text(record), root, None, {})
                    self.assertTrue(valid, detail)
                    records.append(inputs)
                valid, detail = harness_parity(*records)
                self.assertFalse(valid)
                self.assertIn(name, detail)
                for bad in ("/outside/offset.mjs", "<absolute>/bad/offset.mjs", "offset.mjs"):
                    record["harness"][0]["inputs"][0]["path"] = bad
                    self.assertFalse(harness_inputs(report_text(record), root, None, {})[0])

    def test_cleanup_and_callback_need_more_than_reporter_hook_states(self):
        for name in ("cleanup", "callback", "soft-hook"):
            with self.subTest(case=name):
                record = record_from(captured(name))
                test = record["files"][0]["tasks"][0]
                self.assertTrue(all(state == "pass" for state in test["result"]["hooks"].values()))
                self.assertEqual(test["result"]["errors"][0]["name"], "AssertionError")
                self.assertEqual(test["body"]["errors"], [])
                self.assertIn("outside the test body", parse_report(captured(name).decode())[3])

    def test_missing_duplicate_and_malformed_reports_fail_closed(self):
        content = captured("assertion")
        for log in (b"AssertionError: expected 1 to be 2", content + content,
                    (PREFIX + "null\n").encode(), (PREFIX + "{broken\n").encode()):
            self.assertFalse(red_assertion({"status": "normal", "exit_code": 1}, log, "vitest")[0])

    def test_missing_phase_evidence_and_inconsistent_results_fail_closed(self):
        original = record_from(captured("assertion"))
        for mutation in (
            lambda r: r.update(version="4.0.0"),
            lambda r: r.update(complete=False),
            lambda r: r.update(reason="interrupted"),
            lambda r: r.update(nonce="invalid"),
            lambda r: r["files"][0].update(probe=None),
            lambda r: r["files"][0]["tasks"][0].update(body=None),
            lambda r: r["files"][0]["tasks"][0]["body"].update(nonce="1" * 32),
            lambda r: r["files"][0]["result"].update(state="pass"),
            lambda r: r["files"][0]["tasks"].append(copy.deepcopy(r["files"][0]["tasks"][0])),
            lambda r: r["files"][0]["tasks"][0]["result"].update(state="run"),
            lambda r: r["files"][0]["tasks"][0]["result"].update(errors=[]),
            lambda r: r["files"][0]["tasks"][0]["result"].update(retryCount=True),
            lambda r: r["files"][0]["tasks"][0]["result"].update(hooks={"beforeEach": "run"}),
        ):
            record = copy.deepcopy(original)
            mutation(record)
            valid = red_assertion({"status": "normal", "exit_code": 1}, report_text(record),
                                  "vitest")
            self.assertFalse(valid[0], (record, valid))

    def test_exit_status_is_independent_of_report(self):
        for status, code in (("normal", 0), ("normal", 2), ("timeout", 1), ("signal", -15)):
            self.assertFalse(red_assertion({"status": status, "exit_code": code},
                                           captured("assertion"), "vitest")[0])
        for status, code in (("normal", 1), ("timeout", 0), ("signal", -15)):
            self.assertFalse(green_execution({"status": status, "exit_code": code},
                                             captured("pass"), "vitest")[0])

    def test_auto_detection_and_single_run_instrumentation(self):
        for command in (
            ["vitest", "run", "tests/value.test.js"], ["vitest"],
            ["npx", "vitest", "run"], ["pnpm", "vitest", "run"],
            ["pnpm", "exec", "vitest"], ["/tmp/tooling/vitest", "run"],
        ):
            with self.subTest(command=command):
                self.assertEqual(runner_for(command), "vitest")
                argv = instrument(command, {})
                self.assertIn("--run", argv)
                self.assertIn("--watch=false", argv)
                self.assertIn("--reporter=default", argv)
                self.assertTrue(argv[-1].endswith("_vitest_reporter.mjs"))
        for command in (["jest"], ["npm", "test"], ["echo", "vitest"], ["npx", "other", "vitest"]):
            self.assertEqual(runner_for(command), "unsupported")

    def test_watch_and_report_overrides_are_rejected_before_execution(self):
        for option in ("--watch", "--watch=true", "-w", "--run=false", "--update", "-u",
                       "--reporter=json", "--outputFile=other.json", "--outputFile.json=x",
                       "--diff=other.mjs", "--environment=node", "--pool=forks", "--workspace=x",
                       "--unknown", "--"):
            with self.subTest(option=option), self.assertRaises(DVError):
                instrument(["vitest", "run", option], {})
        with self.assertRaises(DVError):
            instrument(["npm", "test"], {}, "vitest")

    def test_options_consume_keyword_values_and_only_first_positional_is_a_command(self):
        for option in ("-t", "--testNamePattern", "--project", "-c", "--config",
                       "--root", "-r", "--dir"):
            for value in ("watch", "dev"):
                command = ["vitest", option, value, "run"]
                self.assertEqual(instrument(command, {})[:len(command)], command)
        self.assertEqual(instrument(["vitest", "run", "watch"], {})[:3],
                         ["vitest", "run", "watch"])
        for command in (["vitest", "watch"], ["vitest", "-t", "dev", "watch"]):
            with self.assertRaises(DVError):
                instrument(command, {})

    def test_allowlist_requires_values_and_keeps_a_single_run_flag(self):
        for option in ("-t", "--project", "--maxWorkers", "--minWorkers", "-c", "--root", "--dir"):
            for args in ([option], [option + "="], [option, "--globals"]):
                with self.subTest(args=args), self.assertRaisesRegex(DVError, "requires a value"):
                    instrument(["vitest", "run", *args], {})
        self.assertEqual(instrument(["vitest", "--run"], {}).count("--run"), 1)

    def test_live_error_classification_and_shutdown_fields_are_required(self):
        original = record_from(captured("assertion"))
        for mutation in (
            lambda r: r.update(schema_version=2),
            lambda r: r["files"][0].pop("snapshots"),
            lambda r: r["files"][0].update(snapshots=[{"path": "relative", "sha256": None}]),
            lambda r: r.update(shutdownComplete=False),
            lambda r: r.update(lateErrors=[{"name": "Error", "message": "late"}]),
            lambda r: r.update(lateStderr=["late diagnostic"]),
            lambda r: r.update(exitCode=0),
            lambda r: r.update(exitCode=2),
            lambda r: r["files"][0]["tasks"][0]["body"]["errors"][0].pop("assertion"),
            lambda r: r["files"][0]["tasks"][0]["body"]["errors"][0].update(assertion=None),
        ):
            record = copy.deepcopy(original)
            mutation(record)
            self.assertFalse(red_assertion({"status": "normal", "exit_code": 1},
                                           report_text(record), "vitest")[0], record)

    def test_snapshot_entry_and_identity_fields_are_required(self):
        original = record_from(captured("snapshot-external"))
        for mutation in (
            lambda t: t.pop("snapshotIdentity"),
            lambda t: t["snapshotIdentity"].update(occurrence=0),
            lambda t: t["snapshotIdentity"].update(names=[]),
            lambda t: t.pop("snapshotEntries"),
            lambda t: t["snapshotEntries"][0].pop("key"),
            lambda t: t["snapshotEntries"][0].update(kind="other"),
            lambda t: t["snapshotEntries"][0].update(sha256="invalid"),
            lambda t: t["snapshotEntries"][0].update(path="/other/file.snap"),
        ):
            record = copy.deepcopy(original)
            mutation(record["files"][0]["tasks"][0])
            self.assertFalse(parse_report(report_text(record).decode())[1], record)

    def test_snapshot_parity_preserves_entry_order_and_expected_hashes(self):
        key = ("value.test.js", "", ("suite", "value"), 1)
        first = ("value.snap", "external", "suite > value 1", "1" * 64)
        second = ("value.snap", "external", "suite > value 2", "2" * 64)
        red = {key: (frozenset(), (first, second))}
        self.assertTrue(snapshot_parity(red, red)[0])
        for entries in ((second, first), (first,), (first, (*second[:3], "3" * 64))):
            valid, detail = snapshot_parity(red, {key: (frozenset(), entries)})
            self.assertFalse(valid)
            self.assertIn("test 'suite > value' occurrence 1", detail)
            self.assertIn("entry", detail)

    def test_javascript_probes_are_bound_to_checker_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            for name in ("__init__.py", "checker.py", "_vitest_reporter.mjs", "_vitest_runner.mjs"):
                (package / name).write_text("first")
            first = checker_digest(package)
            (package / "_vitest_runner.mjs").write_text("changed")
            self.assertNotEqual(first, checker_digest(package))


def dependency_state(root):
    state = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            value = os.readlink(path)
        elif path.is_file():
            value = digest(path.read_bytes())
        else:
            value = "directory"
        state[str(path.relative_to(root))] = value
    return state


@unittest.skipUnless(VITEST, "Vitest is optional runner tooling; set DV_VITEST to its executable")
class RealVitestTests(unittest.TestCase):
    def command(self, *args):
        return [str(Path(VITEST).absolute()), "run", "--globals", "--maxWorkers=1", *args]

    def save_proof(self, output):
        destination = os.environ.get("DV_VITEST_PROOF_RESULTS")
        if not destination or not output.get("evidence_dir"):
            return
        self.proof_index = getattr(self, "proof_index", 0) + 1
        target = Path(destination) / f"{self._testMethodName}-{self.proof_index}"
        target.mkdir(parents=True, exist_ok=True)
        for name in ("proof.json", "red.log", "green.log"):
            source = Path(output["evidence_dir"]) / name
            if source.exists():
                shutil.copy2(source, target / name)

    def prove_case(self, source, test_source, files=None, args=(),
                   fixed="export const value = 2;", baseline_files=None, dependencies=False):
        baseline = (baseline_files if baseline_files is not None else
                    {name: value for name, value in (files or {}).items()
                     if not name.endswith(".snap") and "__snapshots__" not in Path(name).parts})
        box = Sandbox(old={"src/value.js": source, **baseline}, new={
            "src/value.js": fixed, "tests/value.test.js": test_source, **(files or {}),
        })
        self.addCleanup(box.close)
        if dependencies:
            (box.root / "node_modules").symlink_to(Path(VITEST).resolve().parent.parent)
            with (box.root / ".git/info/exclude").open("a") as stream:
                stream.write("\nnode_modules/\n")
        proc = subprocess.run(
            [sys.executable, "-m", "dv", "prove", "--test", shlex.join(self.command(*args)),
             "--timeout", "30", "--json"],
            cwd=box.root, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            capture_output=True, text=True, timeout=90,
        )
        output = json.loads(proc.stdout)
        self.save_proof(output)
        return box, proc, output

    def test_root_global_setup_with_projects_fails_closed(self):
        files = dict(CONFIG_FILES["root-global-teardown"])
        files["vitest.config.mjs"] = files["vitest.config.mjs"].replace(
            "['value.test.js']", "['tests/value.test.js']"
        )
        _, proc, output = self.prove_case("export const value = 1;",
            "import {value} from '../src/value.js'; test('value', () => expect(value).toBe(2));",
            files)
        self.assertEqual(proc.returncode, 1, output)
        self.assertFalse(output["ok"], output)
        self.assertIn("globalSetup", str(output["checks"]))

    def test_late_failures_invalidate_full_cli_proof(self):
        for case in ("late-rejection", "late-close-error", "late-exit-error"):
            with self.subTest(case=case):
                _, proc, output = self.prove_case("export const value = 1;",
                    "import {value} from '../src/value.js'; "
                    "test('value', () => expect(value).toBe(2));", CONFIG_FILES[case])
                self.assertEqual(proc.returncode, 1, output)
                self.assertFalse(output["ok"], output)
                self.assertFalse(output["red"]["report_complete"], output)

    def test_production_runtime_errors_cannot_impersonate_assertions(self):
        for thrown in (
            "Object.assign(new TypeError('runtime configuration failed'), {name:'AssertionError'})",
            "{name:'AssertionError', message:'runtime configuration failed'}",
        ):
            with self.subTest(thrown=thrown):
                _, proc, output = self.prove_case(
                    "export function value() { throw " + thrown + "; }",
                    "import {value} from '../src/value.js'; "
                    "test('value', () => expect(value()).toBe(2));",
                    fixed="export function value() { return 2; }")
                self.assertEqual(proc.returncode, 1, output)
                self.assertFalse(output["ok"], output)
                self.assertEqual(output["green"]["tests"]["passed"], 1, output)
                self.assertIn("non-assertion", str(output["checks"]))

    def test_inline_and_new_external_snapshots_prove_red_green(self):
        for matcher in ("toMatchInlineSnapshot('2')", "toMatchSnapshot()"):
            with self.subTest(matcher=matcher):
                files = {}
                path = "tests/__snapshots__/value.test.js.snap"
                if matcher == "toMatchSnapshot()":
                    files[path] = CONFIG_FILES["snapshot-external"][
                        "__snapshots__/value.test.js.snap"
                    ]
                box, proc, output = self.prove_case("export const value = 1;",
                    "import {value} from '../src/value.js'; "
                    "test('value', () => expect(value)." + matcher + ");", files)
                self.assertEqual(proc.returncode, 0, output)
                self.assertTrue(output["ok"], output)
                self.assertEqual(output["red"]["exit_code"], 1)
                self.assertEqual(output["green"]["tests"]["passed"], 1)
                if files:
                    self.assertIn(path, output["retained_tests"])
                    self.assertEqual((box.root / path).read_text(), files[path])

    def test_nonstandard_snapshot_reads_must_match_head(self):
        path = "golden/value.expected"
        for kind in ("raw", "custom"):
            matcher = ("toMatchFileSnapshot('../golden/value.expected')" if kind == "raw"
                       else "toMatchSnapshot()")
            config = {} if kind == "raw" else {"vitest.config.mjs":
                "export default {test: {resolveSnapshotPath: path => "
                "path.replace('/tests/value.test.js', '/golden/value.expected')}};"}
            expected = "same" if kind == "raw" else 'exports[`value 1`] = `"same"`;\n'
            source = "export const value = 'same';"
            test = ("import {value} from '../src/value.js'; test('value', async () => "
                    "await expect(value)." + matcher + ");")
            for previous in (None, expected.replace("same", "old"), expected):
                with self.subTest(kind=kind, previous=previous):
                    baseline = {**config, **({path: previous} if previous is not None else {})}
                    old = source if previous != expected else source.replace("same", "old")
                    _, proc, output = self.prove_case(old, test, {**config, path: expected},
                                                     fixed=source, baseline_files=baseline)
                    accepted = previous == expected
                    self.assertEqual(proc.returncode, 0 if accepted else 1, output)
                    self.assertEqual(output["ok"], accepted, output)
                    self.assertEqual(output["red"]["exit_code"], 1, output)
                    self.assertEqual(output["green"]["exit_code"], 0, output)
                    if not accepted:
                        self.assertIn("Snapshot read differs from head: " + path,
                                      str(output["checks"]))

    def test_missing_snapshots_fail_without_writing_even_with_update_environment(self):
        for matcher in ("toMatchInlineSnapshot()", "toMatchSnapshot()"):
            with self.subTest(matcher=matcher), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = "test('value', () => expect(2)." + matcher + ");"
                (root / "value.test.js").write_text(source)
                (root / "vitest.config.mjs").write_text("export default {test: {update: true}};")
                phase = run_test(self.command(), root, root / "run.log", 30,
                                 {**os.environ, "UPDATE_SNAPSHOT": "1"})
                self.assertEqual(phase["exit_code"], 1)
                self.assertFalse(green_execution(
                    phase, (root / "run.log").read_bytes(), "vitest"
                )[0])
                self.assertEqual((root / "value.test.js").read_text(), source)
                self.assertFalse((root / "__snapshots__").exists())

    def test_keyword_test_filters_work_in_full_cli_proof(self):
        for option, name in (("-t", "watch"), ("--testNamePattern", "dev")):
            with self.subTest(name=name):
                _, proc, output = self.prove_case("export const value = 1;",
                    "import {value} from '../src/value.js'; "
                    "test('watch', () => expect(value).toBe(2)); "
                    "test('dev', () => expect(value).toBe(2));", args=(option, name))
                self.assertEqual(proc.returncode, 0, output)
                self.assertTrue(output["ok"], output)
                self.assertEqual(output["red"]["tests"]["executed"], 1)
                self.assertEqual(output["green"]["tests"]["passed"], 1)
                self.assertEqual(output["green"]["tests"]["skipped"], 1)

    def test_live_lifecycle_and_body_probe(self):
        for name in ("assertion", "pass", "after-each", "cleanup", "callback", "soft-hook",
                     "concurrent-soft", "snapshot-runtime"):
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = CASES[name]
                if name == "callback":
                    source = source.replace(
                        "() => { onTestFinished(", "({onTestFinished}) => { onTestFinished("
                    )
                (root / "value.test.js").write_text(source)
                phase = run_test(self.command(), root, root / "run.log", 30, dict(os.environ))
                log = (root / "run.log").read_bytes()
                self.assertEqual(red_assertion(phase, log, "vitest")[0], name in ACCEPTED_RED, log)
                self.assertEqual(green_execution(phase, log, "vitest")[0], name == "pass", log)

    def test_coverage_config_is_disabled_and_omitted_run_does_not_watch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "vitest.config.mjs").write_text(
                "export default {test: {coverage: {enabled: true, thresholds: {lines: 100}}}};"
            )
            (root / "value.test.js").write_text(CASES["pass"])
            command = self.command()
            command.remove("run")
            phase = run_test(command, root, root / "run.log", 30, dict(os.environ))
            content = (root / "run.log").read_bytes()
            self.assertTrue(green_execution(phase, content, "vitest")[0], content)
            self.assertIn("--coverage.enabled=false", phase["argv"])
            self.assertFalse((root / "coverage").exists())

    def test_custom_runner_and_pool_are_rejected_without_overriding(self):
        for setting in (
            "runner: './custom.mjs'", "pool: './custom.mjs'", "browser: {enabled: true}",
        ):
            with self.subTest(setting=setting), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "vitest.config.mjs").write_text("export default {test: {" + setting + "}};")
                (root / "value.test.js").write_text(CASES["assertion"])
                phase = run_test(self.command(), root, root / "run.log", 30, dict(os.environ))
                log = (root / "run.log").read_bytes()
                self.assertFalse(red_assertion(phase, log, "vitest")[0], log)
                self.assertNotEqual(phase["exit_code"], 0, log)

    def test_prove_real_git_red_green_and_import_rejection(self):
        for old_source, accepted in (("export const value = 1;", True),
                                     ("import 'nonexistent_regression_dependency';", False)):
            with self.subTest(accepted=accepted):
                box = Sandbox(old={"src/value.js": old_source}, new={
                    "src/value.js": "export const value = 2;",
                    "tests/value.test.js": "import {value} from '../src/value.js'; "
                                           "test('value', () => expect(value).toBe(2));",
                })
                self.addCleanup(box.close)
                proc = subprocess.run(
                    [sys.executable, "-m", "dv", "prove", "--test", shlex.join(self.command()),
                     "--timeout", "30", "--json"],
                    cwd=box.root, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                    capture_output=True, text=True, timeout=90,
                )
                output = json.loads(proc.stdout)
                self.assertEqual(proc.returncode, 0 if accepted else 1, output)
                self.assertEqual(output["ok"], accepted, output)
                self.assertEqual(output["runner"], "vitest")
                self.assertEqual(output["red"]["exit_code"], 1)
                self.assertEqual(output["green"]["tests"]["passed"], 1)
                directory = Path(output["evidence_dir"])
                for phase in ("red", "green"):
                    content = (directory / f"{phase}.log").read_bytes()
                    self.assertEqual(output[phase]["log_sha256"], digest(content))
                self.assertFalse((directory / "recovery.json").exists())
                self.assertFalse(any(directory.glob(".vitest-report-*")))
                if not accepted:
                    self.assertIn("file/suite", str(output["checks"]))

    def test_snapshot_oracle_switching_fails_per_test(self):
        for kind in ("resolver", "swapped-tests", "one-phase"):
            with self.subTest(kind=kind):
                config = "export default {test:{env:{FIRST:'a'}}};"
                files = {"golden/a.expected": "1", "golden/b.expected": "2"}
                source = fixed = "export const value = 2;"
                test = "import {value} from '../src/value.js'; "
                if kind == "resolver":
                    files = {name: 'exports[`value 1`] = `' + value + '`;\n'
                             for name, value in files.items()}
                    config = ("export default {test:{resolveSnapshotPath: () => "
                              "process.cwd()+'/golden/a.expected'}};")
                    test += "test('value',()=>expect(value).toMatchSnapshot());"
                    head_config = config.replace("a.expected", "b.expected")
                elif kind == "swapped-tests":
                    test += ("test('first',async()=>await expect(String(value))"
                             ".toMatchFileSnapshot("
                             "'../golden/'+process.env.FIRST+'.expected'));"
                             "test('second',async()=>await expect(String(value-1))"
                             ".toMatchFileSnapshot("
                             "'../golden/'+(process.env.FIRST==='a'?'b':'a')+'.expected'));")
                    head_config = config.replace("FIRST:'a'", "FIRST:'b'")
                else:
                    source = "export const value = 1;"
                    test += ("test('value',async()=>{if(value===2) await expect('2')"
                             ".toMatchFileSnapshot('../golden/b.expected'); "
                             "expect(value).toBe(2)});")
                    head_config = config
                _, proc, output = self.prove_case(source, test,
                    {**files, "vitest.config.mjs": head_config}, fixed=fixed,
                    baseline_files={**files, "vitest.config.mjs": config})
                self.assertEqual(proc.returncode, 1, output)
                if kind != "one-phase":
                    self.assertEqual(output["red"]["exit_code"], 1, output)
                    self.assertEqual(output["green"]["exit_code"], 0, output)
                    self.assertNotIn("vitest.config.mjs", output["retained_tests"])
                    checks = {check["check"]: check for check in output["checks"]}
                    self.assertEqual(checks["harness identity"]["outcome"], "fail")
                    self.assertIn("vitest.config.mjs", checks["harness identity"]["detail"])
                else:
                    self.assertEqual(output["red"]["exit_code"], 1, output)
                    self.assertEqual(output["green"]["exit_code"], 0, output)
                    checks = {check["check"]: check for check in output["checks"]}
                    self.assertEqual(checks["red snapshot inputs"]["outcome"], "pass")
                    self.assertEqual(checks["green snapshot inputs"]["outcome"], "pass")
                    self.assertEqual(checks["snapshot oracle parity"]["outcome"], "fail")

    def test_shuffled_same_name_snapshot_entries_cannot_manufacture_red(self):
        helper = "import {expect} from 'vitest'; export function check(value) {" \
                 "expect(value).toMatchSnapshot();}"
        snapshot = 'exports[`value 1`] = `2`;\nexports[`value 2`] = `1`;\n'
        for parameterized in (False, True):
            with self.subTest(test_each=parameterized):
                tests = ("test.each([value,1])('value', check);" if parameterized else
                         "test('value',()=>check(value)); test('value',()=>check(1));")
                files = {"helper.js": helper, "tests/__snapshots__/value.test.js.snap": snapshot,
                         "package.json": '{"name":"sample-project","type":"module"}'}
                config = "export default {test:{sequence:{shuffle:true,seed:3}}};"
                _, proc, output = self.prove_case("export const value = 2;",
                    "import {value} from '../src/value.js'; import {check} from '../helper.js'; "
                    + tests, {**files, "vitest.config.mjs": config.replace("seed:3", "seed:1")},
                    fixed="export const value = 2;",
                    baseline_files={**files, "vitest.config.mjs": config})
                self.assertEqual(proc.returncode, 1, output)
                self.assertEqual(output["green"]["exit_code"], 0, output)
                self.assertIn("duplicate full name", str(output["checks"]))
                self.assertIn("vitest.config.mjs", str(output["checks"]))

    def test_changed_configs_fail_harness_identity(self):
        for path, args in (
            ("vitest.config.mjs", ()), ("vite.config.mjs", ()),
            ("vitest.config.ts", ()), ("vitest.workspace.js", ()),
            ("config/proof.mjs", ("-c", "config/proof.mjs")),
            ("config/proof.mjs", ("--config=config/proof.mjs",)),
        ):
            with self.subTest(path=path, args=args):
                config = "{test:{globals:true,env:{ORACLE:'head'}}}"
                if "workspace" in path:
                    config = "[" + config + "]"
                config = "export default " + config + ";"
                _, proc, output = self.prove_case("export const value = 1;",
                    "import {value} from '../src/value.js'; test('value',()=>{"
                    "expect(value).toMatchSnapshot()});", {
                        path: config, "tests/__snapshots__/value.test.js.snap":
                        'exports[`value 1`] = `2`;\n',
                    }, args=args, baseline_files={path: config.replace("'head'", "'base'")})
                self.assertEqual(proc.returncode, 1, output)
                self.assertNotIn(path, output["retained_tests"])
                self.assertIn(path, str(output["checks"]))
                checks = {check["check"]: check for check in output["checks"]}
                self.assertEqual(checks["harness identity"]["outcome"], "fail")

    def test_head_config_that_cannot_load_on_base_fails_closed(self):
        _, proc, output = self.prove_case("export const value = 1;",
            "import {value} from '../src/value.js'; test('value',()=>expect(value).toBe(2));", {
                "vitest.config.mjs": "import config from './new-config.js'; export default config;",
                "new-config.js": "export default {};",
            }, baseline_files={"vitest.config.mjs": "export default {};"})
        self.assertEqual(proc.returncode, 1, output)
        self.assertNotIn("vitest.config.mjs", output["retained_tests"])
        self.assertIn("new-config.js", str(output["checks"]))
        self.assertIn("cannot attribute the red result", str(output["checks"]))

    def test_verified_allowlist_with_local_root_and_dir(self):
        prefix = "packages/sample/"
        config = "export default {test:{name:'selected'}};"
        box = Sandbox(old={prefix + "src/value.js": "export const value = 1;",
                           prefix + "vitest.config.mjs": config}, new={
            prefix + "src/value.js": "export const value = 2;",
            prefix + "tests/value.test.js": "import {value} from '../src/value.js'; "
            "test('value',()=>expect(value).toBe(2));",
        })
        self.addCleanup(box.close)
        root = box.root / prefix
        for args in (
            ["-r", prefix, "--dir", prefix + "tests"],
            ["--root=" + str(root), "--dir=" + str(root / "tests")],
            ["--root", str(root), "--dir", str(root / "tests")],
        ):
            with self.subTest(args=args):
                command = self.command(*args, "--project", "selected", "--minWorkers=1", "--run")
                proc = subprocess.run([sys.executable, "-m", "dv", "prove", "--test",
                    shlex.join(command), "--json", "--timeout", "30"], cwd=box.root,
                    env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                    capture_output=True, text=True, timeout=90)
                output = json.loads(proc.stdout)
                self.save_proof(output)
                self.assertEqual(proc.returncode, 0, output)
                self.assertEqual(git(box.root, "status", "--porcelain"), "")
                for phase in ("red", "green"):
                    self.assertFalse(any(str(box.root) in arg for arg in output[phase]["argv"]))

    def test_unsupported_options_never_execute_either_phase(self):
        for args in (("--diff", "diff.mjs"), ("--diff=diff.mjs",),
                     ("--pool", "forks"), ("--workspace", "projects.mjs")):
            with self.subTest(args=args):
                box, proc, output = self.prove_case("export const value = 1;",
                    "import {value} from '../src/value.js'; "
                    "test('value',()=>expect(value).toBe(2));", {
                        "diff.mjs": "import {writeFileSync} from 'node:fs'; "
                        "writeFileSync('caller-touched','bad'); export default {};",
                    }, args=args)
                self.assertEqual(proc.returncode, 2, output)
                self.assertIn(args[0].split("=")[0], str(output))
                self.assertFalse((box.root / "caller-touched").exists())
                self.assertFalse(list((box.root / ".git/dv/proofs").glob("*")))

    def test_collection_reordering_config_cannot_manufacture_red(self):
        files = {"src/value.ts": "broken\n",
                 "package.json": '{"name":"sample-project","type":"module"}',
                 "tests/__snapshots__/value.test.js.snap":
                 'exports[`value 1`] = `2`;\nexports[`value 2`] = `1`;\n'}
        config = "export default {test:{env:{REVERSE:'1'}}};"
        _, proc, output = self.prove_case("export const value = 2;",
            "import {value} from '../src/value.js'; const cases = "
            "process.env.REVERSE==='1'?[1,value]:[value,1]; "
            "test.each(cases)('value',v=>expect(v).toMatchSnapshot());",
            {**files, "vitest.config.mjs": config.replace("'1'", "'0'")},
            fixed="export const value = 2;",
            baseline_files={**files, "vitest.config.mjs": config})
        self.assertEqual(proc.returncode, 1, output)
        self.assertIn("vitest.config.mjs", str(output["checks"]))
        self.assertIn("cannot attribute the red result", str(output["checks"]))

    def test_changed_harness_dependencies_cannot_manufacture_red(self):
        cases = []
        for path in ("config/base.mjs", "vitest.setup-env.js"):
            cases.append((path, "export default {OFFSET:'1'};",
                "export default {OFFSET:'0'};", "import env from './" + path +
                "'; export default {test:{env}};", "process.env.OFFSET"))
        cases.append(("setup-env.js", "process.env.OFFSET='1';", "process.env.OFFSET='0';",
            "export default {test:{setupFiles:['./setup-env.js']}};", "process.env.OFFSET"))
        for filename in (".env", ".env.local", ".env.test", ".env.test.local"):
            cases.append((filename, "VITE_OFFSET=1\n", "VITE_OFFSET=0\n", None,
                          "import.meta.env.VITE_OFFSET"))
        cases.append(("environment/.env.test", "VITE_OFFSET=1\n", "VITE_OFFSET=0\n",
            "export default {envDir:'./environment'};", "import.meta.env.VITE_OFFSET"))
        for filename in (".env", ".env.test"):
            cases.append((filename, "OFFSET=1\n", "OFFSET=0\n",
                "import {loadEnv} from 'vite'; export default ({mode})=>"
                "({test:{env:loadEnv(mode,process.cwd(),'')}});", "process.env.OFFSET"))
        for path, before, after, config, offset in cases:
            with self.subTest(path=path, config=config):
                common = {"src/value.ts": "broken\n",
                          "package.json": '{"name":"sample-project","type":"module"}'}
                if config:
                    common["vitest.config.mjs"] = config
                _, proc, output = self.prove_case("export const value = 2;",
                    "import {value} from '../src/value.js'; test('value',()=>expect(value+Number("
                    + offset + ")).toMatchInlineSnapshot('2'));", {**common, path: after},
                    fixed="export const value = 2;", baseline_files={**common, path: before},
                    dependencies=bool(config and "loadEnv" in config))
                self.assertEqual(proc.returncode, 1, output)
                self.assertEqual(output["red"]["exit_code"], 1, output)
                self.assertEqual(output["green"]["exit_code"], 0, output)
                checks = {check["check"]: check for check in output["checks"]}
                self.assertEqual(checks["harness identity"]["outcome"], "fail")
                self.assertIn(path, checks["harness identity"]["detail"])
                self.assertIn("cannot attribute the red result", str(output["checks"]))

    def test_setup_and_workspace_imports_are_harness_inputs(self):
        for kind in ("setup", "workspace"):
            with self.subTest(kind=kind):
                config = ({"vitest.config.mjs":
                    "export default {test:{setupFiles:['./setup-env.js']}};",
                    "setup-env.js": "import {offset} from './config/helper.js'; "
                                    "process.env.OFFSET=offset;"} if kind == "setup" else {
                    "vitest.workspace.js": "import {offset} from './config/helper.js'; "
                    "export default [{test:{env:{OFFSET:String(offset)}}}];"})
                common = {"src/value.ts": "broken\n", **config,
                          "package.json": '{"name":"sample-project","type":"module"}'}
                _, proc, output = self.prove_case("export const value = 2;",
                    "import {value} from '../src/value.js'; test('value',()=>expect(value+Number("
                    "process.env.OFFSET)).toMatchInlineSnapshot('2'));",
                    {**common, "config/helper.js": "export const offset = 0;"},
                    fixed="export const value = 2;", baseline_files={
                        **common, "config/helper.js": "export const offset = 1;"})
                self.assertEqual(proc.returncode, 1, output)
                self.assertEqual(output["red"]["exit_code"], 1, output)
                self.assertEqual(output["green"]["exit_code"], 0, output)
                self.assertIn("config/helper.js", str(output["checks"]))

    def test_checkout_paths_in_regexp_aliases_and_stringified_defines(self):
        for expression in (
            "root", "new RegExp('^'+root)",
            r"new RegExp('^'+root.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'))",
            r"new RegExp('^'+root.replace(/[.*+?^${}()|[\]\\]/g,'\\$&').replaceAll('-','\\-'))",
        ):
            with self.subTest(expression=expression):
                config = "import path from 'node:path'; const root=path.resolve(__dirname,'src');" \
                    "export default {resolve:{alias:[{find:" + expression + \
                    ",replacement:root}]},define:{__ROOT__:JSON.stringify(path.resolve(__dirname))}};"
                _, proc, output = self.prove_case("export const value = 1;",
                    "import {value} from '../src/value.js'; test('value',()=>{"
                    "expect(__ROOT__).toContain('/'); expect(value).toBe(2);});",
                    {"vitest.config.mjs": config})
                self.assertEqual(proc.returncode, 0, output)
                self.assertTrue(output["ok"], output)

    def test_unreported_plugin_reads_are_part_of_the_change_under_test(self):
        config = "import {readFileSync} from 'node:fs'; " \
            "const offset=Number(readFileSync(new URL('./offset.dat',import.meta.url),'utf8'));" \
            "export default {plugins:[{name:'test-offset',transform(code,id){" \
            "if(id.endsWith('/value.test.js')) return code.replace('expect(value)'," \
            "'expect(value + '+offset+')');}}]};"
        common = {"src/value.ts": "broken\n", "vitest.config.mjs": config}
        box, proc, output = self.prove_case("export const value = 2;",
            "import {value} from '../src/value.js'; "
            "test('value',()=>expect(value).toMatchInlineSnapshot('2'));",
            {**common, "offset.dat": "0"}, fixed="export const value = 2;",
            baseline_files={**common, "offset.dat": "1"})
        self.assertEqual(proc.returncode, 0, output)
        self.assertTrue(output["ok"], output)
        self.assertEqual(git(box.root, "diff", "main", "HEAD", "--", "src/value.js"), "")
        for phase in ("red", "green"):
            record = record_from((Path(output["evidence_dir"]) / (phase + ".log")).read_bytes())
            self.assertFalse(any(file["path"] == "offset.dat" for project in record["harness"]
                                 for file in project["inputs"]))

    def test_external_harness_inputs_are_bound_across_phases(self):
        for case in ("unchanged", "changed", "env-added", "env-deleted"):
            with self.subTest(case=case), tempfile.TemporaryDirectory(prefix="dv-harness-") as tmp:
                external = Path(tmp).resolve()
                helper = external / "offset.mjs"
                helper.write_text("export const offset=" + ("1;" if case == "changed" else "0;"))
                env_file = external / ".env.test"
                if case == "env-deleted":
                    env_file.write_text("VITE_OFFSET=1\n")
                config = "import {offset} from " + json.dumps(str(helper)) + "; export default {" \
                    "envDir:" + json.dumps(str(external)) + ",plugins:[{name:'test-offset'," \
                    "transform(code,id){if(id.endsWith('/value.test.js')) " \
                    "return code.replace('expect(value)','expect(value + '+offset+')');}}]};"
                box = Sandbox(old={"src/value.js": "export const value = " +
                    ("1;" if case == "unchanged" else "2;"), "vitest.config.mjs": config}, new={
                        "src/value.js": "export const value = 2;", "tests/value.test.js":
                        "import {value} from '../src/value.js'; test('value',()=>expect(value)"
                        ".toMatchInlineSnapshot('2'));"})
                self.addCleanup(box.close)

                def phase_run(*args, **kwargs):
                    result = run_test(*args, **kwargs)
                    if args[2].name == "red.log":
                        if case == "changed":
                            helper.write_text("export const offset=0;")
                        elif case == "env-added":
                            env_file.write_text("VITE_OFFSET=0\n")
                        elif case == "env-deleted":
                            env_file.unlink()
                    return result

                with patch("dv.prove.Path.cwd", return_value=box.root), \
                        patch("dv.prove.run_test", side_effect=phase_run):
                    output = prove.run(shlex.join(self.command()), timeout=30)
                self.save_proof(output)
                self.assertEqual(output["ok"], case == "unchanged", output)
                if case in ("unchanged", "changed"):
                    self.assertEqual(output["red"]["exit_code"], 1, output)
                    self.assertEqual(output["green"]["exit_code"], 0, output)
                checks = {check["check"]: check for check in output["checks"]}
                self.assertEqual(checks["harness identity"]["outcome"],
                                 "pass" if case == "unchanged" else "fail")
                if case != "unchanged":
                    self.assertIn("offset.mjs" if case == "changed" else ".env.test",
                                  checks["harness identity"]["detail"])
                    self.assertNotIn(str(external), checks["harness identity"]["detail"])
                for phase in ("red", "green"):
                    record = record_from((Path(output["evidence_dir"]) /
                                          (phase + ".log")).read_bytes())
                    inputs = [file for project in record["harness"] for file in project["inputs"]]
                    helper_inputs = [file for file in inputs if
                                     file["path"].endswith("/offset.mjs")]
                    expected = "1;" if case == "changed" and phase == "red" else "0;"
                    self.assertTrue(helper_inputs)
                    self.assertTrue(all(file["external"] and file["sha256"] ==
                                        digest(("export const offset=" + expected).encode())
                                        for file in helper_inputs))
                    self.assertNotIn(str(external), json.dumps(inputs))
                    env_inputs = [file for file in inputs if file["path"].endswith("/.env.test")]
                    self.assertTrue(env_inputs)
                    absent = case in ("unchanged", "changed") or \
                        (case == "env-added" and phase == "red") or \
                        (case == "env-deleted" and phase == "green")
                    self.assertEqual(all(file["sha256"] is None for file in env_inputs), absent)

    def test_config_computed_values_cannot_manufacture_red(self):
        for key, before, after, assertion in (
            ("env", {"OFFSET": "1"}, {"OFFSET": "0"},
             "expect(value+Number(process.env.OFFSET)).toMatchInlineSnapshot('2')"),
            ("provide", {"expected": 3}, {"expected": 2}, "expect(value).toBe(inject('expected'))"),
            ("provide", {"expected": {}}, {"expected": []},
             "expect(inject('expected')).toEqual([])"),
            ("provide", {"api": {"port": 3}}, {"api": {"port": 2}},
             "expect(value).toBe(inject('api').port)"),
            ("provide", {"api": {"token": 3}}, {"api": {"token": 2}},
             "expect(value).toBe(inject('api').token)"),
        ):
            with self.subTest(key=key):
                def package(value):
                    return json.dumps({"name": "sample-project", "type": "module",
                                       "vitest": {key: value}})
                config = "import {readFileSync} from 'node:fs'; export default {test:JSON.parse(" \
                         "readFileSync(new URL('./package.json',import.meta.url),'utf8')).vitest};"
                common = {"src/value.ts": "broken\n", "vitest.config.mjs": config}
                _, proc, output = self.prove_case("export const value = 2;",
                    "import {inject} from 'vitest'; import {value} from '../src/value.js'; "
                    "test('value',()=>" + assertion + ");",
                    {**common, "package.json": package(after)}, fixed="export const value = 2;",
                    baseline_files={**common, "package.json": package(before)}, dependencies=True)
                self.assertEqual(proc.returncode, 1, output)
                self.assertEqual(output["red"]["exit_code"], 1, output)
                self.assertEqual(output["green"]["exit_code"], 0, output)
                self.assertIn("config key '" + key + "'", str(output["checks"]))
                for phase in ("red", "green"):
                    path = Path(output["evidence_dir"]) / (phase + ".log")
                    record = record_from(path.read_bytes())
                    self.assertNotIn("OFFSET", json.dumps(record["harness"]))

    def test_nondeterministic_configuration_fails_closed(self):
        for expression in ("Date.now()", "Math.random()"):
            with self.subTest(expression=expression):
                _, proc, output = self.prove_case("export const value = 1;",
                    "import {value} from '../src/value.js'; "
                    "test('value',()=>expect(value).toBe(2));",
                    {"vitest.config.mjs": "export default {test:{provide:{stamp:" + expression +
                                           "}}};"})
                self.assertEqual(proc.returncode, 1, output)
                self.assertIn("config key 'provide'", str(output["checks"]))

    def test_unchanged_harness_allows_a_real_product_fix(self):
        files = {"vitest.config.mjs": "import env from './config/base.mjs'; "
                 "export default {test:{env,setupFiles:['./setup-env.js']}};",
                 "config/base.mjs": "export default {OFFSET:'0'};",
                 "setup-env.js": "process.env.SETUP_OFFSET='0';", ".env.test": "VITE_OFFSET=0\n"}
        _, proc, output = self.prove_case("export const value = 1;",
            "import {value} from '../src/value.js'; test('value',()=>expect(value+"
            "Number(process.env.OFFSET)+Number(process.env.SETUP_OFFSET)+"
            "Number(import.meta.env.VITE_OFFSET)).toMatchInlineSnapshot('2'));", files)
        self.assertEqual(proc.returncode, 0, output)
        for phase in ("red", "green"):
            log = (Path(output["evidence_dir"]) / (phase + ".log")).read_bytes()
            inputs = record_from(log)["harness"][0]["inputs"]
            self.assertTrue(set(files) <= {item["path"] for item in inputs})
        self.assertTrue(output["ok"], output)

    def test_harness_input_additions_and_deletions_fail_closed(self):
        for before, after, fallback in ((None, "VITE_OFFSET=0\n", 1),
                                        ("VITE_OFFSET=1\n", None, 0)):
            with self.subTest(before=before):
                common = {"src/value.ts": "broken\n"}
                baseline = {**common, **({".env.test": before} if before else {})}
                _, proc, output = self.prove_case("export const value = 2;",
                    "import {value} from '../src/value.js'; test('value',()=>expect(value+Number("
                    "import.meta.env.VITE_OFFSET ?? " + str(fallback) +
                    ")).toMatchInlineSnapshot('2'));", {**common, ".env.test": after},
                    fixed="export const value = 2;", baseline_files=baseline)
                self.assertEqual(proc.returncode, 1, output)
                self.assertEqual(output["red"]["exit_code"], 1, output)
                self.assertEqual(output["green"]["exit_code"], 0, output)
                self.assertIn(".env.test", str(output["checks"]))

    def test_test_support_helper_changes_are_retained(self):
        for directory in ("tests", "__tests__", "test-utils", "test_utils",
                          "fixtures", "__mocks__"):
            for product_fix in (False, True):
                with self.subTest(directory=directory, product_fix=product_fix):
                    path = directory + "/helper.js"
                    common = {"src/value.ts": "broken\n"}
                    _, proc, output = self.prove_case(
                        "export const value = " + ("1;" if product_fix else "2;"),
                        "import {value} from '../src/value.js'; "
                        "import {expected} from '../" + path +
                        "'; test('value',()=>expect(value).toBe(expected));",
                        {**common, path: "export const expected = 2;"},
                        baseline_files={**common, path: "export const expected = 3;"})
                    self.assertEqual(proc.returncode, 0 if product_fix else 1, output)
                    if product_fix:
                        self.assertIn(path, output["retained_tests"])
                    else:
                        self.assertIsNone(output["red"])
                        self.assertIn("production difference", str(output["checks"]))

    def test_duplicate_snapshot_names_fail_closed(self):
        snapshot = "()=>expect(value).toMatchInlineSnapshot('2')"
        for tests in (
            "test.each([0,1])('value',()=>expect(value).toMatchSnapshot());",
            "test('value'," + snapshot + "); test('value',()=>expect(value).toBe(2));",
            "test('value'," + snapshot + "); test.skip('value',()=>{});",
            ("describe('suite',()=>test('value'," + snapshot + "));") * 2,
            "test('suite > value'," + snapshot + ");"
            "describe('suite',()=>test('value'," + snapshot + "));",
        ):
            with self.subTest(tests=tests):
                _, proc, output = self.prove_case("export const value = 1;",
                    "import {value} from '../src/value.js'; " + tests,
                    {"tests/__snapshots__/value.test.js.snap":
                     'exports[`value 1`] = `2`;\nexports[`value 2`] = `2`;\n'}
                    if "test.each" in tests else {})
                self.assertEqual(proc.returncode, 1, output)
                self.assertIn("duplicate full name", str(output["checks"]))
                self.assertIn("value %s", str(output["checks"]))

    def test_interpolated_snapshot_names_prove_real_fix(self):
        _, proc, output = self.prove_case("export const value = 1;",
            "import {value} from '../src/value.js'; test.each([0,1])('value %s', n => "
            "expect(value-n).toMatchSnapshot());", {
                "tests/__snapshots__/value.test.js.snap":
                'exports[`value 0 1`] = `2`;\nexports[`value 1 1`] = `1`;\n',
            })
        self.assertEqual(proc.returncode, 0, output)
        self.assertTrue(output["ok"], output)

    def test_raw_snapshot_hashes_use_file_bytes(self):
        _, proc, output = self.prove_case("export const value = 'old';",
            "import {value} from '../src/value.js'; test('value',async()=>"
            "await expect(value).toMatchFileSnapshot('../golden/value.expected'));",
            {"golden/value.expected": b"\xff"}, fixed="export const value = '\ufffd';")
        self.assertEqual(proc.returncode, 0, output)
        self.assertTrue(output["ok"], output)

    def test_obsolete_snapshot_writes_are_blocked(self):
        path = "tests/__snapshots__/value.test.js.snap"
        snapshot = 'exports[`obsolete 1`] = `1`;\nexports[`value 1`] = `2`;\n'
        box, proc, output = self.prove_case("export const value = 1;",
            "import {value} from '../src/value.js'; "
            "test('value',()=>expect(value).toMatchSnapshot());",
            {path: snapshot})
        self.assertNotEqual(proc.returncode, 0, output)
        self.assertFalse(output["ok"], output)
        self.assertEqual((box.root / path).read_text(), snapshot)
        log = (Path(output["evidence_dir"]) / "green.log").read_text()
        self.assertIn("snapshot writes are not allowed", log)
        self.assertFalse(any(check["check"].endswith("mutations") for check in output["checks"]))

    def test_local_launcher_paths_and_placeholders_use_isolated_dependencies(self):
        dependency = Path(VITEST).resolve().parent.parent
        for mode in ("relative", "absolute", "path"):
            with self.subTest(mode=mode):
                files = {"node_modules/.gitkeep": "", "vitest.config.mjs": "export default {};"}
                box = Sandbox(old={"src/value.js": "export const value = 1;", **files}, new={
                    "src/value.js": "export const value = 2;",
                    "tests/value.test.js": "import {test,expect} from 'vitest'; "
                    "import {value} from '../src/value.js'; "
                    "test('value',()=>expect(value).toBe(2));",
                })
                self.addCleanup(box.close)
                with (box.root / ".git/info/exclude").open("a") as stream:
                    stream.write("\nnode_modules/\n")
                shutil.copytree(dependency, box.root / "node_modules", dirs_exist_ok=True,
                                symlinks=True)
                launcher = box.root / "node_modules/.bin/vitest"
                launcher.unlink()
                launcher.write_text('#!/bin/sh\n'
                    'directory=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
                    'printf owned > "$directory/.launcher-cache"\n'
                    'exec node "$directory/vitest/vitest.mjs" "$@"\n')
                launcher.chmod(0o755)
                executable = {"relative": "./node_modules/.bin/vitest",
                              "absolute": str(launcher), "path": "vitest"}[mode]
                command = [executable, "run", "--maxWorkers=1", "--config=" +
                           str(box.root / "vitest.config.mjs"),
                           str(box.root / "tests/value.test.js")]
                before = dependency_state(box.root / "node_modules")
                env = {**os.environ, "PYTHONPATH": str(ROOT / "src"),
                       "PATH": str(launcher.parent) + os.pathsep + os.environ["PATH"]}
                proc = subprocess.run([sys.executable, "-m", "dv", "prove", "--test",
                                       shlex.join(command), "--json"], cwd=box.root,
                                      env=env, capture_output=True, text=True, timeout=90)
                output = json.loads(proc.stdout)
                self.save_proof(output)
                self.assertTrue(output["ok"], output)
                self.assertEqual(git(box.root, "status", "--porcelain"), "")
                self.assertEqual(dependency_state(box.root / "node_modules"), before)
                for phase in ("red", "green"):
                    self.assertFalse(any(str(box.root) in value for value in output[phase]["argv"]))

    def test_test_name_patterns_are_never_treated_as_paths(self):
        for kind in ("docs", "absolute", "joined", "descriptive", "relative"):
            with self.subTest(kind=kind):
                box = Sandbox(old={"src/value.js": "export const value = 1;",
                    "package.json": '{"name":"sample-project","type":"module"}'}, new={
                    "src/value.js": "export const value = 2;", "docs/note.md": "notes",
                })
                self.addCleanup(box.close)
                name = {"docs": "docs", "absolute": str(box.root / "src/value.js"),
                        "joined": str(box.root / "src/value.js"),
                        "descriptive": f"literal {box.root}/missing-description",
                        "relative": "src/../src/value.js"}[kind]
                box.write("tests/value.test.js", "import {test,expect} from 'vitest'; "
                    "import {value} from '../src/value.js'; test(" + json.dumps(name) +
                    ",()=>expect(value).toBe(2));")
                box.commit("Add the named regression")
                arguments = ["--testNamePattern=" + name] if kind == "joined" else ["-t", name]
                command = self.command(*arguments)
                proc = subprocess.run([sys.executable, "-m", "dv", "prove", "--test",
                                       shlex.join(command), "--json"], cwd=box.root,
                    env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                    capture_output=True, text=True, timeout=90)
                output = json.loads(proc.stdout)
                self.save_proof(output)
                self.assertEqual(proc.returncode, 0, output)
                self.assertTrue(output["ok"], output)
                for phase in ("red", "green"):
                    for argument in arguments:
                        self.assertIn(argument, output[phase]["argv"])

    def test_legacy_vitest_rejects_before_touching_caller_snapshots(self):
        snapshot = 'exports[`obsolete 1`] = `1`;\nexports[`value 1`] = `2`;\n'
        box = Sandbox(old={"src/value.js": "export const value = 1;"}, new={
            "src/value.js": "export const value = 2;",
            "tests/value.test.js": "test('value', () => expect(2).toMatchSnapshot());",
            "tests/__snapshots__/value.test.js.snap": snapshot,
        }, command=self.command())
        self.addCleanup(box.close)
        before = dependency_state(box.root)
        for runner in ("auto", "vitest"):
            box.update(test_runner=runner)
            with self.subTest(runner=runner), self.assertRaises(DVError) as failure:
                redgreen.run(box.item)
            self.assertEqual(failure.exception.code, "unsupported_runner")
            self.assertIn("dv prove", str(failure.exception))
            checks = evidence.redgreen_checks(box.item)
            self.assertEqual(checks[0]["outcome"], "fail")
            self.assertIn("dv prove", checks[0]["detail"])
            self.assertFalse(box.item.file("redgreen-red.log").exists())
            self.assertFalse(box.item.file("restoration.json").exists())
        proc = subprocess.run([sys.executable, "-m", "dv", "redgreen", str(box.path), "--json"],
                              cwd=box.root, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("dv prove", proc.stdout)
        after = dependency_state(box.root)
        self.assertEqual(
            {key: value for key, value in before.items() if not key.startswith(".dv/")},
            {key: value for key, value in after.items() if not key.startswith(".dv/")},
        )

    @unittest.skipUnless(importlib.util.find_spec("setuptools"), "installed CLI needs setuptools")
    def test_installed_cli_cleans_temp_root_after_success_failure_and_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel, _ = build_distributions(root)
            environment = root / "installed"
            subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
            python = environment / "bin/python"
            subprocess.run([str(python), "-m", "pip", "install", "--no-index", "--no-deps",
                            str(wheel)], check=True, capture_output=True, timeout=60)
            cli = str(environment / "bin/dv")
            env = {key: value for key, value in os.environ.items()
                   if key not in ("PYTHONPATH", "PYTHONHOME")}
            version = subprocess.run([cli, "--version"], env=env, capture_output=True,
                                     text=True, check=True)
            self.assertEqual(version.stdout.strip(), "dv " + __version__)
            for case, expected_exit in (("success", 0), ("failure", 1), ("timeout", 3)):
                with self.subTest(case=case):
                    temporary = root / case
                    temporary.mkdir()
                    before = dependency_state(temporary)
                    self.assertEqual(before, {})
                    test = ("import {test} from 'vitest'; test('pending', async () => "
                            "await new Promise(() => {}), 60000);" if case == "timeout" else
                            "import {test,expect} from 'vitest'; "
                            "import {value} from '../src/value.js'; test('value',()=>expect(value)"
                            ".toBe(" + ("2" if case == "success" else "3") + "));")
                    box = Sandbox(old={"src/value.js": "export const value = 1;"}, new={
                        "src/value.js": "export const value = 2;", "tests/value.test.js": test,
                        ".gitignore": "__pycache__/\nnode_modules\n"})
                    self.addCleanup(box.close)
                    (box.root / "node_modules").symlink_to(Path(VITEST).resolve().parent.parent)
                    self.assertEqual(git(box.root, "status", "--porcelain"), "")
                    proc = subprocess.run([cli, "prove", "--test", shlex.join(self.command()),
                        "--json", "--timeout", "2" if case == "timeout" else "30"],
                        cwd=box.root, env={**env, "TMPDIR": str(temporary),
                                          "TEMP": str(temporary), "TMP": str(temporary)},
                        capture_output=True, text=True, timeout=90)
                    output = json.loads(proc.stdout)
                    self.save_proof(output)
                    self.assertEqual(proc.returncode, expected_exit, output)
                    if case == "timeout":
                        self.assertEqual(output["red"]["status"], "timeout", output)
                        self.assertIsNone(output["green"])
                    self.assertTrue(temporary.is_dir())
                    self.assertEqual(dependency_state(temporary), before)
                    self.assertEqual(git(box.root, "status", "--porcelain"), "")

    @unittest.skipUnless(os.environ.get("DV_VITEST_MATRIX"), "Set DV_VITEST_MATRIX for Vite matrix")
    def test_compatibility_matrix(self):
        configurations = json.loads(Path(os.environ["DV_VITEST_MATRIX"]).read_text())
        results = []
        for configuration in configurations:
            versions = subprocess.run(["node", "--input-type=module", "-e",
                "import {createRequire} from 'node:module'; import {realpathSync} from 'node:fs';"
                "const r=createRequire(realpathSync(process.argv[1]+'/vitest/package.json'));"
                "console.log(JSON.stringify({vitest:r('./package.json').version,"
                "vite:r('vite/package.json').version}));", configuration["node_modules"]],
                capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(versions.stdout),
                             {"vitest": "3.2.4", "vite": configuration["vite"]})
            workspace = configuration.get("workspace", False)
            for package in ((True,) if workspace else (False, True)):
                with self.subTest(configuration=configuration, package_json=package):
                    files = {".gitignore": "__pycache__/\nnode_modules\n",
                             "vitest.config.mjs": "import {defineConfig} from 'vitest/config'; "
                             "export default defineConfig({server:{fs:{deny:"
                             "['**/.git/**','**/blocked/**']}},plugins:[{name:'fs-policy',"
                             "configResolved(c){if(!c.server.fs.strict || "
                             "!c.server.fs.deny.includes('**/.git/**') || "
                             "!c.server.fs.deny.includes('**/blocked/**')) "
                             "throw Error('filesystem policy changed')}}]});"}
                    if package:
                        files["package.json"] = json.dumps({"name": "sample-project",
                                                           "private": True, "type": "module"})
                    dependency = Path(configuration["node_modules"])
                    source_path, test_path = "src/value.js", "tests/value.test.js"
                    imported = "import {value} from '../src/value.js';"
                    if workspace:
                        for name in ("package.json", "pnpm-workspace.yaml",
                                     "packages/a/package.json", "packages/b/package.json"):
                            files[name] = (dependency.parent / name).read_text()
                        source_path, test_path = "packages/b/index.js", "packages/a/value.test.js"
                        imported = "import {value} from '@sample/value'; import isNumber "
                        imported += ("from 'is-number'; if(!isNumber(value)) "
                                     "throw Error('dependency');")
                    source = ("import {test,expect} from 'vitest'; "
                              "import {execFileSync} from 'node:child_process'; " + imported +
                              "test('value', () => {"
                              "expect(execFileSync('git',['-C',process.env.DV_TEST_CALLER_ROOT,"
                              "'status','--porcelain'],{encoding:'utf8'})).toBe(''); "
                              "if(value===2) expect(execFileSync('git',['--no-optional-locks',"
                              "'status','--porcelain'],{encoding:'utf8'})).toBe(''); "
                              "expect(value).toBe(2)});")
                    box = Sandbox(old={source_path: "export const value = 1;", **files}, new={
                        source_path: "export const value = 2;", test_path: source,
                    })
                    self.addCleanup(box.close)
                    if workspace:
                        shutil.copytree(dependency, box.root / "node_modules", symlinks=True)
                        shutil.copytree(dependency.parent / "packages/a/node_modules",
                                        box.root / "packages/a/node_modules", symlinks=True)
                    else:
                        (box.root / "node_modules").symlink_to(dependency, target_is_directory=True)
                    original_dependencies = dependency_state(dependency)
                    command = ["./node_modules/.bin/vitest", "run", "--maxWorkers=1"]
                    installed = configuration.get("installed_python") if package else None
                    env = {**os.environ, "DV_TEST_CALLER_ROOT": str(box.root)}
                    if installed:
                        env.pop("PYTHONPATH", None)
                    else:
                        env["PYTHONPATH"] = str(ROOT / "src")
                    proc = subprocess.run(
                        [installed or sys.executable, "-m", "dv", "prove", "--test",
                         shlex.join(command), "--json", "--timeout", "30"],
                        cwd=box.root, env=env, capture_output=True, text=True, timeout=90,
                    )
                    output = json.loads(proc.stdout)
                    record = {"vite": configuration["vite"], "layout": configuration["layout"],
                              "package_json": package, "installed_wheel": bool(installed),
                              "ok": output["ok"], "exit_code": proc.returncode}
                    destination = os.environ.get("DV_VITEST_MATRIX_RESULTS")
                    if destination:
                        target = Path(destination).parent / (
                            configuration["layout"] + "-" + configuration["vite"] +
                            ("-package" if package else "-no-package"))
                        target.mkdir(parents=True, exist_ok=True)
                        for name in ("proof.json", "red.log", "green.log"):
                            shutil.copy2(Path(output["evidence_dir"]) / name, target / name)
                        record["evidence"] = str(target)
                    results.append(record)
                    if destination:
                        Path(destination).write_text(json.dumps(results, indent=2) + "\n")
                    self.assertEqual(proc.returncode, 0, output)
                    self.assertTrue(output["ok"], output)
                    self.assertEqual(output["red"]["exit_code"], 1, output)
                    self.assertEqual(output["green"]["exit_code"], 0, output)
                    self.assertFalse(any(box.root.glob(".dv-vitest-*")))
                    self.assertEqual(dependency_state(dependency), original_dependencies)
                    for phase in ("red", "green"):
                        self.assertFalse(Path(output[phase]["vitest_reporter"]).parents[2].exists())

    def test_legacy_gate_rejects_saved_vitest_evidence(self):
        box = Sandbox(command=self.command())
        self.addCleanup(box.close)
        write_json(box.item.file("redgreen.json"), {"state": "complete", "runner": "vitest"})
        check = evidence.redgreen_checks(box.item)[0]
        self.assertEqual(check["outcome"], "fail")
        self.assertIn("dv prove", check["detail"])
