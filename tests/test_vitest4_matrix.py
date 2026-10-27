"""Optional real npm/pnpm matrix cell; tooling is installed explicitly, never by tests."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import ROOT, Sandbox, git
from test_vitest_runner import dependency_state

MODULES = os.environ.get("DV_VITEST4_MATRIX_MODULES")
CLI = os.environ.get("DV_VITEST4_MATRIX_CLI")
IMPORT = "import {value} from '../src/value.js';"
BODY = "test('value',()=>expect(value).toBe(2));"


@unittest.skipUnless(MODULES and CLI, "Set matrix dependency tree and installed wheel CLI")
class Vitest4MatrixTests(unittest.TestCase):
    def test_real_cell(self):
        outcomes = []
        cases = {
            "body": (BODY, True),
            "snapshot": ("test('value',()=>expect(value).toMatchSnapshot());", True),
            "fixture-setup": ("test.extend({x:async({},use)=>{expect(value).toBe(2);await use(2)}})"
                              "('value',({x})=>expect(x).toBe(2));", False),
            "worker-clean": ("test.extend({x:[async({},use)=>{await use(value)},"
                             "{scope:'worker'}]})('value',({x})=>expect(x).toBe(2));", True),
            "worker-teardown": ("test.extend({x:[async({},use)=>{await use(value);"
                                "await new Promise(r=>setTimeout(r,30));throw Error('teardown')},"
                                "{scope:'worker'}]})('value',({x})=>expect(x).toBe(2));", False),
            "config-change": (BODY, False),
        }
        before = dependency_state(Path(MODULES))
        for pool in ("forks", "threads"):
            for name, (source, accepted) in cases.items():
                for execution in (("source", "wheel") if name in ("body", "snapshot")
                                  else ("wheel",)):
                    with self.subTest(pool=pool, case=name, execution=execution):
                        config = ("import path from 'node:path';"
                                  "export default {define:{ROOT:JSON.stringify(path.resolve('.'))},"
                                  "test:{pool:'" + pool + "',env:{MARKER:'head'}}};")
                        files = {"vitest.config.mjs": config}
                        if name == "snapshot":
                            files["tests/__snapshots__/value.test.js.snap"] = (
                                'exports[`value 1`] = `2`;\n')
                        old = {**files}
                        if name == "config-change":
                            old["vitest.config.mjs"] = config.replace("'head'", "'base'")
                        box = Sandbox(old={"src/value.js": "export const value=1;", **old}, new={
                            "src/value.js": "export const value=2;",
                            "tests/value.test.js": IMPORT + source, **files,
                            ".gitignore": "node_modules\n__pycache__/\n"})
                        try:
                            (box.root / "node_modules").symlink_to(MODULES)
                            launcher = os.environ.get("DV_VITEST4_MATRIX_LAUNCHER", "npm")
                            command = (["pnpm", "exec", "vitest"] if launcher == "pnpm" else
                                       ["./node_modules/.bin/vitest"])
                            command += ["run", "--globals", "--maxWorkers=1"]
                            env = {key: value for key, value in os.environ.items()
                                   if key not in ("PYTHONPATH", "PYTHONHOME", "NODE_PATH")}
                            cli = [CLI]
                            if execution == "source":
                                cli = [sys.executable, "-m", "dv"]
                                env["PYTHONPATH"] = str(ROOT / "src")
                            with tempfile.TemporaryDirectory() as directory:
                                temporary = Path(directory)
                                env.update(TMPDIR=directory, TEMP=directory, TMP=directory)
                                proc = subprocess.run([*cli, "prove", "--test", shlex.join(command),
                                    "--timeout", "30", "--json"], cwd=box.root, env=env,
                                    capture_output=True, text=True, timeout=90)
                                output = json.loads(proc.stdout)
                                self.assertEqual(proc.returncode, 0 if accepted else 1, output)
                                self.assertEqual(output["ok"], accepted, output)
                                self.assertEqual(list(temporary.iterdir()), [])
                            self.assertEqual(git(box.root, "status", "--porcelain"), "")
                            outcomes.append({"pool": pool, "case": name, "execution": execution,
                                             "accepted": accepted, "exit": proc.returncode})
                            target = os.environ.get("DV_VITEST4_MATRIX_EVIDENCE")
                            if target:
                                destination = Path(target) / f"{pool}-{name}-{execution}"
                                shutil.copytree(output["evidence_dir"], destination,
                                                dirs_exist_ok=True)
                        finally:
                            box.close()
        self.assertEqual(dependency_state(Path(MODULES)), before)
        output = os.environ.get("DV_VITEST4_MATRIX_RESULT")
        if output:
            Path(output).write_text(json.dumps(outcomes, indent=2) + "\n")
