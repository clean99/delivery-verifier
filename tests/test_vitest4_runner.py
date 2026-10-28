from __future__ import annotations

import copy
import json
import os
import shlex
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_vitest_runner as regression
from support import ROOT, Sandbox, git

from dv.runners import instrument
from dv.vitest import PREFIX, harness_inputs, parse_report, test_outcomes, test_parity

VITEST4 = os.environ.get("DV_VITEST4")
IMPORT = "import {value} from '../src/value.js';\n"
BODY = "test('value', () => expect(value).toBe(2));"
SNAPSHOT = "tests/__snapshots__/value.test.js.snap"


class Vitest4ReceiptTests(unittest.TestCase):
    def test_other_major_versions_remain_rejected(self):
        record = regression.record_from(regression.captured("pass"))
        for version in ("4.0.0", "4.1.11", "4.1.13", "4.2.0", "4.1.12-beta.1", "5.0.0", "5.0.5"):
            with self.subTest(version=version):
                record["version"] = version
                _, complete, _, detail = parse_report(regression.report_text(record).decode())
                self.assertFalse(complete)
                self.assertIn(f"Unsupported Vitest version {version}; "
                              "supported: 3.2.x and 4.1.12", detail)

    def test_base_failures_must_pass_under_the_same_identity(self):
        red = regression.record_from(regression.captured("assertion"))
        green = regression.record_from(regression.captured("pass"))
        root = Path(red["files"][0]["filepath"]).parent
        green["files"][0]["filepath"] = red["files"][0]["filepath"]
        before = test_outcomes(regression.report_text(red), root)
        self.assertTrue(test_parity(before, test_outcomes(regression.report_text(green), root))[0])
        for state in ("skip", "todo", "fail"):
            with self.subTest(state=state):
                green["files"][0]["tasks"][0]["result"]["state"] = state
                self.assertFalse(test_parity(
                    before, test_outcomes(regression.report_text(green), root))[0])
        self.assertFalse(test_parity(before, {})[0])
        green["files"][0]["tasks"][0]["result"]["state"] = "pass"
        for field in ("project", "filepath"):
            changed = copy.deepcopy(green)
            changed["files"][0][field] += "-other"
            self.assertFalse(test_parity(
                before, test_outcomes(regression.report_text(changed), root))[0])

    def test_missing_consumed_entries_are_rejected(self):
        for case in ("snapshot-inline", "snapshot-external"):
            record = regression.record_from(regression.captured(case))
            record["files"][0]["tasks"][0]["snapshotEntries"][0]["sha256"] = None
            complete = parse_report(regression.report_text(record).decode())
            self.assertFalse(complete[1])
            self.assertIn("missing consumed snapshot entry", complete[3])

    def test_null_harness_report_has_a_startup_diagnostic(self):
        for record in (None, [], "report"):
            valid, _, detail = harness_inputs(regression.report_text(record), ROOT, None, {})
            self.assertFalse(valid)
            self.assertIn("startup", detail)
            self.assertIn("phase log", detail)
            self.assertNotIn("has no attribute", detail)

    def test_pre_execution_config_hashes_are_required(self):
        record = {"schema_version": 7, "version": "4.1.12", "harness": [{
            "kind": "root", "project": "", "root": ".", "inputs": [],
            "configHashes": {"env": "1" * 64}}]}
        for initial in (None, {"env": "2" * 64}, {"env": "1" * 64}):
            record["harness"][0]["initialConfigHashes"] = initial
            valid, _, _ = harness_inputs(regression.report_text(record), ROOT, None, {})
            self.assertEqual(valid, initial == {"env": "1" * 64})

    def test_cli_help_lists_both_supported_versions(self):
        from dv.cli import build_parser

        parser = build_parser()
        help_text = parser._subparsers._group_actions[0].choices["prove"].format_help()
        self.assertIn("Vitest 3.2.x / 4.1.12", " ".join(help_text.split()))

    def test_new_version_requires_source_and_shutdown_checkpoints(self):
        record = regression.record_from(regression.captured("pass"))
        record.update(version="4.1.12", sourceVerified=True, postCloseErrors=[], finalErrors=[])
        # An old wrapper receipt cannot become a 4.1.12 body receipt.
        self.assertFalse(parse_report(regression.report_text(record).decode())[1])
        for field, value in (("sourceVerified", False), ("postCloseErrors", None),
                             ("finalErrors", [{"name": "Error", "message": "teardown"}])):
            with self.subTest(field=field):
                changed = copy.deepcopy(record)
                changed[field] = value
                self.assertFalse(parse_report(regression.report_text(changed).decode())[1])

    def test_version_specific_arguments(self):
        command = ["vitest", "run"]
        self.assertIn("--update=none", instrument(command, {}, version="4.1.12"))
        self.assertIn("--update=false", instrument(command, {}, version="3.2.7"))
        for args in (["--minWorkers=1"], ["--minWorkers", "1"]):
            with self.subTest(args=args):
                with self.assertRaisesRegex(Exception, "4.1.12 does not support --minWorkers"):
                    instrument(command + args, {}, version="4.1.12")
                self.assertIn(args[0], instrument(command + args, {}, version="3.2.7"))


