"""First-usable acceptance contract; DV_SPEC_STRICT=1 exposes pending failures.

Expected failures are implementation debt, not passing acceptance checks. Remove
pending markers as each contract lands; unexpected successes fail the normal suite.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from support import ROOT, Sandbox, git


def pending(test):
    return test if os.environ.get("DV_SPEC_STRICT") == "1" else unittest.expectedFailure(test)


def environment():
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        PYTHONPATH=str(ROOT / "src"),
        PYTHONDONTWRITEBYTECODE="1",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        HUSKY="0",
        LEFTHOOK="0",
        UV_NO_CACHE="1",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
    )
    return env


TEST = """import json
import os
import subprocess
import unittest
from pathlib import Path
from value import value

class ValueTests(unittest.TestCase):
    def test_value(self):
        print('SPEC-EXECUTION ' + json.dumps({
            'cwd': str(Path.cwd().resolve()),
            'head': subprocess.check_output(
                ['git', 'rev-parse', 'HEAD'], text=True).strip(),
            'branch': subprocess.run(
                ['git', 'symbolic-ref', '-q', 'HEAD'],
                capture_output=True, text=True).stdout.strip(),
            'cache': os.environ.get('PYTHONPYCACHEPREFIX'),
            'value': value(),
        }))
        self.assertEqual(value(), 42)
