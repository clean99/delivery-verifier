"""Fault injection for the owned snapshot lifecycle, independent of acceptance IDs."""

from __future__ import annotations

import json
import os
import shlex
import unittest
from pathlib import Path
from unittest.mock import patch

import test_first_usable_spec as spec
from support import git
from test_first_usable_spec import COMMAND, TEST

from dv import prove
from dv.common import DVError
from dv.snapshots import LocalRepo, Snapshots


class SnapshotFaultTests(unittest.TestCase):
    def setUp(self):
        self.box = spec.FirstUsableProofSpec()
        self.box.setUp()
        self.addCleanup(self.box.doCleanups)
        self.box.fixture()

    def run_proof(self):
        with (
            patch("dv.prove.Path.cwd", return_value=self.box.root),
            patch.dict(os.environ, self.box.env),
        ):
            return prove.run(shlex.join(COMMAND))

    def test_partial_creation_removes_only_owned_registration(self):
        before = self.box.state()
        original = Snapshots.create

        def fail_after_create(snapshots, *args):
            original(snapshots, *args)
            raise OSError("injected failure after registration")

        with patch.object(Snapshots, "create", fail_after_create):
            output = self.run_proof()
        self.assertFalse(output["ok"])
        self.assertEqual(output["exit_code"], 3)
        self.box.assert_cleaned(before)
        self.box.evidence(output)

    def test_cleanup_failure_preserves_recovery_and_cannot_publish_success(self):
        original = LocalRepo.run

        def fail_remove(repo, *args, **kwargs):
            if args[:2] == ("worktree", "remove"):
                raise DVError("injected removal failure", 3)
            return original(repo, *args, **kwargs)

        with patch.object(LocalRepo, "run", fail_remove):
            output = self.run_proof()
        self.assertFalse(output["ok"])
        self.assertEqual(output["exit_code"], 3)
        directory = self.box.evidence(output)
        recovery = json.loads((directory / "recovery.json").read_text())
        self.assertEqual(set(output["remaining_paths"]), set(recovery["worktrees"]))
        following = self.run_proof()
        self.assertTrue(following["ok"], following)
        self.assertIn("previous recovery", str(following["checks"]))
        for path in recovery["worktrees"]:
            LocalRepo(self.box.root).run("worktree", "remove", "--force", path)

    def test_tracked_file_and_index_mutations_fail_proof(self):
        for mutation in (
            "Path('value.py').write_text('changed by test')",
            "subprocess.run(['git', 'add', 'value.py'], check=True)\n"
            "        subprocess.run(['git', 'rm', '--cached', 'value.py'], check=True)",
        ):
            with self.subTest(mutation=mutation):
                source = TEST.replace(
                    "        self.assertEqual", "        " + mutation + "\n        self.assertEqual"
                )
                self.box.write("tests/test_value.py", source)
                self.box.commit("test: inject tracked input mutation")
                before = self.box.state()
                output = self.box.prove(expected=1)
                self.assertIn("mutations", str(output["checks"]))
                self.assertTrue((Path(output["evidence_dir"]) / "red-mutations.json").is_file())
                self.box.assert_cleaned(before)

    def test_checkout_hooks_and_smudge_filters_never_run(self):
        self.box.write(".gitattributes", "*.py filter=proof-test\n")
        self.box.commit("test: define a checkout filter")
        marker = self.box.directory / "filter-started"
        hook_dir = self.box.directory / "hooks"
        hook_dir.mkdir()
        hook = hook_dir / "post-checkout"
        hook.write_text("#!/bin/sh\ntouch " + shlex.quote(str(marker)) + "\n")
        hook.chmod(0o755)
        git(self.box.root, "config", "core.hooksPath", str(hook_dir))
        git(
            self.box.root,
            "config",
            "filter.proof-test.smudge",
            "touch " + shlex.quote(str(marker)) + "; cat",
        )
        before = self.box.state()
        self.box.prove()
        self.assertFalse(marker.exists())
        self.box.assert_cleaned(before)

    def test_committed_policy_ignores_dirty_checkout_policy(self):
        self.box.write(".dv.toml", "malformed local policy [\n")
        before = self.box.state()
        self.box.prove()
        self.box.assert_cleaned(before)

    def test_modes_support_files_and_base_only_tests(self):
        git(self.box.root, "switch", "main")
        self.box.write("tests/test_obsolete.py", "raise RuntimeError('base-only test')\n")
        self.box.commit("test: add obsolete baseline test")
        git(self.box.root, "switch", "fix")
        git(
            self.box.root,
            "-c",
            "user.name=clean99",
            "-c",
            "user.email=53188310+clean99@users.noreply.github.com",
            "merge",
            "main",
            "--no-commit",
        )
        self.box.commit("test: merge baseline support")
        (self.box.root / "tests/test_obsolete.py").unlink()
        self.box.write("tests/helper.txt", "fixture support\n")
        test = TEST.replace(
            "        self.assertEqual",
            "        self.assertEqual(\n"
            "            Path('tests/helper.txt').read_text(), 'fixture support\\n')\n"
            "        self.assertTrue(Path('tests/test_value.py').stat().st_mode & 0o111)\n"
            "        self.assertEqual",
        )
        self.box.write("tests/test_value.py", test)
        (self.box.root / "tests/test_value.py").chmod(0o755)
        self.box.commit("test: retain executable regression and support")
        output = self.box.prove()
        self.assertEqual(output["retained_tests"]["tests/test_value.py"]["mode"], "100755")
        self.assertIn("tests/helper.txt", output["retained_tests"])
        self.assertNotIn("tests/test_obsolete.py", output["retained_tests"])

    def test_cleanup_refuses_replaced_worktree_symlink(self):
        other = self.box.directory / "unrelated-worktree"
        git(self.box.root, "worktree", "add", "--detach", str(other), self.box.base)
        before = self.box.state()
        directory = self.box.directory / "owned-attempt"
        directory.mkdir()
        owned = directory / "red"
        owned.symlink_to(other, target_is_directory=True)
        snapshots = Snapshots(LocalRepo(self.box.root), directory)
        snapshots.owned.append(owned)
        snapshots.save_recovery()
        self.assertEqual(snapshots.cleanup(), [str(owned)])
        self.assertTrue((other / ".git").exists())
        self.assertEqual(self.box.state(), before)
        self.assertTrue(snapshots.recovery.exists())

    def test_materialization_never_overwrites_existing_admin_file(self):
        from dv.snapshots import Entry

        directory = self.box.directory / "owned-attempt"
        directory.mkdir()
        snapshots = Snapshots(LocalRepo(self.box.root), directory)
        oid = git(self.box.root, "rev-parse", "HEAD:value.py")
        with self.assertRaises(FileExistsError):
            snapshots.create("red", self.box.base, {".git": Entry("100644", oid)})
        self.assertTrue((directory / "red/.git").read_text().startswith("gitdir: "))
        self.assertEqual(snapshots.cleanup(), [])

    def test_lfs_pointer_rejected_without_hydration(self):
        self.box.write(
            "asset.bin",
            "version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 12\n",
        )
        self.box.commit("test: add unsupported LFS pointer")
        before = self.box.state()
        output = self.box.prove(expected=2)
        self.assertEqual(output["error"]["code"], "unsupported_path")
        self.box.assert_cleaned(before)
