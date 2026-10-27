from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from support import ROOT, Sandbox, git

from dv.runners import instrument
from dv.vitest import PREFIX

VITEST4 = os.environ.get("DV_VITEST4")
VERSION_ERROR = "Unsupported Vitest version 4.1.12; supported: 3.2.x"
IMPORT = "import {value} from '../src/value.js';\n"
BODY = "test('value', () => expect(value).toBe(2));"
SNAPSHOT = "tests/__snapshots__/value.test.js.snap"


@unittest.skipUnless(VITEST4 and shutil.which(VITEST4),
                     "Vitest 4 is optional runner tooling; set DV_VITEST4 to its executable")
class RealVitest4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        proc = subprocess.run(["node", "--input-type=module", "-e",
            "import {createRequire} from 'node:module';"
            "import {realpathSync} from 'node:fs';"
            "const r=createRequire(realpathSync(process.argv[1]));"
            "console.log(JSON.stringify({vitest:r('./package.json').version,"
            "vite:r('vite/package.json').version}));", str(Path(VITEST4).absolute())],
            capture_output=True, text=True, check=True, timeout=10)
        versions = json.loads(proc.stdout)
        if versions != {"vitest": "4.1.12", "vite": "7.3.7"}:
            raise unittest.SkipTest("Vitest 4 counterexamples require vitest@4.1.12 + vite@7.3.7")

    def command(self, *args):
        return [str(Path(VITEST4).absolute()), "run", "--globals", "--maxWorkers=1", *args]

    def refuse(self, source, files=None):
        files = files or {}
        box = Sandbox(old={"src/value.js": "export const value = 1;", **files}, new={
            "src/value.js": "export const value = 2;", "tests/value.test.js": IMPORT + source,
        })
        self.addCleanup(box.close)
        proc = subprocess.run(
            [sys.executable, "-m", "dv", "prove", "--test", shlex.join(self.command()),
             "--timeout", "30", "--json"], cwd=box.root,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            capture_output=True, text=True, timeout=90,
        )
        output = json.loads(proc.stdout)
        self.assertEqual(proc.returncode, 1, output)
        self.assertFalse(output["ok"], output)
        self.assertEqual(output["state"], "complete", output)
        for phase in ("red", "green"):
            # Both processes start; the reporter refuses before collecting user tests.
            log = (Path(output["evidence_dir"]) / f"{phase}.log").read_text()
            records = [json.loads(line[len(PREFIX):]) for line in log.splitlines()
                       if line.startswith(PREFIX)]
            self.assertEqual(len(records), 1, log)
            self.assertEqual(records[0]["version"], "4.1.12")
            self.assertFalse(records[0]["complete"], records[0])
            self.assertEqual(records[0].get("files", []), [])
            self.assertEqual(output[phase]["tests"]["executed"], 0, output)
            self.assertFalse(output[phase]["report_complete"], output)
            check = next(row for row in output["checks"]
                         if row["check"] == f"{phase} snapshot inputs")
            self.assertEqual(check["outcome"], "fail", check)
            self.assertIn(VERSION_ERROR, check["detail"])
        self.assertEqual(git(box.root, "status", "--porcelain"), "")
        return box

    def stock_run(self, box, *args):
        env = {**os.environ, "CI": "true"}
        env.pop("UPDATE_SNAPSHOT", None)
        return subprocess.run(self.command(*args), cwd=box.root, env=env,
                              capture_output=True, text=True, timeout=30)

    def test_fixture_setup_assertion_cannot_be_a_red_body(self):
        """Opening 4.x used to PASS with fixtures:false although the body never ran."""
        self.refuse("const extended = test.extend({"
            "x: async ({}, use) => { expect(value).toBe(2); await use(2); }});"
            "extended('fixture-only regression', ({x}) => { expect(x).toBe(2); });")

    def test_snapshot_match_path_stays_closed(self):
        """SnapshotClient.match bypasses assert's read and live-error attribution."""
        self.refuse("test('value', () => expect(value).toMatchSnapshot());",
                    {SNAPSHOT: 'exports[`value 1`] = `2`;\n'})

    def test_vite_define_checkout_paths_stay_closed(self):
        """viteDefine must normalize quoted roots without discarding config identity."""
        self.refuse("test('value', () => { expect(__ROOT__).toContain('/');"
                    "expect(value).toBe(2); });", {
            "vitest.config.mjs": "import path from 'node:path';"
            "const root=path.resolve(__dirname); export default {"
            "resolve:{alias:[{find:new RegExp('^'+root),replacement:root}]},"
            "define:{__ROOT__:JSON.stringify(root)}};",
        })

    def test_update_false_is_truthy_but_proof_does_not_write(self):
        """The 3.x probe's update=false becomes update-all in 4.1.12."""
        box = self.refuse("test('value', () => expect(value).toMatchSnapshot());",
                          {SNAPSHOT: 'exports[`value 1`] = `1`;\n'})
        self.assertIn("--update=false", instrument(self.command(), {}))
        before = (box.root / SNAPSHOT).read_bytes()
        proc = self.stock_run(box, "--update=none")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual((box.root / SNAPSHOT).read_bytes(), before)
        proc = self.stock_run(box, "--update=false")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('exports[`value 1`] = `2`;', (box.root / SNAPSHOT).read_text())
        self.assertNotEqual((box.root / SNAPSHOT).read_bytes(), before)

    def test_removed_min_workers_needs_version_specific_validation(self):
        """4.x rejects minWorkers before loading any reporter, unlike the 3.x CLI."""
        box = self.refuse(BODY)
        self.assertIn("--minWorkers=1", instrument(self.command("--minWorkers=1"), {}))
        proc = self.stock_run(box, "--minWorkers=1")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('Unknown option `--minWorkers`', proc.stderr)

    def test_around_hooks_cannot_supply_red_body_errors(self):
        """aroundEach/aroundAll need explicit rejection, even if their hooks pass."""
        for hook in ("aroundEach", "aroundAll"):
            with self.subTest(hook=hook):
                self.refuse(f"{hook}(async (run, {{}}) => {{"
                            "expect(value).toBe(2); await run(); });"
                            "test('value', () => expect(2).toBe(2));")