"""
COMMAND = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]


class FirstUsableProofSpec(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="dv-prove-spec-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.root = self.directory / "project with spaces"
        self.root.mkdir()
        self.env = environment()

    def require_prove(self):
        proc = self.invoke("prove", "--help")
        self.assertEqual(
            proc.returncode,
            0,
            "SPEC pending: dv prove must be a real CLI command.\n" + proc.stderr,
        )
        self.assertIn("--test", proc.stdout)

    def fixture(
        self, *, test=TEST, old="def value():\n    return 21\n", new="def value():\n    return 42\n"
    ):
        git(self.root, "init", "-b", "main")
        self.write(".gitignore", "__pycache__/\nignored.local\n")
        self.write("value.py", old)
        self.commit("test: define baseline")
        self.base = git(self.root, "rev-parse", "HEAD")
        git(self.root, "switch", "-c", "fix")
        self.write("value.py", new)
        self.write("tests/test_value.py", test)
        self.commit("test: add fix and regression")
        self.head = git(self.root, "rev-parse", "HEAD")
        self.env["TMPDIR"] = str(self.directory / "execution-temp")
        Path(self.env["TMPDIR"]).mkdir()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit(self, message):
        git(self.root, "add", ".")
        git(
            self.root,
            "-c",
            "user.name=clean99",
            "-c",
            "user.email=53188310+clean99@users.noreply.github.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            message,
        )

    def invoke(self, *args, timeout=30):
        return subprocess.run(
            [sys.executable, "-m", "dv", *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def prove(self, *args, command=COMMAND, expected=0):
        proc = self.invoke("prove", "--test", shlex.join(command), "--json", *args)
        self.assertEqual(proc.returncode, expected, proc.stdout + proc.stderr)
        self.assertEqual(proc.stderr, "", proc.stderr)
        output = json.loads(proc.stdout)
        self.assertEqual(output["command"], "prove")
        self.assertEqual(output["ok"], expected == 0, output)
        return output

    def state(self):
        status = git(self.root, "status", "--porcelain=v1", "--untracked-files=all")
        branch = git(self.root, "symbolic-ref", "HEAD")
        worktrees = git(self.root, "worktree", "list", "--porcelain")
        files = {}
        for path in self.root.rglob("*"):
            if ".git" in path.relative_to(self.root).parts:
                continue
            if path.is_file():
                files[str(path.relative_to(self.root))] = (
                    path.read_bytes(),
                    path.stat().st_mode,
                )
        return (
            files,
            (self.root / ".git/index").read_bytes(),
            (self.root / ".git/config").read_bytes(),
            (self.root / ".git/info/exclude").read_bytes(),
            status,
            branch,
            worktrees,
        )

    def evidence(self, output):
        directory = Path(output["evidence_dir"])
        self.assertTrue(directory.is_dir(), output)
        self.assertEqual(json.loads((directory / "proof.json").read_text()), output)
        for phase in ("red", "green"):
            if output.get(phase) is None:
                continue
            log = (directory / f"{phase}.log").read_bytes()
            self.assertEqual(hashlib.sha256(log).hexdigest(), output[phase]["log_sha256"])
        return directory

    def assert_cleaned(self, before):
        self.assertEqual(self.state(), before)
        self.assertEqual(list(Path(self.env["TMPDIR"]).iterdir()), [])

    def test_A01_one_command_records_real_red_green_without_item(self):
        self.require_prove()
        self.fixture()
        before = self.state()
        output = self.prove()
        self.assertEqual(output["head_sha"], self.head)
        self.assertEqual(output["base_sha"], self.base)
        self.assertEqual(output["argv"], COMMAND)
        self.assertEqual(output["runner"], "unittest")
        self.assertEqual(output["state"], "complete")
        self.assertEqual(output["red"]["exit_code"], 1)
        self.assertEqual(output["green"]["exit_code"], 0)
        self.assertGreater(output["red"]["tests"]["executed"], 0)
        self.assertGreater(output["green"]["tests"]["passed"], 0)
        self.assertEqual(
            output["optional_checks"], {"hygiene": "not_requested", "review": "not_requested"}
        )
        self.assertEqual(output["isolation"], "git-worktree")
        self.assertFalse(output["security_sandbox"])
        self.assertRegex(output["checker_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(output["context_sha256"], r"^[0-9a-f]{64}$")
        logs = self.evidence(output)
        self.assertIn("AssertionError", (logs / "red.log").read_text())
        self.assertIn("OK", (logs / "green.log").read_text())
        self.assert_cleaned(before)

    def test_A02_hollow_test_does_not_prove_change(self):
        self.require_prove()
        self.fixture(
            test=TEST.replace("self.assertEqual(value(), 42)", "self.assertIsNotNone(value())")
        )
        before = self.state()
        output = self.prove(expected=1)
        self.assertEqual(output["red"]["exit_code"], 0)
        self.assertIn("red assertion", str(output["checks"]))
        self.evidence(output)
        self.assert_cleaned(before)

    def test_A03_wrong_reason_failure_is_rejected(self):
        self.require_prove()
        for old in (
            "def value():\n    raise RuntimeError('not a behavioral assertion')\n",
            "import nonexistent_spec_dependency\n",
        ):
            with self.subTest(old=old):
                with tempfile.TemporaryDirectory(dir=self.directory) as path:
                    self.root = Path(path)
                    self.env.pop("TMPDIR", None)
                    self.fixture(old=old)
                    before = self.state()
                    output = self.prove(expected=1)
                    self.assertEqual(output["green"]["exit_code"], 0)
                    self.assertIn("red assertion", str(output["checks"]))
                    self.assert_cleaned(before)
                Path(self.env["TMPDIR"]).rmdir()

    def test_A04_green_with_zero_executed_tests_is_rejected(self):
        self.require_prove()
        test = (
            TEST
            + "\ndef load_tests(loader, tests, pattern):\n"
            "    if value() == 42:\n"
            "        print('Bootstrap exited before running tests')\n"
            "        raise SystemExit(0)\n"
            "    return tests\n"
        )
        self.fixture(test=test)
        output = self.prove(expected=1)
        self.assertEqual(output["red"]["exit_code"], 1)
        self.assertEqual(output["green"]["exit_code"], 0)
        self.assertEqual(output["green"]["tests"]["executed"], 0)
        self.assertIn("green execution", str(output["checks"]))

    def test_A05_green_with_only_skipped_tests_is_rejected(self):
        self.require_prove()
        test = TEST.replace(
            "    def test_value(self):",
            "    @unittest.skipIf(value() == 42, 'conditional skip')\n    def test_value(self):",
        )
        self.fixture(test=test)
        output = self.prove(expected=1)
        self.assertEqual(output["green"]["exit_code"], 0)
        self.assertEqual(output["green"]["tests"]["executed"], 0)
        self.assertEqual(output["green"]["tests"]["skipped"], 1)

    def test_A06_both_phases_are_detached_and_leave_dirty_checkout_untouched(self):
        self.require_prove()
        self.fixture()
        self.write("value.py", "local staged version\n")
        git(self.root, "add", "value.py")
        self.write("value.py", "local unstaged version\n")
        self.write("scratch.local", "untracked content\n")
        self.write("ignored.local", "ignored content\n")
        before = self.state()
        output = self.prove()
        directory = self.evidence(output)
        executions = []
        for phase, commit in (("red", self.base), ("green", self.head)):
            lines = (directory / f"{phase}.log").read_text().splitlines()
            payload = next(
                line.removeprefix("SPEC-EXECUTION ")
                for line in lines
                if line.startswith("SPEC-EXECUTION ")
            )
            execution = json.loads(payload)
            self.assertNotEqual(Path(execution["cwd"]), self.root)
            self.assertFalse(Path(execution["cwd"]).exists())
            self.assertEqual(execution["head"], commit)
            self.assertEqual(execution["branch"], "")
            self.assertTrue(execution["cache"])
            executions.append(execution)
        self.assertNotEqual(executions[0]["cwd"], executions[1]["cwd"])
        self.assertNotEqual(executions[0]["cache"], executions[1]["cache"])
        self.assert_cleaned(before)

    def test_A07_explicit_head_and_merge_base_do_not_use_callers_head(self):
        self.require_prove()
        self.fixture()
        git(self.root, "switch", "main")
        self.write("unrelated.txt", "base branch advanced\n")
        self.commit("test: advance base independently")
        before = self.state()
        output = self.prove("--base", "main", "--head", "fix")
        self.assertEqual(output["head_sha"], self.head)
        self.assertEqual(output["base_sha"], self.base)
        self.assert_cleaned(before)

    def test_A08_test_command_is_not_shell_code(self):
        self.require_prove()
        self.fixture()
        sentinel = self.directory / "injected-file"
        command = shlex.join(COMMAND) + "; touch " + shlex.quote(str(sentinel))
        proc = self.invoke("prove", "--test", command, "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(sentinel.exists())
        for value in ("", "   ", "unterminated '"):
            proc = self.invoke("prove", "--test", value, "--json")
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_A09_timeout_cleans_only_owned_worktrees(self):
        self.require_prove()
        test = TEST.replace(
            "        self.assertEqual",
            "        import time\n        time.sleep(60)\n        self.assertEqual",
        )
        self.fixture(test=test)
        other = self.directory / "unrelated-worktree"
        git(self.root, "worktree", "add", "--detach", str(other), self.base)
        before = self.state()
        output = self.prove("--timeout", "1", expected=3)
        self.assertEqual(output["red"]["status"], "timeout")
        self.assertTrue(other.is_dir())
        self.evidence(output)
        self.assert_cleaned(before)

    def test_A10_missing_executable_is_operational_not_red(self):
        self.require_prove()
        self.fixture()
        before = self.state()
        command = [str(self.directory / "python3"), "-m", "unittest", "discover", "-s", "tests"]
        output = self.prove(command=command, expected=3)
        self.assertEqual(output["red"]["status"], "launch_error")
        self.assert_cleaned(before)

    def test_A11_sigterm_invalidates_attempt_and_cleans_up(self):
        self.require_prove()
        marker = self.directory / "started"
        self.env["DV_SPEC_STARTED"] = str(marker)
        test = TEST.replace(
            "        self.assertEqual",
            "        import time\n"
            "        Path(os.environ['DV_SPEC_STARTED']).write_text('started')\n"
            "        time.sleep(60)\n        self.assertEqual",
        )
        self.fixture(test=test)
        before = self.state()
        with subprocess.Popen(
            [sys.executable, "-m", "dv", "prove", "--test", shlex.join(COMMAND), "--json"],
            cwd=self.root,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as proc:
            try:
                deadline = time.monotonic() + 10
                while not marker.exists() and proc.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(marker.exists(), "test process did not start")
                proc.send_signal(signal.SIGTERM)
                stdout, stderr = proc.communicate(timeout=10)
                self.assertEqual(proc.returncode, 130, stdout + stderr)
                self.assertFalse(json.loads(stdout)["ok"])
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()
        self.assert_cleaned(before)
        records = list((self.root / ".git/dv/proofs").glob("*/proof.json"))
        self.assertTrue(records, "interrupted attempt must remain inspectable")
        self.assertTrue(all(not json.loads(path.read_text())["ok"] for path in records))

    def test_A12_hygiene_is_opt_in_and_never_runs_full_suite(self):
        self.require_prove()
        self.fixture()
        self.write("src/debug.js", "console.log('leftover debug');\n")
        self.write(
            ".dv.toml",
            '[dv]\nstatic_commands = [["missing-static-command"]]\n'
            'test_commands = [["missing-suite-command"]]\n',
        )
        self.commit("test: add optional policy violation")
        plain = self.prove()
        self.assertEqual(plain["optional_checks"]["hygiene"], "not_requested")
        checked = self.prove("--hygiene", expected=1)
        self.assertEqual(checked["optional_checks"]["hygiene"], "fail")
        self.assertEqual(checked["green"]["exit_code"], 0)
        self.assertNotIn("launch_error", str(checked))

    def test_A13_opt_in_review_reuses_binding_and_rejects_stale_report(self):
        self.require_prove()
        box = Sandbox()
        self.addCleanup(box.close)
        self.root = box.root
        box.ready()
        command = box.item.meta["test_command"]
        self.prove("--require-review", str(box.path), command=command)
        git(
            self.root,
            "commit",
            "--amend",
            "--no-edit",
            "--allow-empty",
            "-m",
            "Changed review scope",
        )
        self.prove(command=command)
        rejected = self.prove("--require-review", str(box.path), command=command, expected=1)
        self.assertEqual(rejected["optional_checks"]["review"], "fail")
        self.assertIn("stale", str(rejected["checks"]).lower())

    def test_A14_new_attempt_never_reuses_old_success_or_overwrites_item(self):
        self.require_prove()
        self.fixture()
        self.write(".dv/item.json", '{"keep": "existing user metadata"}\n')
        original = (self.root / ".dv/item.json").read_bytes()
        good = self.prove()
        self.write(
            "tests/test_value.py",
            TEST.replace("self.assertEqual(value(), 42)", "self.assertIsNotNone(value())"),
        )
        self.commit("test: replace assertion with hollow check")
        bad = self.prove(expected=1)
        self.assertNotEqual(good["evidence_dir"], bad["evidence_dir"])
        self.assertNotEqual(good["context_sha256"], bad["context_sha256"])
        self.assertEqual((self.root / ".dv/item.json").read_bytes(), original)
        self.evidence(good)
        self.evidence(bad)

    def test_A15_help_states_scope_and_reporter_limits(self):
        self.require_prove()
        text = self.invoke("prove", "--help").stdout.lower()
        for phrase in ("committed", "not a security sandbox", "unittest", "pytest", "tap"):
            self.assertIn(phrase, text)
        for option in ("--base", "--head", "--hygiene", "--require-review", "--test-file"):
            self.assertIn(option, text)
        self.fixture()
        for command in (["jest"], ["vitest", "run"]):
            output = self.prove(command=command, expected=2)
            self.assertEqual(output["error"]["code"], "unsupported_runner")

    def test_A16_explicit_test_file_retains_custom_test_without_retaining_fix(self):
        self.require_prove()
        self.fixture()
        (self.root / "tests/test_value.py").unlink()
        self.write("checks/regression.py", TEST)
        self.commit("test: move regression to explicit custom path")
        command = [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "checks",
            "-p",
            "regression.py",
        ]
        output = self.prove("--test-file", "checks/regression.py", command=command)
        self.assertIn("checks/regression.py", output["retained_tests"])
        self.assertNotIn("value.py", output["retained_tests"])
        rejected = self.prove("--test-file", "value.py", command=command, expected=2)
        self.assertEqual(rejected["error"]["code"], "mixed_test_file")

    def test_A17_existing_pytest_reporter_works_through_prove(self):
        self.require_prove()
        if importlib.util.find_spec("pytest") is None:
            self.skipTest("pytest 8.4.2 is required in the release matrix")
        self.fixture()
        output = self.prove(
            command=[
                sys.executable,
                "-m",
                "pytest",
                "--tb=long",
                "--color=no",
                "-p",
                "no:cacheprovider",
                "tests/test_value.py",
            ]
        )
        self.assertEqual(output["runner"], "pytest")
        self.assertEqual(output["green"]["tests"]["passed"], 1)

    def test_A18_existing_node_tap_reporter_works_through_prove(self):
        self.require_prove()
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node 20 is required in the release matrix")
        self.fixture()
        self.write(
            "tests/value.test.cjs",
            (
                "const test = require('node:test');\n"
                "const assert = require('node:assert/strict');\n"
                "const fs = require('node:fs');\n"
                "test('value', () => {\n"
                "  assert.equal(fs.readFileSync('value.py', 'utf8').includes('return 42'), true);\n"
                "});\n"
            ),
        )
        self.commit("test: add TAP regression")
        output = self.prove(command=[node, "--test", "--test-reporter=tap", "tests/value.test.cjs"])
        self.assertEqual(output["runner"], "node-test")
        self.assertEqual(output["green"]["tests"]["passed"], 1)


if __name__ == "__main__":
    unittest.main()
