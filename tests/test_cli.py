from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from support import REPORT, ROOT, Sandbox

from dv import __version__
from dv.cli import ExitCode, build_parser, main

SHA = "a" * 40


class CommandLineTests(unittest.TestCase):
    def box(self):
        box = Sandbox()
        self.addCleanup(box.close)
        return box

    def invoke(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_help_and_version(self):
        cases = [(["--version"], f"dv {__version__}"), (["--help"], "Local checks only")]
        for command in ("init", "redgreen", "hygiene", "review", "approve", "gate", "hooks"):
            cases.append(([command, "--help"], "usage:"))
        for argv, expected in cases:
            with self.subTest(argv=argv):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as raised:
                    main(argv)
                self.assertEqual(raised.exception.code, 0)
                self.assertIn(expected, stdout.getvalue())

    def test_invalid_invocations_exit_two(self):
        cases = (
            [],
            ["missing"],
            ["gate"],
            ["init"],
            ["review"],
            ["hooks"],
            ["hooks", "install", "--agent", "other"],
            ["gate", ".dv/item.json", "--require"],
            ["redgreen", ".dv/item.json", "--timeout", "0"],
            ["redgreen", ".dv/item.json", "--timeout", "later"],
            ["approve", ".dv/item.json", "--head", "abcdef0", "--note", "Approve"],
            ["approve", ".dv/item.json", "--head", "z" * 40, "--note", "Approve"],
        )
        for argv in cases:
            with (
                self.subTest(argv=argv),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                main(argv)
            self.assertEqual(raised.exception.code, ExitCode.INVALID_INPUT)

    def test_parser_defaults_and_full_ids(self):
        args = build_parser().parse_args(["init", "--implementer", "model-a"])
        self.assertEqual(
            (args.item, args.base, args.kind), (Path(".dv/item.json"), "main", "small")
        )
        for length in (40, 64):
            args = build_parser().parse_args(
                ["approve", ".dv/item.json", "--head", "B" * length, "--note", "Approve"]
            )
            self.assertEqual(args.head, "b" * length)
        args = build_parser().parse_args(["hygiene", ".dv/work item/item.json"])
        self.assertEqual(args.item, Path(".dv/work item/item.json"))

    def test_json_error_envelope(self):
        box = self.box()
        box.write(".dv/bad.json", "{}")
        code, stdout, stderr = self.invoke("gate", str(box.root / ".dv/bad.json"), "--json")
        self.assertEqual(code, 2)
        self.assertEqual(stderr, "")
        output = json.loads(stdout)
        self.assertFalse(output["ok"])
        self.assertEqual(output["error"]["code"], "invalid_input")

    def test_cli_full_local_workflow(self):
        box = self.box()
        item = str(box.path)
        box.write(".dv/notes.txt", REPORT)
        commands = [
            ("redgreen", item, "--json"),
            ("hygiene", item, "--json"),
            (
                "review",
                "record",
                item,
                "--reviewer",
                "model-b",
                "--head",
                box.head,
                "--verdict",
                "pass",
                "--file",
                str(box.root / ".dv/notes.txt"),
                "--json",
            ),
            ("gate", item, "--json"),
            ("approve", item, "--head", box.head, "--note", "Approved exact inputs", "--json"),
            ("gate", item, "--require-approval", "--json"),
        ]
        for command in commands:
            code, stdout, stderr = self.invoke(*command)
            self.assertEqual(code, 0, (command, stdout, stderr))
            self.assertTrue(json.loads(stdout)["ok"])
            self.assertEqual(stderr, "")

    def test_cli_check_failure_is_one_and_warnings_are_visible(self):
        box = self.box()
        code, stdout, _ = self.invoke("gate", str(box.path))
        self.assertEqual(code, 1)
        self.assertIn("[FAIL]", stdout)
        self.assertIn("[WARN]", stdout)
        self.assertIn("Next:", stdout)

    def test_operational_failure_is_three(self):
        box = self.box()
        box.update(test_command=["dv-command-does-not-exist"])
        code, stdout, _ = self.invoke("redgreen", str(box.path), "--json")
        self.assertEqual(code, 3)
        self.assertFalse(json.loads(stdout)["ok"])

    def test_hook_protocol_exits_zero_on_deny(self):
        box = self.box()
        payload = json.dumps({"cwd": str(box.root), "tool_input": {"command": "git push"}})
        with patch("sys.stdin", io.StringIO(payload)):
            code, stdout, stderr = self.invoke(
                "hooks", "run", "pre-tool-use", "--project", str(box.root)
            )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_interrupt_exit_code(self):
        with patch("dv.cli.execute", side_effect=KeyboardInterrupt):
            code, stdout, _ = self.invoke("gate", ".dv/item.json", "--json")
        self.assertEqual(code, 130)
        self.assertFalse(json.loads(stdout)["ok"])

    def test_module_entrypoint_in_external_cwd(self):
        box = self.box()
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run(
            [sys.executable, "-m", "dv", "--version"],
            cwd=box.root,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), f"dv {__version__}")
        result = subprocess.run(
            [sys.executable, "-m", "dv", "hygiene", ".dv/item.json", "--json"],
            cwd=box.root,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])
