from __future__ import annotations

import sys
import unittest

from support import Sandbox, git

from dv import hooks, hygiene, redgreen
from dv.common import DVError


class RegressionTests(unittest.TestCase):
    def box(self, **kwargs):
        box = Sandbox(**kwargs)
        self.addCleanup(box.close)
        return box

    def test_red_mutations_do_not_disappear_into_restoration(self):
        code = (
            "from pathlib import Path; p=Path('src/value.ts'); "
            "fixed=p.read_text().strip()=='fixed'; "
            "p.write_text('mutated red') if not fixed else None; "
            "raise SystemExit(0 if fixed else 1)"
        )
        box = self.box(command=[sys.executable, "-c", code])
        output = redgreen.run(box.item)
        self.assertFalse(output["ok"])
        self.assertIsNone(output["green"])
        self.assertIn("mutated red", (box.root / ".dv/redgreen-mutations.patch").read_text())
        self.assertEqual((box.root / "src/value.ts").read_text(), "fixed\n")

    def test_hidden_dirty_index_flags_prevent_destructive_restore(self):
        for flag in ("--assume-unchanged", "--skip-worktree"):
            box = self.box()
            git(box.root, "update-index", flag, "src/value.ts")
            box.write("src/value.ts", "uncommitted work")
            with self.assertRaises(DVError):
                redgreen.run(box.item)
            self.assertEqual((box.root / "src/value.ts").read_text(), "uncommitted work")

    def test_compact_shell_operators_still_guard_push(self):
        box = self.box()
        for command in (
            "true&&git push origin HEAD:main",
            "true;git push origin HEAD:main",
            "git push>output",
            "true|gh pr create --title x",
        ):
            self.assertTrue(hooks.guarded(command), command)
            output = hooks.pre_tool_use(
                {"cwd": str(box.root), "tool_input": {"command": command}}, box.root
            )
            self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_malformed_preparing_item_cannot_be_silently_skipped(self):
        box = self.box()
        box.update(implementer=42)
        output = hooks.stop({"cwd": str(box.root), "session_id": "test"}, box.root)
        self.assertEqual(output["decision"], "block")

    def test_assertion_words_in_strings_and_comments_are_not_assertions(self):
        for path, source in (
            ("a.test.ts", "it('assert something', () => { run(); });"),
            ("a.test.ts", "it('example', () => { console.log('assert'); });"),
            ("test_a.py", "def test_x():\n    print('assert')\n"),
            ("test_a.py", "def test_x():\n    # assert x == 1\n    run()\n"),
        ):
            self.assertTrue(hygiene.weak_tests(path, source, {1}), (path, source))

    def test_python_exception_assertions_are_behavioral(self):
        source = "def test_x():\n    with pytest.raises(ValueError):\n        parse('bad')\n"
        self.assertFalse(hygiene.weak_tests("test_x.py", source, {1}))

    def test_boolean_fresh_context_cannot_be_forged_with_string(self):
        from dv import evidence, gate
        from dv.common import read_json, write_json

        box = self.box()
        box.ready()
        record = read_json(box.root / ".dv/review.json")
        record.update(reviewer="model-a", fresh_context="yes")
        write_json(box.root / ".dv/review.json", record)
        self.assertEqual(evidence.review_checks(box.item)[0]["outcome"], "fail")
        self.assertFalse(gate.run(box.item)["ok"])

    def test_changed_retained_test_fails_without_reverting_it(self):
        code = (
            "from pathlib import Path; "
            "Path('tests/test_value.py').write_text('changed test'); raise SystemExit(1)"
        )
        box = self.box(command=[sys.executable, "-c", code])
        output = redgreen.run(box.item)
        self.assertFalse(output["ok"])
        self.assertEqual((box.root / "tests/test_value.py").read_text(), "changed test")
        self.assertEqual((box.root / "src/value.ts").read_text(), "fixed\n")

    def test_attached_http_method_flags_are_guarded(self):
        for flag in ("-XPOST", "-X=PATCH", "--method=DELETE", "-X post"):
            self.assertTrue(hooks.guarded(f"gh api repos/example/project {flag}"), flag)
        self.assertFalse(hooks.guarded("gh api repos/example/project -XGET"))

    def test_automatic_extra_push_refs_are_not_approved(self):
        box = self.box()
        git(box.root, "config", "push.followTags", "true")
        with self.assertRaises(DVError):
            hooks.push_source("git push origin HEAD:main", box.root, box.root)

    def test_hook_install_requires_an_installed_entry_point(self):
        from unittest.mock import patch

        box = self.box()
        with patch("dv.hooks.shutil.which", return_value=None):
            with patch("dv.hooks.sys.argv", ["python"]), self.assertRaises(DVError) as raised:
                hooks.install(box.root, dry_run=True)
        self.assertEqual(raised.exception.code, "missing_executable")
