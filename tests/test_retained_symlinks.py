"""Retained links must not import phase-specific inputs into the test overlay."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_first_usable_spec as spec
from support import git

from dv.common import DVError
from dv.snapshots import Entry, retained_inputs


class RetainedSymlinkTests(unittest.TestCase):
    def setUp(self):
        self.path = "tests/fixture.txt"
        self.link = Entry("120000", "../data/value.txt")
        self.old = Entry("100644", "old")
        self.new = Entry("100644", "new")
        self.base = {self.path: self.link, "data/value.txt": self.old, "value.py": self.old}
        self.head = {**self.base, self.path: Entry(self.link.mode, self.link.oid),
                     "value.py": self.new, "tests/test_value.py": self.new}

    def retain(self):
        with patch("dv.snapshots.content", side_effect=lambda repo, entry: entry.oid.encode()):
            return retained_inputs(None, self.base, self.head, {}, ())

    def reject(self, message):
        with self.assertRaisesRegex(DVError, message) as failure:
            self.retain()
        self.assertEqual(failure.exception.exit_code, 2)
        self.assertEqual(failure.exception.code, "unsupported_path")
        self.assertIn(self.path, str(failure.exception))

    def target(self, destination):
        self.base[self.path] = self.head[self.path] = Entry("120000", destination)

    def test_unchanged_link_keeps_base_entry_without_copying_target(self):
        red, retained = self.retain()
        self.assertIs(red[self.path], self.base[self.path])
        self.assertIsNot(red[self.path], self.head[self.path])
        self.assertEqual(retained[self.path], self.link)
        self.assertEqual(red["value.py"], self.old)
        self.assertEqual(red["data/value.txt"], self.old)

    def test_changed_target_rejected_even_if_both_targets_exist(self):
        self.base["data/other.txt"] = self.head["data/other.txt"] = self.old
        self.head[self.path] = Entry("120000", "../data/other.txt")
        self.reject("symlink was changed")

    def test_added_symlink_rejected(self):
        del self.base[self.path]
        self.reject("symlink was added")

    def test_deleted_symlink_rejected(self):
        del self.head[self.path]
        self.reject("symlink was deleted")

    def test_mode_changes_rejected_in_both_directions(self):
        for entries in (self.base, self.head):
            with self.subTest(entries="base" if entries is self.base else "head"):
                entries[self.path] = Entry("100644", self.link.oid)
                self.reject("symlink was changed")
                entries[self.path] = self.link

    def test_escaping_targets_rejected(self):
        for destination in ("/outside/value.txt", "../../outside/value.txt",
                            "../../project/data/value.txt"):
            with self.subTest(destination=destination):
                self.target(destination)
                self.reject("escapes the snapshot")

    def test_changed_production_target_rejected(self):
        self.target("../value.py")
        self.reject("points to a changed production file")

    def test_changed_document_target_rejected(self):
        for entry in (self.new, Entry("100755", self.old.oid)):
            with self.subTest(entry=entry):
                self.head["data/value.txt"] = entry
                self.reject("target differs between snapshots")

    def test_base_only_support_target_cannot_disappear_from_red(self):
        self.target("obsolete.txt")
        self.base["tests/obsolete.txt"] = self.old
        self.reject("tracked regular file")

    def test_changed_retained_target_uses_head_bytes_in_both_phases(self):
        self.target("expected.txt")
        self.base["tests/expected.txt"] = self.old
        self.head["tests/expected.txt"] = self.new
        red, retained = self.retain()
        self.assertEqual(red["tests/expected.txt"], self.new)
        self.assertEqual(retained["tests/expected.txt"], self.new)

    def test_chains_and_loops_rejected(self):
        for destination, other in (("fixture.txt", "../data/value.txt"),
                                   ("other.txt", "fixture.txt"),
                                   ("other.txt", "../data/value.txt")):
            with self.subTest(destination=destination, other=other):
                self.target(destination)
                self.base["tests/other.txt"] = self.head["tests/other.txt"] = Entry("120000", other)
                self.reject("chain or loop")

    def test_symlink_components_checked_before_parent_traversal(self):
        self.target("../alias/../data/value.txt")
        self.base["alias/file"] = self.head["alias/file"] = self.old
        for entries in (self.base, self.head):
            with self.subTest(entries="base" if entries is self.base else "head"):
                del entries["alias/file"]
                entries["alias"] = Entry("120000", "data")
                self.reject("chain or loop")
                del entries["alias"]
                entries["alias/file"] = self.old

    def test_directory_dangling_and_non_directory_targets_rejected(self):
        for destination, message in (
            ("../data", "tracked regular file"),
            ("../missing", "tracked regular file"),
            ("../missing/../data/value.txt", "non-directory"),
            ("../data/value.txt/../value.txt", "non-directory"),
            ("../data/value.txt/", "non-directory"),
            ("", "invalid target"),
            ("../data/\0value.txt", "invalid target"),
        ):
            with self.subTest(destination=destination):
                self.target(destination)
                self.reject(message)

    def test_explicit_test_file_still_must_be_regular(self):
        with self.assertRaisesRegex(DVError, "Test file must be regular"):
            retained_inputs(None, self.base, self.head, {}, [self.path])


@unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is optional runner tooling")
class SymlinkProofTests(unittest.TestCase):
    def setUp(self):
        self.box = spec.FirstUsableProofSpec()
        self.box.setUp()
        self.addCleanup(self.box.doCleanups)
        self.box.env.pop("PYTHONPATH", None)
        git(self.box.root, "init", "-b", "main")
        self.box.write(".gitignore", "__pycache__/\n.pytest_cache/\n")
        self.box.write("value.py", "def value():\n    return 21\n")
        self.box.write("data/value.txt", "unchanged fixture\n")
        self.path = "js/packages/tool/test/fixtures/value.txt"
        link = self.box.root / self.path
        link.parent.mkdir(parents=True)
        self.destination = "../../../../../data/value.txt"
        link.symlink_to(self.destination)
        self.box.commit("test: baseline with unrelated fixture link")
        git(self.box.root, "switch", "-c", "fix")
        self.box.write("value.py", "def value():\n    return 42\n")
        self.box.write("tests/test_value.py", spec.TEST)
        self.box.write("tests/test_hollow.py", spec.TEST.replace(
            "self.assertEqual(value(), 42)", "self.assertIsNotNone(value())"))
        self.box.commit("test: fix with distinguishing and hollow tests")
        self.box.env["TMPDIR"] = str(self.box.directory / "execution-temp")
        Path(self.box.env["TMPDIR"]).mkdir()

    def test_real_pytest_proof_accepts_regression_but_rejects_hollow_test(self):
        before = self.box.state()
        for filename, expected, red_exit in (("test_value.py", 0, 1), ("test_hollow.py", 1, 0)):
            with self.subTest(filename=filename):
                output = self.box.prove(command=[sys.executable, "-m", "pytest", "--tb=long",
                                                 "tests/" + filename], expected=expected)
                self.assertEqual(output["state"], "complete")
                self.assertEqual(output["red"]["exit_code"], red_exit)
                self.assertEqual(output["green"]["exit_code"], 0)
                self.assertEqual(output["green"]["tests"]["passed"], 1)
                self.assertEqual(output["retained_tests"][self.path], {
                    "mode": "120000",
                    "sha256": hashlib.sha256(self.destination.encode()).hexdigest(),
                })
                self.box.evidence(output)
                self.box.assert_cleaned(before)
                self.assertEqual(os.readlink(self.box.root / self.path), self.destination)

    def test_real_proof_rejects_changed_link_before_either_phase(self):
        link = self.box.root / self.path
        link.unlink()
        link.symlink_to("../../../../../value.py")
        self.box.commit("test: retarget fixture to changed production")
        before = self.box.state()
        output = self.box.prove(expected=2)
        self.assertIn("symlink was changed", output["error"]["message"])
        self.assertIsNone(output["red"])
        self.assertIsNone(output["green"])
        self.box.evidence(output)
        self.box.assert_cleaned(before)
