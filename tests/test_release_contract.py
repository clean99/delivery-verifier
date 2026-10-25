from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import ROOT, Sandbox

from dv import evidence, redgreen
from dv.checker import checker_digest
from dv.common import DVError, digest, read_json, write_json
from dv.runners import red_assertion, runner_for


class ReleaseContractTests(unittest.TestCase):
    def box(self, **kwargs):
        box = Sandbox(**kwargs)
        self.addCleanup(box.close)
        return box

    def test_only_assertion_failures_prove_red(self):
        cases = {
            "assertion": "def answer():\n    return 21\n",
            "runtime": "def answer():\n    raise RuntimeError('before assertion')\n",
            "import": "import nonexistent_regression_dependency\n",
            "collection": "def broken syntax\n",
            "setup": "def answer():\n    return 42\n",
        }
        for name, broken in cases.items():
            with self.subTest(name=name):
                test = (
                    "import unittest\nfrom answer import answer\n"
                    "class AnswerTests(unittest.TestCase):\n"
                    "    def test_answer(self):\n        self.assertEqual(answer(), 42)\n"
                )
                if name == "setup":
                    test += (
                        "    def setUp(self):\n"
                        "        from pathlib import Path\n"
                        "        if Path('src/value.ts').read_text().strip() != 'fixed':\n"
                        "            raise RuntimeError('setup failed')\n"
                    )
                box = self.box(
                    old={"answer.py": broken},
                    new={
                        "answer.py": "def answer():\n    return 42\n",
                        "tests/test_value.py": test,
                    },
                )
                output = redgreen.run(box.item)
                self.assertEqual(output["green"]["exit_code"], 0, output)
                self.assertEqual(output["ok"], name == "assertion", output)
                if name != "assertion":
                    self.assertIn("red assertion", str(output["checks"]))
                    self.assertEqual(evidence.redgreen_checks(box.item)[0]["outcome"], "fail")

    def test_checker_changes_invalidate_all_evidence_and_inflight_scope(self):
        box = self.box()
        with patch("dv.item.checker_digest", return_value="a" * 64, create=True):
            old = box.ready()
            self.assertTrue(evidence.approve(old, old.head, "Synthetic approval fixture")["ok"])
        with patch("dv.item.checker_digest", return_value="b" * 64, create=True):
            current = box.item
            self.assertNotEqual(current.scope["context_sha256"], old.scope["context_sha256"])
            for check in (
                evidence.redgreen_checks,
                evidence.review_checks,
                evidence.approval_checks,
            ):
                self.assertEqual(check(current)[0]["outcome"], "fail")
            with self.assertRaises(DVError):
                old.ensure_current()

    def test_gate_reclassifies_red_log_instead_of_trusting_flags(self):
        box = self.box()
        box.ready()
        path = box.item.file("redgreen.json")
        record = read_json(path)
        log = b"RuntimeError: before assertion\n"
        box.item.file("redgreen-red.log").write_bytes(log)
        record["red"]["log_sha256"] = digest(log)
        write_json(path, record)
        self.assertEqual(evidence.redgreen_checks(box.item)[0]["outcome"], "fail")

    def test_checker_hash_binds_sources_not_caches_or_location(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            root = Path(temporary)
            for name in ("one", "two"):
                package = root / name
                package.mkdir()
                (package / "__init__.py").write_text('VERSION = "1"\n')
                (package / "checker.py").write_text("pass\n")
                (package / "_vendor").mkdir()
                (package / "_vendor/parser.py").write_text("pass\n")
            first, second = root / "one", root / "two"
            original = checker_digest(first)
            self.assertEqual(original, checker_digest(second))
            (first / "__pycache__").mkdir()
            (first / "__pycache__/checker.pyc").write_bytes(b"cache")
            self.assertEqual(original, checker_digest(first))
            for path in ("__init__.py", "checker.py", "_vendor/parser.py"):
                content = (first / path).read_bytes()
                (first / path).write_bytes(content + b"\n")
                self.assertNotEqual(original, checker_digest(first))
                (first / path).write_bytes(content)
            (first / "extra.py").write_text("pass\n")
            self.assertNotEqual(original, checker_digest(first))
            (first / "extra.py").unlink()
            (first / "_vendor/parser.py").rename(first / "_vendor/renamed.py")
            self.assertNotEqual(original, checker_digest(first))

    def test_missing_checker_binding_is_stale(self):
        box = self.box()
        box.ready()
        path = box.item.file("redgreen.json")
        record = read_json(path)
        del record["checker_sha256"]
        write_json(path, record)
        self.assertEqual(evidence.redgreen_checks(box.item)[0]["outcome"], "fail")

    def test_captured_runner_failures_require_assertions(self):
        for runner in ("pytest", "node"):
            for case in ("assertion", "runtime", "import", "collection", "setup", "mixed"):
                with self.subTest(runner=runner, case=case):
                    content = (ROOT / f"tests/fixtures/runners/{runner}-{case}.log").read_bytes()
                    phase = {"status": "normal", "exit_code": 1}
                    selected = "node-test" if runner == "node" else runner
                    valid, detail = red_assertion(phase, content, selected)
                    self.assertEqual(valid, case == "assertion", detail)
                    if valid:
                        for code in (0, 2, 124, True):
                            phase["exit_code"] = code
                            self.assertFalse(red_assertion(phase, content, selected)[0])
                        phase.update(status="timeout", exit_code=1)
                        self.assertFalse(red_assertion(phase, content, selected)[0])

    def test_hidden_tracebacks_are_not_assertion_proof(self):
        path = ROOT / "tests/fixtures/runners/pytest-assertion.log"
        text = path.read_text()
        for content in (text.split("test_value.py:2:")[0], "1 failed in 0.01s\n"):
            self.assertFalse(
                red_assertion({"status": "normal", "exit_code": 1}, content.encode(), "pytest")[0]
            )
        runtime = (ROOT / "tests/fixtures/runners/pytest-runtime.log").read_text()
        runtime = runtime.replace("before assertion", "assert misleading error text")
        self.assertFalse(
            red_assertion({"status": "normal", "exit_code": 1}, runtime.encode(), "pytest")[0]
        )

    def test_runner_detection_and_explicit_wrapper_contract(self):
        cases = [
            (["python3.12", "-m", "unittest", "discover"], "unittest"),
            (["python", "-m", "pytest"], "pytest"),
            ([".venv/bin/pytest", "-q"], "pytest"),
            (["node", "--test", "--test-reporter=tap"], "node-test"),
            (["python", "-c", "raise RuntimeError()"], "unsupported"),
            (["jest"], "unsupported"),
            (["vitest", "run"], "unsupported"),
        ]
        for command, expected in cases:
            self.assertEqual(runner_for(command), expected)
        self.assertEqual(runner_for(["wrapper"], "unittest"), "unittest")
        box = self.box()
        for value in ("jest", [], None):
            box.update(test_runner=value)
            with self.assertRaises(DVError):
                box.item
