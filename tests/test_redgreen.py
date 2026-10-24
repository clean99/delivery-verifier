from __future__ import annotations

import os
import signal
import sys
import unittest
from unittest.mock import patch

from support import Sandbox, git

from dv import redgreen
from dv.common import DVError


class RedGreenTests(unittest.TestCase):
    def box(self, **kwargs):
        box = Sandbox(**kwargs)
        self.addCleanup(box.close)
        return box

    def assert_restored(self, box, index):
        self.assertEqual(box.item.repo.index(), index)
        self.assertEqual(git(box.root, "status", "--porcelain"), "")
        self.assertEqual((box.root / "src/value.ts").read_text(), "fixed\n")
        self.assertFalse((box.root / ".dv/restoration.json").exists())

    def test_real_red_then_green_and_logs(self):
        box = self.box()
        index = box.item.repo.index()
        output = redgreen.run(box.item)
        self.assertTrue(output["ok"], output)
        self.assertEqual(output["red"]["exit_code"], 1)
        self.assertEqual(output["green"]["exit_code"], 0)
        self.assertIn("AssertionError", (box.root / ".dv/redgreen-red.log").read_text())
        self.assert_restored(box, index)

    def test_always_pass_rejected(self):
        box = self.box(command=[sys.executable, "-c", "pass"])
        output = redgreen.run(box.item)
        self.assertFalse(output["ok"])
        self.assertEqual(output["red"]["exit_code"], 0)
        self.assertEqual(output["green"]["exit_code"], 0)

    def test_green_failure_rejected(self):
        box = self.box(command=[sys.executable, "-c", "raise SystemExit(1)"])
        self.assertFalse(redgreen.run(box.item)["ok"])

    def test_added_deleted_paths_and_modes_restore(self):
        box = self.box(
            old={"src/removed file.ts": "old\n"},
            new={"src/removed file.ts": None, "src/new file.ts": "new\n"},
        )
        (box.root / "src/new file.ts").chmod(0o755)
        box.commit()
        box.update(
            test_command=[
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; assert Path('src/new file.ts').exists()"
                    "; assert not Path('src/removed file.ts').exists()"
                ),
            ]
        )
        index = box.item.repo.index()
        self.assertTrue(redgreen.run(box.item)["ok"])
        self.assertFalse((box.root / "src/removed file.ts").exists())
        self.assertTrue(os.access(box.root / "src/new file.ts", os.X_OK))
        self.assert_restored(box, index)

    def test_symlink_source_is_restored_without_writing_target(self):
        box = self.box()
        box.write("src/target.ts", "fixed\n")
        (box.root / "src/link.ts").symlink_to("target.ts")
        box.commit()
        index = box.item.repo.index()
        self.assertTrue(redgreen.run(box.item)["ok"])
        self.assertTrue((box.root / "src/link.ts").is_symlink())
        self.assert_restored(box, index)

    def test_dirty_untracked_or_index_changes_are_rejected_before_mutation(self):
        for staged in (False, True):
            box = self.box()
            box.write("unrelated.txt", "preserve\n")
            if staged:
                git(box.root, "add", "unrelated.txt")
            before = box.item.repo.index()
            with self.assertRaises(DVError) as raised:
                redgreen.run(box.item)
            self.assertEqual(raised.exception.exit_code, 1)
            self.assertEqual(box.item.repo.index(), before)
            self.assertEqual((box.root / "unrelated.txt").read_text(), "preserve\n")
            self.assertEqual((box.root / "src/value.ts").read_text(), "fixed\n")

    def test_merge_in_progress_is_rejected(self):
        box = self.box()
        (box.root / ".git/MERGE_HEAD").write_text(box.head)
        with self.assertRaises(DVError):
            redgreen.run(box.item)

    def test_timeout_is_not_red_and_restores(self):
        box = self.box(command=[sys.executable, "-c", "import time; time.sleep(30)"])
        index = box.item.repo.index()
        output = redgreen.run(box.item, timeout=1)
        self.assertFalse(output["ok"])
        self.assertEqual(output["red"]["status"], "timeout")
        self.assertEqual(output["exit_code"], 3)
        self.assert_restored(box, index)

    def test_timeout_reaps_child_process_group(self):
        box = self.box(
            command=[
                sys.executable,
                "-c",
                "import subprocess,sys,time; from pathlib import Path; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                "Path('.dv/child-pid').write_text(str(p.pid)); time.sleep(30)",
            ]
        )
        redgreen.run(box.item, timeout=1)
        pid = int((box.root / ".dv/child-pid").read_text())
        import subprocess

        status = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
        )
        self.assertTrue(
            not status.stdout.strip() or status.stdout.strip().startswith("Z"), status.stdout
        )

    def test_launch_failure_is_not_red(self):
        box = self.box(command=["dv-nonexistent-test-executable"])
        index = box.item.repo.index()
        output = redgreen.run(box.item)
        self.assertEqual(output["red"]["status"], "launch_error")
        self.assertFalse(output["ok"])
        self.assert_restored(box, index)

    def test_signal_is_not_red(self):
        box = self.box(
            command=[sys.executable, "-c", "import os,signal; os.kill(os.getpid(),signal.SIGTERM)"]
        )
        output = redgreen.run(box.item)
        self.assertEqual(output["red"]["exit_code"], -signal.SIGTERM)
        self.assertFalse(output["ok"])

    def test_interrupt_restores_and_preserves_index(self):
        box = self.box()
        index = box.item.repo.index()
        with patch("dv.redgreen.process.run", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                redgreen.run(box.item)
        self.assert_restored(box, index)

    def test_failed_revert_still_restores_all_paths(self):
        box = self.box(new={"src/added.ts": "new\n"})
        item = box.item
        original = redgreen.restore_path

        def fail_once(current, ref, path):
            if ref == item.base and path == "src/value.ts":
                raise DVError("Simulated Git failure", 3)
            return original(current, ref, path)

        with patch("dv.redgreen.restore_path", side_effect=fail_once):
            with self.assertRaises(DVError):
                redgreen.run(item)
        self.assertEqual((box.root / "src/added.ts").read_text(), "new\n")
        self.assert_restored(box, item.repo.index())

    def test_restore_failure_keeps_recovery_plan_and_skips_green(self):
        box = self.box()
        item = box.item
        original = redgreen.restore_path

        def fail_restore(current, ref, path):
            if ref == item.head:
                raise DVError("Simulated restore failure", 3)
            return original(current, ref, path)

        with patch("dv.redgreen.restore_path", side_effect=fail_restore):
            with self.assertRaises(DVError) as raised:
                redgreen.run(item)
        self.assertEqual(raised.exception.code, "restoration_failed")
        self.assertTrue((box.root / ".dv/restoration.json").exists())
        self.assertFalse((box.root / ".dv/redgreen-green.log").exists())

    def test_test_created_files_are_not_deleted(self):
        box = self.box(
            command=[
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; Path('evidence.txt').write_text('keep')"
                    "; raise SystemExit(1)"
                ),
            ]
        )
        output = redgreen.run(box.item)
        self.assertFalse(output["ok"])
        self.assertEqual((box.root / "evidence.txt").read_text(), "keep")
        self.assertEqual((box.root / "src/value.ts").read_text(), "fixed\n")
        self.assertIsNone(output["green"])

    def test_green_mutations_fail_and_remain_for_inspection(self):
        code = (
            "from pathlib import Path; p=Path('src/value.ts'); good=p.read_tex"
            "t().strip()=='fixed'; "
        )
        code += "p.write_text('mutated') if good else None; raise SystemExit(0 if good else 1)"
        box = self.box(command=[sys.executable, "-c", code])
        output = redgreen.run(box.item)
        self.assertFalse(output["ok"])
        self.assertEqual((box.root / "src/value.ts").read_text(), "mutated")

    def test_argv_is_not_implicitly_shell_expanded(self):
        box = self.box(
            command=[
                sys.executable,
                "-c",
                "import sys; assert sys.argv[1] == '$HOME;echo hi'",
                "$HOME;echo hi",
            ]
        )
        output = redgreen.run(box.item)
        self.assertEqual(output["red"]["exit_code"], 0)

    def test_rust_inline_proof_is_refused(self):
        box = self.box(
            new={
                "src/value.rs": (
                    "pub fn value() {}\n#[test]\nfn test_value() { assert_eq!(1, 1); }\n"
                )
            }
        )
        with self.assertRaises(DVError) as raised:
            redgreen.run(box.item)
        self.assertEqual(raised.exception.code, "mixed_test_file")

    def test_explicit_production_file_cannot_retain_fix(self):
        box = self.box()
        box.update(test_files=["tests/test_value.py", "src/value.ts"])
        with self.assertRaises(DVError) as raised:
            redgreen.run(box.item)
        self.assertEqual(raised.exception.code, "mixed_test_file")

    def test_explicit_custom_python_regression_test(self):
        box = self.box(new={"checks/verify.py": "def test_value():\n    assert 1 == 1\n"})
        box.update(test_files=["tests/test_value.py", "checks/verify.py"])
        output = redgreen.run(box.item)
        self.assertIn("checks/verify.py", output["retained_tests"])
        self.assertNotIn("checks/verify.py", output["reverted_paths"])

    def test_concurrent_mutation_is_rejected(self):
        box = self.box()
        with box.item.repo.lock():
            with self.assertRaises(DVError) as raised:
                redgreen.run(box.item)
        self.assertEqual(raised.exception.code, "locked")
