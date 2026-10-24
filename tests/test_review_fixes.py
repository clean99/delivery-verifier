from __future__ import annotations

import base64
import io
import json
import os
import py_compile
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from support import ROOT, Sandbox, git

from dv import cli, evidence, gate, hooks, hygiene, redgreen
from dv.common import DVError, read_json, write_json
from dv.git import Repo


class ReviewFixTests(unittest.TestCase):
    def box(self, **kwargs):
        box = Sandbox(**kwargs)
        self.addCleanup(box.close)
        return box

    def cli(self, box, *args, payload=None):
        return subprocess.run(
            [sys.executable, "-m", "dv", *args],
            cwd=box.root,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_C1_ignored_deleted_path_is_never_overwritten(self):
        for kind in ("file", "symlink", "directory"):
            with self.subTest(kind=kind):
                box = self.box(old={"src/removed.ts": "old\n"}, new={"src/removed.ts": None})
                with (box.root / ".git/info/exclude").open("a") as stream:
                    stream.write("\n/src/removed.ts\n")
                target = box.root / "src/removed.ts"
                if kind == "file":
                    target.write_bytes(b"private local content")
                elif kind == "symlink":
                    target.symlink_to("missing-user-target")
                else:
                    target.mkdir()
                    (target / "keep").write_text("keep")
                self.assertEqual(box.item.repo.clean_problem(), "")
                with self.assertRaises(DVError) as raised:
                    redgreen.run(box.item)
                self.assertEqual(raised.exception.code, "path_collision")
                if kind == "file":
                    self.assertEqual(target.read_bytes(), b"private local content")
                elif kind == "symlink":
                    self.assertEqual(os.readlink(target), "missing-user-target")
                else:
                    self.assertEqual((target / "keep").read_text(), "keep")
                self.assertEqual((box.root / "src/value.ts").read_text(), "fixed\n")

    def test_C3_actual_green_interrupt_invalidates_old_success(self):
        command = [
            sys.executable,
            "-c",
            (
                "import os,signal,time; from pathlib import Path; "
                "good=Path('src/value.ts').read_text().strip()=='fixed'; "
                "interrupt=good and Path('.dv/interrupt').exists(); "
                "os.kill(os.getppid(),signal.SIGINT) if interrupt else None; "
                "time.sleep(30) if interrupt else None; raise SystemExit(0 if good else 1)"
            ),
        ]
        box = self.box(command=command)
        box.ready()
        self.assertTrue(gate.run(box.item)["ok"])
        box.write(".dv/interrupt", "yes")
        proc = self.cli(box, "redgreen", str(box.path), "--json")
        self.assertEqual(proc.returncode, 130, proc.stderr)
        record = read_json(box.root / ".dv/redgreen.json")
        self.assertEqual(record.get("state"), "running")
        self.assertFalse(record["ok"])
        self.assertFalse(gate.run(box.item)["ok"])
        self.assertEqual((box.root / "src/value.ts").read_text(), "fixed\n")

    def test_C3_recovery_plan_blocks_even_complete_or_waived_proof(self):
        box = self.box()
        box.ready()
        write_json(box.root / ".dv/restoration.json", {"pending": True})
        self.assertEqual(evidence.redgreen_checks(box.item)[0]["outcome"], "fail")
        box.update(exceptions={"no_test_reason": "Documentation-only verification"})
        self.assertEqual(evidence.redgreen_checks(box.item)[0]["outcome"], "fail")

    def test_C4_INSTALL_DOC_1_equal_length_import_uses_fresh_phase_caches(self):
        source = "def answer():\n    return 21\n"
        fixed = source.replace("21", "42")
        test = (
            "import unittest\nfrom answer import answer\n"
            "class AnswerTests(unittest.TestCase):\n"
            "    def test_answer(self):\n        self.assertEqual(answer(), 42)\n"
        )
        box = self.box(
            old={"answer.py": source}, new={"answer.py": fixed, "tests/test_answer.py": test}
        )
        path = box.root / "answer.py"
        stamp = 1700000000
        path.write_text(source)
        os.utime(path, (stamp, stamp))
        cache = Path(py_compile.compile(str(path), doraise=True))
        original_cache = cache.read_bytes()
        path.write_text(fixed)
        os.utime(path, (stamp, stamp))
        original = redgreen.restore_path

        def restore(item, ref, name):
            original(item, ref, name)
            if name == "answer.py":
                os.utime(path, (stamp, stamp))

        with patch("dv.redgreen.restore_path", side_effect=restore):
            output = redgreen.run(box.item)
        self.assertTrue(output["ok"], output)
        self.assertEqual((output["red"]["exit_code"], output["green"]["exit_code"]), (1, 0))
        self.assertEqual(cache.read_bytes(), original_cache)

    def test_C5_deleted_baseline_path_mutations_are_saved(self):
        for mutation in (
            "p.write_bytes(b'changed baseline')",
            "p.chmod(0o700)",
            "p.unlink(); p.symlink_to('value.ts')",
        ):
            with self.subTest(mutation=mutation):
                code = (
                    "from pathlib import Path; p=Path('src/removed.ts'); "
                    "good=Path('src/value.ts').read_text().strip()=='fixed'; "
                    f"exec({mutation!r}) if not good else None; raise SystemExit(0 if good else 1)"
                )
                box = self.box(
                    old={"src/removed.ts": "old\n"},
                    new={"src/removed.ts": None},
                    command=[sys.executable, "-c", code],
                )
                output = redgreen.run(box.item)
                self.assertFalse(output["ok"], output)
                self.assertIsNone(output["green"])
                changes = read_json(box.root / ".dv/redgreen-mutations.json")["paths"]
                entry = next(x for x in changes if x["path"] == "src/removed.ts")
                self.assertNotEqual(entry["before"], entry["after"])
                if "write_bytes" in mutation:
                    self.assertEqual(
                        base64.b64decode(entry["after"]["content"]), b"changed baseline"
                    )
                self.assertFalse((box.root / "src/removed.ts").exists())

    def test_HYG_001_callback_body_follows_options_and_destructuring(self):
        for source in (
            "test('count', { timeout: 100 }, () => { assert.equal(2 + 2, 4); });",
            "test('title', ({ page }) => { expect(page.title()).toEqual('Home'); });",
            "test('count', { timeout: 100 }, function ({ value }) { assert.equal(value, 4); });",
        ):
            self.assertEqual(hygiene.weak_tests("value.test.ts", source, {1}), [], source)
        source = "test('none', { timeout: 100 }, ({ page }) => { page.title(); });"
        self.assertTrue(hygiene.weak_tests("value.test.ts", source, {1}))

    def test_HYG_002_only_code_debug_and_comment_todos_fail(self):
        box = self.box(
            new={
                "src/data.ts": (
                    "export const keywords = new Set(['debugger']);\n"
                    "export const initialState = 'TODO';\n"
                    "export const example = `console.log('data')`;\n"
                    "export const pattern = /debugger/;\n"
                )
            }
        )
        self.assertTrue(hygiene.run(box.item)["ok"])
        box.write("src/data.ts", "/*\nconsole.log('example')\nTODO remove this\n*/\n")
        box.commit()
        output = hygiene.run(box.item)
        self.assertTrue(any(r["check"] == "unfinished work" for r in output["checks"]))
        self.assertFalse(any(r["check"] == "debug output" for r in output["checks"]))

    def test_HYG_002_exact_code_exemption_is_visible(self):
        line = "console.log('intentional command output');"
        box = self.box(new={"src/command.js": line + "\n"})
        with (box.root / ".dv.toml").open("a") as stream:
            stream.write("allowed_code_lines = [" + json.dumps(line) + "]\n")
        box.commit()
        output = hygiene.run(box.item)
        self.assertTrue(output["ok"], output)
        self.assertTrue(any(r["check"] == "code exemptions" for r in output["checks"]))
        box.write("src/command.js", " " + line + "\n")
        box.commit()
        self.assertTrue(any(r["check"] == "debug output" for r in hygiene.run(box.item)["checks"]))

    def test_HYG_003_focused_parameterized_and_tagged_tests_fail(self):
        for owner in ("test", "describe"):
            for each in ("([1, 2])", "`value\n${1}\n${2}`"):
                source = f"{owner}.only.each{each}('value', (x) => {{ expect(x).toEqual(1); }});\n"
                box = self.box(new={"tests/value.test.ts": source})
                rows = hygiene.run(box.item)["checks"]
                self.assertTrue(any(r["check"] == "focused test" for r in rows), source)

    def test_HYG_004_python_behavior_boolean_assertions(self):
        for assertion in (
            "self.assertTrue(value() == 42)",
            "self.assertFalse(value() < 0)",
            "self.assertTrue(is_valid(value()))",
            "assert value() == 42",
            "assert is_valid(value())",
        ):
            source = f"def test_value(self):\n    {assertion}\n"
            self.assertFalse(hygiene.weak_tests("test_value.py", source, {1}), assertion)
        for assertion in (
            "self.assertTrue(value)",
            "self.assertTrue(path.exists())",
            "assert True",
        ):
            source = f"def test_value(self):\n    {assertion}\n"
            self.assertTrue(hygiene.weak_tests("test_value.py", source, {1}), assertion)

    def test_HYG_005_go_testify_aliases_and_receivers(self):
        for alias in ("require", "check"):
            for method in ("Equal(t, 2, value())", "NoError(t, run())"):
                source = (
                    f'import {alias} "github.com/stretchr/testify/require"\n'
                    f"func TestValue(t *testing.T) {{ {alias}.{method} }}\n"
                )
                self.assertFalse(hygiene.weak_tests("value_test.go", source, {2}), source)
        source = (
            'import "github.com/stretchr/testify/require"\n'
            "func TestValue(t *testing.T) { check := require.New(t); check.Equal(2, value()) }\n"
        )
        self.assertFalse(hygiene.weak_tests("value_test.go", source, {2}))
        self.assertTrue(hygiene.weak_tests("value_test.go", "func TestValue(t *testing.T) {}", {1}))

    def test_HYG_005_custom_framework_has_a_visible_inspection_waiver(self):
        box = self.box(
            new={
                "tests/value_test.go": (
                    "func TestValue(t *testing.T) { custom.Check(value(), 2) }\n"
                )
            }
        )
        self.assertFalse(hygiene.run(box.item)["ok"])
        box.update(
            exceptions={"weak_test_justification": "Custom Check compares actual and expected"}
        )
        output = hygiene.run(box.item)
        self.assertTrue(output["ok"], output)
        waived = [r for r in output["checks"] if r["check"] == "weak test"]
        self.assertEqual(waived[0]["outcome"], "warn")
        self.assertIn("Custom Check", waived[0]["detail"])
        self.assertEqual(evidence.redgreen_checks(box.item)[0]["outcome"], "fail")

    def test_HYG_006_rust_test_named_test_is_one_block(self):
        source = "#[test]\nfn test() { assert_eq!(value(), 2); }\n"
        self.assertFalse(hygiene.weak_tests("value.rs", source, {1, 2}))
        self.assertTrue(hygiene.weak_tests("value.rs", "#[test]\nfn test() { value(); }", {1, 2}))

    def test_HYG_007_rust_should_panic_is_an_assertion(self):
        for attribute in ("#[should_panic]", '#[should_panic(expected = "empty input")]'):
            source = f'#[test]\n{attribute}\nfn rejects_empty() {{ parse(""); }}\n'
            self.assertFalse(hygiene.weak_tests("value.rs", source, {1, 2, 3}))

    def test_HYG_008_deleting_junk_is_allowed_and_counted(self):
        box = self.box(
            old={"build/old.js": "old\n", "old.key": "synthetic\n"},
            new={"build/old.js": None, "old.key": None},
        )
        output = hygiene.run(box.item)
        self.assertTrue(output["ok"], output)
        self.assertEqual(output["stats"]["removed"], 5)
        self.assertIn("build/old.js", [f["path"] for f in output["files"]])

    def test_HYG_009_regex_escaped_slashes_are_not_comments(self):
        source = r"export const scheme = /https?:\/\//;"
        self.assertEqual(hygiene.comment_text("scheme.ts", source), "")
        self.assertEqual(hygiene.comment_text("scheme.ts", source + " // note"), "// note")
        box = self.box(new={"src/scheme.ts": source + "\n"})
        self.assertTrue(hygiene.run(box.item)["ok"])

    def test_HYG_010_authorship_scans_comment_not_string(self):
        line = 'message = "Generated by Codex" # fixture text'
        box = self.box(new={"src/fixture.py": line + "\n"})
        box.update(exceptions={"comment_justification": "Synthetic parser fixture"})
        self.assertTrue(hygiene.run(box.item)["ok"])
        line = 'message = "text" # Generated by Codex'
        box.write("src/fixture.py", line + "\n")
        box.commit()
        self.assertFalse(hygiene.run(box.item)["ok"])
        with (box.root / ".dv.toml").open("a") as stream:
            stream.write("allowed_prose_lines = [" + json.dumps(line) + "]\n")
        box.commit()
        self.assertTrue(hygiene.run(box.item)["ok"])

    def test_C2_hook_install_is_fail_closed_and_has_outer_timeout(self):
        box = self.box()
        with patch("dv.hooks.shutil.which", return_value="/example/bin/dv"):
            hooks.install(box.root)
        owned = read_json(box.root / ".claude/settings.json")["hooks"]["PreToolUse"][0]["hooks"][0]
        self.assertEqual(owned.get("onFailure"), "block")
        self.assertGreater(owned.get("timeout", 0), 3600)

    def test_C2_hook_interrupt_and_abnormal_exit_emit_deny(self):
        box = self.box()
        payload = {"cwd": str(box.root), "tool_input": {"command": "git push origin HEAD:main"}}
        for error in (KeyboardInterrupt, SystemExit):
            with patch("dv.hooks.gate.run", side_effect=error):
                try:
                    output = hooks.pre_tool_use(payload, box.root)
                except BaseException as caught:
                    self.fail(f"Hook leaked {type(caught).__name__} without a deny decision")
            self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        box.write(
            ".dv.toml",
            "[dv]\nstatic_commands = [["
            + json.dumps(sys.executable)
            + ', "-c", "import os,signal,time; '
            'os.kill(os.getppid(), signal.SIGTERM); time.sleep(30)"]]\n',
        )
        box.commit()
        proc = self.cli(
            box, "hooks", "run", "pre-tool-use", "--project", str(box.root), payload=payload
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_privacy_1_installer_stdout_never_echoes_existing_settings(self):
        box = self.box()
        secret = "synthetic-service-token-do-not-echo"
        path = box.root / ".claude/settings.json"
        original = {"env": {"EXAMPLE_SERVICE_TOKEN": secret}, "theme": "dark"}
        write_json(path, original)
        for dry in (True, False):
            stdout = io.StringIO()
            with (
                patch("dv.hooks.shutil.which", return_value="/example/bin/dv"),
                patch("dv.cli.Path.cwd", return_value=box.root),
                redirect_stdout(stdout),
            ):
                code = cli.main(
                    ["hooks", "install", "--agent", "claude-code", *(["--dry-run"] if dry else [])]
                )
            self.assertEqual(code, 0)
            self.assertNotIn(secret, stdout.getvalue())
            self.assertNotIn("env", json.loads(stdout.getvalue()))
            self.assertEqual(read_json(path)["env"], original["env"])

    def test_privacy_2_promisor_objects_remain_local_only(self):
        box = self.box(old={"src/old.ts": "old blob\n"}, new={"src/old.ts": "new blob\n"})
        git(box.root, "config", "uploadpack.allowFilter", "true")
        with tempfile.TemporaryDirectory(prefix="dv-partial-", dir=ROOT / "tests") as temp:
            clone = Path(temp) / "clone"
            git(
                box.root, "clone", "--filter=blob:none", "--no-local", box.root.as_uri(), str(clone)
            )
            old_oid = git(box.root, "rev-parse", "main:src/old.ts")
            object_listing = git(
                clone, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)"
            )
            self.assertNotIn(old_oid, object_listing.splitlines())
            trace = Path(temp) / "transport.log"
            wrapper = Path(temp) / "remote-wrapper"
            wrapper.write_text('#!/bin/sh\nprintf "contacted\\n" >> ' + str(trace) + "\nexit 1\n")
            wrapper.chmod(0o755)
            git(clone, "remote", "set-url", "origin", "ext::" + str(wrapper))
            git(clone, "config", "protocol.ext.allow", "always")
            with patch.dict(os.environ, {"GIT_NO_LAZY_FETCH": "1"}):
                with self.assertRaises(DVError):
                    Repo(clone).blob("origin/main", "src/old.ts")
            (clone / ".dv").mkdir()
            with (clone / ".git/info/exclude").open("a") as stream:
                stream.write("\n/.dv/\n")
            write_json(clone / ".dv/item.json", {**read_json(box.path), "base_ref": "origin/main"})
            proc = self.cli(box, "hygiene", str(clone / ".dv/item.json"), "--json")
            self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
            self.assertIn("Automatic fetching is disabled", proc.stdout)
            self.assertFalse(trace.exists(), "Internal Git contacted the promisor transport")
            after = git(clone, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)")
            self.assertNotIn(old_oid, after.splitlines())

    def test_INSTALL_DOC_2_existing_inline_rust_error_explains_limitation(self):
        old = "pub fn value() -> u8 { 1 }\n#[test]\nfn test_value() { assert_eq!(value(), 1); }\n"
        box = self.box(
            old={"src/value.rs": old}, new={"src/value.rs": old.replace("{ 1 }", "{ 2 }")}
        )
        with self.assertRaises(DVError) as raised:
            redgreen.run(box.item)
        self.assertIn("even when inline tests are unchanged", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
