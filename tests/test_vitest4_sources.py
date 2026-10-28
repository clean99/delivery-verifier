from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import ROOT

from dv.common import DVError
from dv.vitest4 import node_json, select

VITEST4 = os.environ.get("DV_VITEST4")


@unittest.skipUnless(shutil.which("node"), "Node is needed for configuration hashing")
class VitestConfigTests(unittest.TestCase):
    def test_typed_path_spans_preserve_config_distinctions(self):
        output = node_json("""
          import {pathToFileURL} from 'node:url';
          import {resolve} from 'node:path';
          const {configHashes}=await import(pathToFileURL(process.argv[1]));
          const roots=['red','green'].map(name=>resolve(process.argv[2],name));
          const hash=(value,root)=>configHashes({viteDefine:value},{},root).viteDefine;
          const equal=fn=>hash(fn(roots[0]),roots[0])===hash(fn(roots[1]),roots[1]);
          const a=roots[0];
          const different=(x,y)=>hash(x,a)!==hash(y,a);
          console.log(JSON.stringify({
            root:equal(r=>({ROOT:r})), quoted:equal(r=>({ROOT:JSON.stringify(r)})),
            escaped:equal(r=>({ROOT:JSON.stringify(JSON.stringify(r))})),
            regex:equal(r=>new RegExp('^'+r+'/suffix','i')),
            prefix:different(a,a+'-outside'), marker:different(a,'.'),
            markerArray:different(a,['root']), type:different('1',1),
            value:different({X:1},{X:2}), missing:different({X:undefined},{}),
            flag:different(/suffix/i,/suffix/g), suffix:different(/one/,/two/),
            index:different(Object.assign(/one/g,{lastIndex:1}),/one/g),
            external:different(resolve(a,'../outside-a'),resolve(a,'../outside-b')),
          }));
        """, str(ROOT / "src/dv/_vitest_reporter.mjs"), tempfile.gettempdir(), cwd=ROOT)
        self.assertTrue(all(output.values()), output)

    def test_resolver_closures_cannot_be_identified_by_function_source(self):
        output = node_json("""
          import {pathToFileURL} from 'node:url';
          const {configHashes}=await import(pathToFileURL(process.argv[1]));
          const resolver=target=>()=>target;
          const one=resolver('one.js'), two=resolver('two.js');
          const rejected=[];
          for(const resolveId of [one,two]) {
            for(const customResolver of [resolveId,{resolveId}]) {
              try {
                configHashes({}, {resolve:{alias:[{find:'oracle',replacement:'unused',
                  customResolver}]}}, process.cwd());
                rejected.push(false);
              } catch(error) {rejected.push(error.message.includes('vite.resolve'));}
            }
          }
          console.log(JSON.stringify({sameSource:String(one)===String(two),rejected}));
        """, str(ROOT / "src/dv/_vitest_reporter.mjs"), cwd=ROOT)
        self.assertTrue(output["sameSource"])
        self.assertEqual(output["rejected"], [True] * 4)

    def test_unsupported_config_objects_and_cycles_fail_closed(self):
        output = node_json("""
          import {pathToFileURL} from 'node:url';
          const {configHashes}=await import(pathToFileURL(process.argv[1]));
          const cycle={}; cycle.self=cycle;
          const values=[cycle,new Map(),new Set()];
          console.log(JSON.stringify(values.map(value=>{
            try {configHashes({viteDefine:value},{},process.cwd());return false;}
            catch {return true;}
          })));
        """, str(ROOT / "src/dv/_vitest_reporter.mjs"), cwd=ROOT)
        self.assertEqual(output, [True, True, True])


@unittest.skipUnless(VITEST4 and shutil.which(VITEST4), "Set DV_VITEST4 for source checks")
class VitestSourceTests(unittest.TestCase):
    def test_drifted_source_is_rejected_before_any_dependency_is_modified(self):
        selection = select([VITEST4], ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = Path(selection["path"])
            modules = next(parent for parent in reversed(package.parents)
                           if parent.name == "node_modules")
            shutil.copytree(modules, root / "node_modules", symlinks=True)
            manifest = root / "node_modules" / package.relative_to(modules)
            script = ("import {pathToFileURL} from 'node:url';"
                      "const {selectedSources}=await import(pathToFileURL(process.argv[1]));"
                      "console.log(JSON.stringify(selectedSources(process.argv[2],process.argv[3])));")
            source = root / "node_modules/@vitest/runner/dist/chunk-artifact.js"
            source.write_text(source.read_text() + "\n// source drift\n")
            expected = source.read_bytes()
            with self.assertRaisesRegex(DVError, "unrecognized Vitest 4.1.12 source"):
                node_json(script, str(ROOT / "src/dv/_vitest4_sources.mjs"), str(manifest),
                          str(root), cwd=root)
            self.assertEqual(source.read_bytes(), expected)

    def test_missing_body_instrumentation_rejects_real_execution(self):
        from dv.execution import green_execution
        from dv.runners import run_test

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Simulate a module graph that loads the stock handler despite the adapter.
            (root / "value.test.js").write_text("test('value',()=>expect(2).toBe(2));")
            (root / "vitest.config.mjs").write_text(
                "import fs from 'node:fs';"
                "export default {plugins:[{name:'remove-body-probe',configResolved(){"
                "const p=process.env.DV_VITEST4_SOURCES;"
                "if(p){const m=JSON.parse(fs.readFileSync(p));"
                "fs.writeFileSync(m.runner.path,fs.readFileSync(m.runner.path,'utf8')"
                ".replace('context, dvBody: true','context'));}}}]};")
            phase = run_test([VITEST4, "run", "--globals", "--maxWorkers=1"], root,
                             root / "run.log", 30, dict(os.environ))
            self.assertFalse(green_execution(phase, (root / "run.log").read_bytes(), "vitest")[0])

    def test_source_manifest_is_bound_to_the_executing_package(self):
        package = select([VITEST4], ROOT)["path"]
        proc = subprocess.run(["node", "--input-type=module", "-e",
            "import {pathToFileURL} from 'node:url';"
            "const {selectedSources}=await import(pathToFileURL(process.argv[1]));"
            "selectedSources(process.argv[2]);", str(ROOT / "src/dv/_vitest4_sources.mjs"),
            package], cwd=ROOT, env={**os.environ, "DV_VITEST4_SOURCES": os.devnull},
            capture_output=True, text=True, timeout=10)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(proc.stdout)

    def test_missing_final_collection_is_not_a_valid_receipt(self):
        from test_vitest_runner import captured, record_from, report_text

        from dv.vitest import parse_report

        record = record_from(captured("pass"))
        record.update(version="4.1.12", sourceVerified=True, postCloseErrors=[], finalErrors=[])
        for field in ("postCloseErrors", "finalErrors", "shutdownComplete"):
            changed = json.loads(json.dumps(record))
            changed.pop(field)
            self.assertFalse(parse_report(report_text(changed).decode())[1])