@unittest.skipUnless(VITEST4 and shutil.which(VITEST4),
                     "Vitest 4 is optional runner tooling; set DV_VITEST4 to its executable")
class RealVitest4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from dv.vitest4 import select

        if select([VITEST4], ROOT)["version"] != "4.1.12":
            raise unittest.SkipTest("Vitest 4 behavior tests require vitest@4.1.12")

    def command(self, *args):
        return [str(Path(VITEST4).absolute()), "run", "--globals", "--maxWorkers=1", *args]

    save_proof = regression.RealVitestTests.save_proof

    def prove(self, source=BODY, files=None, *, accepted=False, detail=None, args=()):
        files = files or {}
        box = Sandbox(old={"src/value.js": "export const value = 1;", **files}, new={
            "src/value.js": "export const value = 2;", "tests/value.test.js": IMPORT + source,
        })
        self.addCleanup(box.close)
        proc = subprocess.run(
            [sys.executable, "-m", "dv", "prove", "--test", shlex.join(self.command(*args)),
             "--timeout", "30", "--json"], cwd=box.root,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            capture_output=True, text=True, timeout=90,
        )
        output = json.loads(proc.stdout)
        self.save_proof(output)
        self.assertEqual(proc.returncode, 0 if accepted else 1, output)
        self.assertEqual(output["ok"], accepted, output)
        self.assertEqual(output["state"], "complete", output)
        if detail:
            self.assertIn(detail, str(output["checks"]))
        self.assertEqual(git(box.root, "status", "--porcelain"), "")
        records = {}
        for phase in ("red", "green"):
            log = (Path(output["evidence_dir"]) / f"{phase}.log").read_text()
            records[phase] = json.loads(next(line[len(PREFIX):] for line in log.splitlines()
                                             if line.startswith(PREFIX)))
            self.assertEqual(records[phase]["version"], "4.1.12", log)
        if accepted:
            for record in records.values():
                self.assertTrue(record["sourceVerified"])
                self.assertEqual(record["postCloseErrors"], [])
                self.assertEqual(record["finalErrors"], [])
        return box, output, records

    def test_head_cannot_skip_or_todo_a_base_failure(self):
        for source in (
            "test.skipIf(value===2)('value',()=>expect(value).toBe(2));",
            "(value===2?test.todo:test)('value',()=>expect(value).toBe(2));",
            "test('value',({skip})=>{if(value===2)skip();expect(value).toBe(2)});",
            "describe.skipIf(value===2)('suite',()=>{" + BODY + "});",
        ):
            with self.subTest(source=source):
                self.prove(source + "test('other',()=>expect(true).toBe(true));",
                           detail="Base failure must execute and pass on head")
        self.prove(BODY + "test.skip('unrelated',()=>{});", accepted=True)

    def test_configuration_cannot_change_then_converge(self):
        config = ("import fs from 'node:fs';"
            "const oracle=fs.readFileSync('src/value.js','utf8').includes('= 1')?'base':'head';"
            "export default {test:{env:{ORACLE:oracle}},plugins:[{name:'converge',"
            "configureVitest({vitest}){const report=vitest.report.bind(vitest);"
            "vitest.report=async(name,...args)=>{if(name==='onTestRunEnd')"
            "for(const project of vitest.projects)project.config.env.ORACLE='same';"
            "return report(name,...args)}}}]};")
        _, _, records = self.prove(
            "test('value',()=>expect(process.env.ORACLE).toBe('head'));",
            {"vitest.config.mjs": config}, detail="configuration changed during execution")
        red = records["red"]["harness"][0]
        green = records["green"]["harness"][0]
        self.assertNotEqual(red["initialConfigHashes"]["env"], green["initialConfigHashes"]["env"])
        self.assertEqual(red["configHashes"]["env"], green["configHashes"]["env"])

    def test_custom_resolver_closed_over_targets_are_rejected(self):
        for pool in ("forks", "threads"):
            for resolver in ("resolveId", "{resolveId}"):
                with self.subTest(pool=pool, resolver=resolver):
                    config = ("import fs from 'node:fs';import {resolve} from 'node:path';"
                        "const target=resolve('src',fs.readFileSync('src/value.js','utf8')"
                        ".includes('= 1')?'one.js':'two.js');const resolveId=()=>target;"
                        "export default {resolve:{alias:[{find:'oracle',replacement:'unused',"
                        "customResolver:" + resolver + "}]},test:{pool:'" + pool + "'}};")
                    _, output, _ = self.prove(
                        "import oracle from 'oracle';test('value',()=>expect(oracle).toBe(2));",
                        {"vitest.config.mjs": config, "src/one.js": "export default 1;",
                         "src/two.js": "export default 2;"})
                    for phase in ("red", "green"):
                        log = (Path(output["evidence_dir"]) / f"{phase}.log").read_text()
                        self.assertIn("cannot canonicalize effective configuration key "
                                      "vite.resolve", log)

    def test_direct_snapshot_cache_access_is_sticky_rejection(self):
        for pool in ("forks", "threads"):
            for field in ("_snapshotData", "_initialData"):
                for caught in (False, True):
                    with self.subTest(pool=pool, field=field, caught=caught):
                        access = ("expected=Number(expect.getState().snapshotState."
                                  + field + "[value===1?'one 1':'two 1']);")
                        if caught:
                            access = "try{" + access + "}catch{}"
                        _, _, records = self.prove(
                            "test('one',()=>expect(1).toMatchSnapshot());"
                            "test('two',()=>expect(2).toMatchSnapshot());"
                            "test('value',()=>{let expected=2;" + access
                            + "expect(2).toBe(expected);expect(value).toBe(2)});",
                            {"vitest.config.mjs": "export default {test:{pool:'" + pool + "'}};",
                             SNAPSHOT: 'exports[`one 1`] = `1`;\nexports[`two 1`] = `2`;\n'})
                        for record in records.values():
                            self.assertTrue(record["files"][0]["unsupportedSnapshots"])

    def test_direct_snapshot_state_access_is_sticky_rejection(self):
        for method in ("match", "probeExpectedSnapshot"):
            for caught in (False, True):
                with self.subTest(method=method, caught=caught):
                    access = ("const state=expect.getState().snapshotState;"
                        f"state.{method}({{testId:task.id,testName:'value',received:2}})" +
                        (".markAsChecked();" if method == "probeExpectedSnapshot" else ";"))
                    if caught:
                        access = "try{" + access + "}catch{}"
                    _, _, records = self.prove(
                        "test('value',({task})=>{" + access + "expect(value).toBe(2)});")
                    for record in records.values():
                        self.assertTrue(record["files"][0]["unsupportedSnapshots"])

    def test_environment_reads_are_bound_to_each_test(self):
        for concurrent in (False, True):
            for swapped in (False, True):
                with self.subTest(concurrent=concurrent, swapped=swapped):
                    choice = "(index+value)%2" if swapped else "index"
                    source = ("import {resolve} from 'node:path';"
                        "for(const index of [0,1])test" + (".concurrent" if concurrent else "") +
                        "('read '+index,async({expect})=>{"
                        "const env=expect.getState().snapshotState.environment;"
                        "await env.readSnapshotFile(resolve('golden/'+" + choice + "+'.txt'));"
                        "expect(value).toBe(2)});")
                    self.prove(source, {"golden/0.txt": "one", "golden/1.txt": "two"},
                        accepted=not swapped,
                        detail="Snapshot paths or raw hashes differ" if swapped else None)

    def test_swallowed_missing_snapshots_still_reject(self):
        for matcher in ("toMatchSnapshot()", "toMatchInlineSnapshot()",
                        "toMatchFileSnapshot('../golden/missing.txt')"):
            with self.subTest(matcher=matcher):
                self.prove("test('value',async()=>{try{await expect(value)." + matcher +
                    "}catch{}expect(value).toBe(2)});", detail="missing consumed snapshot")
                self.prove(BODY + "test('missing',async({skip})=>{try{await expect(1)." +
                    matcher + "}catch{}skip()});", detail="missing consumed snapshot")

    def test_fixture_setup_assertion_cannot_be_a_red_body(self):
        _, _, records = self.prove("const extended = test.extend({"
            "x: async ({}, use) => { expect(value).toBe(2); await use(2); }});"
            "extended('fixture-only regression', ({x}) => { expect(x).toBe(2); });",
            detail="incomplete test-body invocation")
        body = records["red"]["files"][0]["tasks"][0]["body"]
        self.assertEqual((body["entered"], body["finished"], body["errors"]), (0, 0, []))

    def test_fixture_body_controls(self):
        fixtures = {
            "none": BODY,
            "empty": "test.extend({})('value', () => expect(value).toBe(2));",
            "unused": "test.extend({x: () => {throw Error('unused');}})"
                      "('value', () => expect(value).toBe(2));",
            "test": "test.extend({x: async ({},use) => await use(value)})"
                    "('value', ({x}) => expect(x).toBe(2));",
            "auto": "test.extend({x: [async ({},use) => await use(2), {auto:true}]})"
                    "('value', () => expect(value).toBe(2));",
            "dependency": "test.extend({x: async ({},use) => await use(value),"
                          "y: async ({x},use) => await use(x)})"
                          "('value', ({y}) => expect(y).toBe(2));",
            "override": "test.extend({x:1}).extend({x: async ({x},use) => await use(x+value)})"
                        "('value', ({x}) => expect(x).toBe(3));",
        }
        for name, source in fixtures.items():
            with self.subTest(fixture=name):
                self.prove(source, accepted=True)

    def test_scoped_fixture_teardown_and_controls(self):
        for pool in ("forks", "threads"):
            for scope in ("test", "file", "worker"):
                for broken in (False, True):
                    with self.subTest(pool=pool, scope=scope, broken=broken):
                        teardown = ("await new Promise(resolve => setTimeout(resolve, 100));"
                                    "throw new Error('teardown must be rejected');"
                                    if broken else "")
                        _, _, records = self.prove("const extended = test.extend({x: "
                            "[async ({}, use) => {await use(value);" + teardown + "}, "
                            "{scope: '" + scope + "'}]});"
                            "extended('value', ({x}) => expect(x).toBe(2));", {
                                "vitest.config.mjs":
                                    f"export default {{test: {{pool: '{pool}'}}}};",
                            }, accepted=not broken)
                        if broken and scope == "worker":
                            self.assertTrue(records["green"]["postCloseErrors"])
                            self.assertTrue(records["green"]["finalErrors"])

    def test_snapshot_match_path_and_custom_messages(self):
        for matcher in ("toMatchSnapshot()", "toMatchInlineSnapshot('2')"):
            for soft in ("", ".soft"):
                with self.subTest(matcher=matcher, soft=soft):
                    files = ({SNAPSHOT: 'exports[`value 1`] = `2`;\n'}
                             if matcher == "toMatchSnapshot()" else {})
                    self.prove(f"test('value', () => expect{soft}(value, 'custom message')."
                               f"{matcher});",
                               files, accepted=True)

    def test_property_mismatch_consumes_the_same_entry(self):
        self.prove("test('value', () => expect({a:value}).toMatchSnapshot({a:2}));",
                   {SNAPSHOT: 'exports[`value 1`] = `\n{\n  "a": 2,\n}\n`;\n'}, accepted=True)

    def test_domain_snapshot_path_is_rejected(self):
        self.prove("import {Snapshots} from 'vitest';"
            "const domain = {name: 'number', capture: value => value,"
            "render: String, parseExpected: Number,"
            "match: (actual, expected) => ({pass: actual === expected})};"
            "expect.extend({toMatchNumberSnapshot(received) {"
            "return Snapshots.toMatchDomainSnapshot.call(this, domain, received); }});"
            "test('value', () => expect(value).toMatchNumberSnapshot());",
            {SNAPSHOT: 'exports[`value 1`] = `2`;\n'})

    def test_swallowed_domain_or_polling_refusals_still_reject(self):
        for poll in (False, True):
            with self.subTest(poll=poll):
                assertion = "expect.poll(()=>value)" if poll else "expect(value)"
                self.prove("import {Snapshots} from 'vitest';"
                    "const domain={name:'number',capture:x=>x,render:String,parseExpected:Number,"
                    "match:(a,b)=>({pass:a===b})};"
                    "expect.extend({toMatchNumberSnapshot(received){"
                    "return Snapshots.toMatchDomainSnapshot.call(this,domain,received)}});"
                    "test('value',async()=>{try{await " + assertion + ".toMatchNumberSnapshot()}"
                    "catch{}expect(value).toBe(2)});",
                    detail="domain and polling snapshots are unsupported")

    def test_snapshot_mismatch_cannot_label_an_unrelated_error(self):
        self.prove("test('value', () => {try {expect(value).toMatchInlineSnapshot('2')} catch {}"
                   "if(value===1) throw Error('unrelated');});", detail="non-assertion")
        self.prove("import {Snapshots} from 'vitest';"
                   "expect.extend({toMatchValue(received) {"
                   "return Snapshots.toMatchInlineSnapshot.call(this,received,'2');}});"
                   "test('value',()=>expect(value).toMatchValue());",
                   detail="unbound custom snapshot")

    def test_vite_define_checkout_paths_preserve_identity(self):
        self.prove("test('value', () => { expect(__ROOT__).toContain('/');"
                   "expect(value).toBe(2); });", {
            "vitest.config.mjs": "import path from 'node:path';"
            "const root=path.resolve(__dirname); export default {"
            "resolve:{alias:[{find:new RegExp('^'+root),replacement:root}]},"
            "define:{__ROOT__:JSON.stringify(root)}};",
        }, accepted=True)

    def test_read_only_snapshot_mode(self):
        box, _, _ = self.prove("test('value', () => expect(value).toMatchSnapshot());",
                              {SNAPSHOT: 'exports[`value 1`] = `1`;\n'})
        self.assertEqual((box.root / SNAPSHOT).read_text(), 'exports[`value 1`] = `1`;\n')

    def test_removed_min_workers_is_rejected_before_phases(self):
        for args in (["--minWorkers=1"], ["--minWorkers", "1"]):
            with self.subTest(args=args):
                box = Sandbox()
                self.addCleanup(box.close)
                proc = subprocess.run([sys.executable, "-m", "dv", "prove", "--test",
                    shlex.join(self.command(*args)), "--json"], cwd=box.root,
                    env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(proc.returncode, 2)
                self.assertIn("4.1.12 does not support --minWorkers", proc.stdout)
                self.assertFalse((box.root / ".git/dv/proofs").exists())

    def test_around_hooks_reject_even_when_passing_or_inherited(self):
        for hook in ("aroundEach", "aroundAll"):
            for check in ("", "expect(value).toBe(2);"):
                with self.subTest(hook=hook, check=check):
                    _, _, records = self.prove(
                        f"{hook}(async (run, {{}}) => {{{check}await run();}});"
                                              "describe('nested', () => {" + BODY + "});")
                    self.assertIn("aroundEach/aroundAll", str(records["green"]["unhandledErrors"]))
                    self.assertIsNone(records["green"]["files"][0]["result"])

    def test_error_filter_callbacks_are_rejected(self):
        self.prove(files={"vitest.config.mjs":
            "export default {test:{onUnhandledError: () => false}};"},
            detail="onUnhandledError callbacks")

    def test_body_async_boundaries(self):
        for source, accepted in (
            ("test('value',async()=>{await expect(Promise.resolve(value)).resolves.toBe(2);});",
             True),
            ("test('value',()=>{expect(Promise.resolve(value)).resolves.toBe(2);});", False),
            ("test('value',()=>{expect.assertions(2);expect(value).toBe(2);});", False),
            ("test('value',async()=>{if(value===1)await new Promise(()=>{});"
             "expect(value).toBe(2);},10);", False),
            ("test('value',({expect})=>{queueMicrotask(()=>expect.soft(value).toBe(2));});", False),
            ("test('value',async({expect})=>{await Promise.resolve();"
             "queueMicrotask(()=>expect.soft(value).toBe(2));});", False),
            ("test('value',({expect})=>{setTimeout(()=>expect.soft(value).toBe(2),5);});"
             "test('wait',async()=>{await new Promise(r=>setTimeout(r,30));});", False),
            ("test('value',({signal})=>{if(value===1)throw Error('cancelled');"
             "expect(value).toBe(2);});", False),
        ):
            with self.subTest(source=source):
                self.prove(source, accepted=accepted)


# Exercise the existing proof regressions with a separate, pinned executable.
# Methods that use removed workspace/CLI options have version-specific cases above.
REPLAY = [name for name in vars(regression.RealVitestTests) if name.startswith('test_')
          and name not in {'test_compatibility_matrix',
                           'test_verified_allowlist_with_local_root_and_dir',
                           'test_changed_configs_fail_harness_identity',
                           'test_setup_and_workspace_imports_are_harness_inputs'}]


@unittest.skipUnless(VITEST4 and shutil.which(VITEST4), "Set DV_VITEST4 for the 4.1.12 replay")
class Vitest4RegressionTests(unittest.TestCase):
    command = RealVitest4Tests.command
    save_proof = RealVitest4Tests.save_proof
    setUpClass = classmethod(RealVitest4Tests.setUpClass.__func__)

    def prove_case(self, *args, **kwargs):
        with patch.object(regression, 'VITEST', VITEST4):
            return regression.RealVitestTests.prove_case(self, *args, **kwargs)

    def test_startup_failure_reports_configuration_error_and_log(self):
        _, proc, output = self.prove_case("export const value = 1;", IMPORT + BODY,
            files={"vitest.config.mjs": "export default {test:{workspace:[]}};"})
        self.assertEqual(proc.returncode, 1, output)
        self.assertEqual(output["state"], "complete", output)
        for phase in ("red", "green"):
            diagnostic = output[phase]["report_error"]
            self.assertIn("workspace", diagnostic)
            self.assertIn(str(Path(output["evidence_dir"]) / f"{phase}.log"), diagnostic)
        self.assertNotIn("has no attribute", str(output["checks"]))

    def test_changed_project_configs_fail_harness_identity(self):
        for filename in ("vitest.config.mjs", "vite.config.mjs", "vitest.config.ts"):
            with self.subTest(filename=filename):
                config = ("export default {test:{projects:"
                          "[{test:{name:'one',env:{ORACLE:'head'}}}]}};")
                _, proc, output = self.prove_case("export const value = 1;", IMPORT + BODY,
                    files={filename: config},
                    baseline_files={filename: config.replace("'head'", "'base'")})
                self.assertEqual(proc.returncode, 1, output)
                self.assertIn(filename, str(output["checks"]))

    def test_setup_imports_in_environment_graphs_are_bound(self):
        config = "export default {test:{setupFiles:['./setup.js']}};"
        _, proc, output = self.prove_case("export const value = 1;", IMPORT + BODY,
            files={"vitest.config.mjs": config, "setup.js": "import './helper.js';",
                   "helper.js": "globalThis.setupValue=2;"},
            baseline_files={"vitest.config.mjs": config, "setup.js": "import './helper.js';",
                            "helper.js": "globalThis.setupValue=1;"}, dependencies=True)
        self.assertEqual(proc.returncode, 1, output)
        self.assertIn("helper.js", str(output["checks"]))


for _name in REPLAY:
    def replay(self, method=getattr(regression.RealVitestTests, _name)):
        with patch.object(regression, 'VITEST', VITEST4):
            method(self)
    setattr(Vitest4RegressionTests, _name, replay)
