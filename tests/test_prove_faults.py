"""Fault injection for the owned snapshot lifecycle, independent of acceptance IDs."""

from __future__ import annotations

import errno
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import sysconfig
import unittest
import venv
from pathlib import Path
from unittest.mock import patch

import test_first_usable_spec as spec
from support import Sandbox, git
from test_first_usable_spec import COMMAND, TEST

from dv import prove
from dv.common import DVError
from dv.snapshots import (
    DependencyCopier,
    Entry,
    LocalRepo,
    Snapshots,
    clone_file,
    retained_inputs,
)


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
        Path(recovery["execution_root"]).rmdir()

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

    def test_inherited_git_environment_cannot_redirect_to_callers_index(self):
        source = TEST.replace(
            "        self.assertEqual",
            "        subprocess.run(['git', 'update-index', '--assume-unchanged', "
            "'value.py'], check=True)\n        self.assertEqual",
        )
        self.box.write("tests/test_value.py", source)
        self.box.commit("test: change index from regression command")
        self.box.write("value.py", "staged caller content\n")
        git(self.box.root, "add", "value.py")
        self.box.write("value.py", "unstaged caller content\n")
        admin = self.box.root / ".git"
        for inherited in (
            {"GIT_INDEX_FILE": str(admin / "index")},
            {
                "GIT_INDEX_FILE": str(admin / "index"),
                "GIT_DIR": str(admin),
                "GIT_COMMON_DIR": str(admin),
                "GIT_WORK_TREE": str(self.box.root),
                "GIT_OBJECT_DIRECTORY": str(admin / "objects"),
            },
        ):
            with self.subTest(variables=list(inherited)), patch.dict(self.box.env, inherited):
                before = self.box.state()
                output = self.box.prove(expected=1)
                self.assertEqual(output["red"]["exit_code"], 1)
                self.assertEqual(output["green"]["exit_code"], 0)
                for phase in ("red", "green"):
                    mutations = Path(output["evidence_dir"]) / f"{phase}-mutations.json"
                    self.assertTrue(json.loads(mutations.read_text())["index_changed"])
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
        self.assertTrue((snapshots.owned[0] / ".git").read_text().startswith("gitdir: "))
        self.assertEqual(snapshots.cleanup(), [])

    @unittest.skipUnless(shutil.which("node"), "Node required")
    def test_node_dependencies_are_copied_without_writes_into_the_caller(self):
        box = Sandbox(old={"src/value.mjs": "export const value = 0;"}, new={
            "src/value.mjs": "export const value = 1;",
            "tests/value.test.mjs": "import test from 'node:test'; "
            "import assert from 'node:assert/strict'; import {writeFileSync} from 'node:fs'; "
            "import increment from 'sample-dependency'; import {value} from '../src/value.mjs';"
            "test('dependency',()=>{writeFileSync(new URL('../node_modules/sample-dependency/"
            "touched',import.meta.url),'owned'); assert.equal(increment(value),2)});",
        })
        self.addCleanup(box.close)
        box.write("node_modules/sample-dependency/package.json",
                  '{"name":"sample-dependency","type":"module","main":"index.js"}')
        box.write("node_modules/sample-dependency/index.js", "export default value => value + 1;")
        before = git(box.root, "status", "--porcelain")
        proc = subprocess.run([sys.executable, "-m", "dv", "prove", "--test",
                               "node --test tests/value.test.mjs", "--json"],
                              cwd=box.root, env=self.box.env, capture_output=True,
                              text=True, timeout=60)
        output = json.loads(proc.stdout)
        self.assertTrue(output["ok"], output)
        self.assertEqual(git(box.root, "status", "--porcelain"), before)
        self.assertFalse((box.root / "node_modules/sample-dependency/touched").exists())
        self.assertFalse((Path(output["evidence_dir"]) / "recovery.json").exists())

    def test_python_proofs_ignore_unrelated_external_node_links(self):
        box = Sandbox(old={"pkg/__init__.py": "VALUE = 1\n"}, new={
            "pkg/__init__.py": "VALUE = 2\n",
            "tests/test_import.py": "import unittest\nimport pkg\n"
            "class ValueTests(unittest.TestCase):\n"
            "    def test_value(self):\n        self.assertEqual(pkg.VALUE, 2)\n",
        })
        self.addCleanup(box.close)
        external = self.box.directory / "external-node-package"
        external.mkdir()
        (external / "package.json").write_text('{"name":"external-package"}')
        modules = box.root / "node_modules"
        modules.mkdir()
        (modules / "external").symlink_to(external, target_is_directory=True)
        before = git(box.root, "status", "--porcelain")
        for runner in ("pytest", "unittest"):
            command = [sys.executable, "-m", runner]
            command += (["--tb=long", "tests/test_import.py"] if runner == "pytest" else
                        ["discover", "-s", "tests", "-p", "test_import.py"])
            with self.subTest(runner=runner), patch("dv.prove.Path.cwd", return_value=box.root), \
                    patch.dict(os.environ, self.box.env), \
                    patch.object(Snapshots, "copy_dependencies", side_effect=AssertionError()):
                output = prove.run(shlex.join(command))
            self.assertTrue(output["ok"], output)
            self.assertEqual(git(box.root, "status", "--porcelain"), before)
            self.assertTrue((modules / "external").is_symlink())

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest required")
    def test_project_virtualenv_commands_preserve_released_behavior(self):
        box = Sandbox(old={"pkg/__init__.py": "VALUE = 1\n",
            ".gitignore": "__pycache__/\n.venv/\n",
            "pyproject.toml": '[tool.pytest.ini_options]\npythonpath=["."]\n'}, new={
            "pkg/__init__.py": "VALUE = 2\n", "tests/test_import.py":
            "import unittest\nimport pkg\nclass ValueTests(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(pkg.VALUE, 2)\n",
        })
        self.addCleanup(box.close)
        environment = box.root / ".venv"
        venv.EnvBuilder(with_pip=False).create(environment)
        python = environment / "bin/python"
        purelib = subprocess.check_output([str(python), "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip()
        Path(purelib, "test-tooling.pth").write_text(sysconfig.get_path("purelib") + "\n")
        pytest = environment / "bin/pytest"
        pytest.write_text(f"#!{python}\nfrom pytest import console_main\n"
                          "raise SystemExit(console_main())\n")
        pytest.chmod(0o755)
        commands = {
            "python-unittest": [str(python), "-m", "unittest", "discover", "-s", "tests"],
            "python-pytest": [str(python), "-m", "pytest", "--tb=long", "tests/test_import.py"],
            "pytest": [str(pytest), "--tb=long", "tests/test_import.py"],
        }
        commands.update({"path-" + name: [Path(command[0]).name, *command[1:]]
                         for name, command in list(commands.items())})
        sources = {"working": Path(__file__).resolve().parents[1]}
        if os.environ.get("DV_RELEASE_SOURCE"):
            sources["release"] = Path(os.environ["DV_RELEASE_SOURCE"])
        results = []
        for revision, source in sources.items():
            for name, command in commands.items():
                with self.subTest(revision=revision, command=name):
                    env = {**self.box.env, "PYTHONPATH": str(source / "src"),
                           "PATH": str(environment / "bin") + os.pathsep + os.environ["PATH"]}
                    proc = subprocess.run([sys.executable, "-m", "dv", "prove", "--test",
                        shlex.join(command), "--json"], cwd=box.root, env=env,
                        capture_output=True, text=True, timeout=90)
                    output = json.loads(proc.stdout)
                    destination = os.environ.get("DV_VENV_RESULTS")
                    if destination:
                        target = Path(destination) / (revision + "-" + name)
                        target.mkdir(parents=True, exist_ok=True)
                        for artifact in ("proof.json", "red.log", "green.log"):
                            saved = Path(output["evidence_dir"]) / artifact
                            if saved.exists():
                                shutil.copy2(saved, target / artifact)
                        results.append({"revision": revision, "command": name,
                                        "ok": output["ok"], "exit_code": proc.returncode})
                        (Path(destination) / "results.json").write_text(json.dumps(results))
                    self.assertEqual(proc.returncode, 0, output)
                    self.assertTrue(output["ok"], output)
                    for phase in ("red", "green"):
                        self.assertEqual(output[phase]["argv"][0], command[0])
        self.assertEqual(git(box.root, "status", "--porcelain"), "")

    @unittest.skipUnless(shutil.which("node"), "Node required")
    def test_tracked_dependency_placeholders_do_not_suppress_installed_packages(self):
        files = {"node_modules/.gitkeep": "", "node_modules/local-dep/index.js":
                 "module.exports = value => value + 1;"}
        box = Sandbox(old={"value.cjs": "exports.value = 1;", **files}, new={
            "value.cjs": "exports.value = 2;", "tests/value.test.cjs":
            "const test=require('node:test'),assert=require('node:assert/strict');"
            "const increment=require('local-dep'),{value}=require('../value.cjs');"
            "test('value',()=>assert.equal(increment(value),3));",
        })
        self.addCleanup(box.close)
        box.write("node_modules/local-dep/package.json", '{"name":"local-dep","main":"index.js"}')
        box.write("node_modules/local-dep/index.js", "throw Error('caller-only edit');")
        before = git(box.root, "diff")
        proc = subprocess.run([sys.executable, "-m", "dv", "prove", "--test",
                               "node --test tests/value.test.cjs", "--json"], cwd=box.root,
                              env=self.box.env, capture_output=True, text=True, timeout=60)
        output = json.loads(proc.stdout)
        self.assertTrue(output["ok"], output)
        self.assertEqual(git(box.root, "diff"), before)

    def test_tracked_dependency_directory_cannot_expand_an_external_link(self):
        external = self.box.directory / "external-package"
        external.mkdir()
        (external / "index.js").write_text("caller-only source")
        modules = self.box.root / "node_modules"
        modules.mkdir()
        (modules / "linked").symlink_to(external, target_is_directory=True)
        root = self.box.directory / "execution"
        target = root / "node_modules/linked"
        target.mkdir(parents=True)
        (target / ".gitkeep").write_text("")
        snapshots = Snapshots(LocalRepo(self.box.root), self.box.directory)
        with self.assertRaisesRegex(DVError, "outside copied dependencies"):
            snapshots.copy_dependencies(root, {"node_modules/linked/.gitkeep": None})
        self.assertFalse((target / "index.js").exists())
        self.assertTrue((target / ".gitkeep").is_file())

    def test_ambiguous_or_unavailable_caller_command_paths_fail_closed(self):
        root = self.box.directory / "execution"
        root.mkdir()
        snapshots = Snapshots(LocalRepo(self.box.root), self.box.directory)
        for argument in ("--config=" + str(self.box.root / "missing.mjs"),
                         str(self.box.root / "tests/*.js")):
            with self.subTest(argument=argument), self.assertRaisesRegex(DVError, "repo-relative"):
                snapshots.command(["vitest", "run", argument], root)
        external = str(self.box.directory / "global/pytest")
        self.assertEqual(snapshots.command([external, "-k", ""], root), [external, "-k", ""])

    def test_command_remapping_preserves_non_path_option_values(self):
        root = self.box.directory / "execution"
        root.mkdir()
        snapshots = Snapshots(LocalRepo(self.box.root), self.box.directory)
        (self.box.root / "docs").mkdir()
        values = ("docs", str(self.box.root / "src/value.py"),
                  f"literal {self.box.root}/missing-description", "sub/../docs")
        for option in ("-t", "--testNamePattern", "--project"):
            for value in values:
                for args in ([option, value], [option + "=" + value]):
                    command = ["vitest", "run", *args]
                    self.assertEqual(snapshots.command(command, root), command)
        (self.box.root / "config.mjs").write_text("export default {}")
        (root / "config.mjs").write_text("export default {}")
        for option in ("-c", "--config", "-r", "--root", "--dir"):
            for args, expected in (
                ([option, str(self.box.root / "config.mjs")], [option, str(root / "config.mjs")]),
                ([option + "=" + str(self.box.root / "config.mjs")],
                 [option + "=" + str(root / "config.mjs")]),
            ):
                self.assertEqual(snapshots.command(["vitest", "run", *args], root),
                                 ["vitest", "run", *expected])
        node = ["node", "--test", "--test-name-pattern", str(self.box.root / "docs")]
        self.assertEqual(snapshots.command(node, root), node)

    def test_test_support_paths_are_retained_consistently(self):
        before, after = Entry("100644", "before"), Entry("100644", "after")
        names = ("tests/helper.js", "__tests__/helper.py", "test-utils/helper.js",
                 "test_utils/helper.py", "fixtures/value.json", "__mocks__/value.js",
                 "conftest.py", "package/conftest.py")
        base = {name: before for name in names}
        base.update({"src/value.js": before, "fixtures/deleted.json": before})
        head = {name: after for name in names}
        head.update({"src/value.js": after, "tests/value.test.js": after})
        red, retained = retained_inputs(LocalRepo(self.box.root), base, head, {}, ())
        self.assertEqual(red["src/value.js"], before)
        self.assertNotIn("fixtures/deleted.json", red)
        for name in (*names, "tests/value.test.js"):
            self.assertEqual(red[name], after)
            self.assertEqual(retained[name], after)

    def test_support_helpers_are_head_inputs_for_released_runners(self):
        for runner in ("unittest", "pytest", "node-test"):
            if runner == "pytest" and importlib.util.find_spec("pytest") is None:
                self.skipTest("pytest is optional runner tooling")
            for product_fix in (False, True):
                with self.subTest(runner=runner, product_fix=product_fix):
                    common = {"src/value.ts": "broken\n"}
                    if runner == "node-test":
                        helper, product = "test-utils/helper.cjs", "value.cjs"
                        expected = "exports.expected = "
                        value = "exports.value = "
                        test = "const {test}=require('node:test'); " \
                               "const assert=require('node:assert/strict'); " \
                               "const {value}=require('./value.cjs'); " \
                               "const {expected}=require('./test-utils/helper.cjs'); " \
                               "test('value',()=>assert.equal(value,expected));"
                        filename = "value.test.cjs"
                        command = ["node", "--test", "--test-reporter=tap", filename]
                    else:
                        helper = "conftest.py" if runner == "pytest" else "test_utils/helper.py"
                        product, expected, value = "value.py", "expected = ", "value = "
                        module = "conftest" if runner == "pytest" else "test_utils.helper"
                        test = ("import unittest\nfrom value import value\nfrom " + module +
                                " import expected\nclass ValueTest(unittest.TestCase):\n"
                                "    def test_value(self): self.assertEqual(value, expected)\n")
                        filename = "test_value.py"
                        command = [sys.executable, "-m", runner, filename]
                    box = Sandbox(old={**common, helper: expected + "3\n",
                        product: value + ("1\n" if product_fix else "2\n")}, new={
                        **common, helper: expected + "2\n", product: value + "2\n",
                        filename: test})
                    self.addCleanup(box.close)
                    with patch("dv.prove.Path.cwd", return_value=box.root):
                        output = prove.run(shlex.join(command))
                    self.assertEqual(output["exit_code"], 0 if product_fix else 1, output)
                    if product_fix:
                        self.assertIn(helper, output["retained_tests"])
                        self.assertEqual(output["red"]["exit_code"], 1)
                        self.assertEqual(output["green"]["exit_code"], 0)
                    else:
                        self.assertIsNone(output["red"])
                        self.assertIn("production difference", str(output["checks"]))

    def test_vitest_path_operands_cannot_escape_the_repository(self):
        root = self.box.directory / "execution"
        root.mkdir()
        snapshots = Snapshots(LocalRepo(self.box.root), self.box.directory)
        for option in ("--config", "--root", "--dir"):
            command = ["vitest", "run", option, str(self.box.directory / "external")]
            with self.subTest(option=option), self.assertRaisesRegex(DVError, "inside"):
                snapshots.command(command, root)

    def test_worktrees_are_outside_caller_collection_and_removed_after_timeout(self):
        owned = []
        create = Snapshots.create

        def observe(snapshots, *args):
            worktree, cache = create(snapshots, *args)
            owned.append(worktree.root.parent)
            self.assertFalse(worktree.root.is_relative_to(self.box.root))
            self.assertNotIn(".git", worktree.root.parts)
            return worktree, cache

        self.box.write("tests/test_value.py", "import time\ntime.sleep(30)\n")
        self.box.commit("test: block test collection")
        before = self.box.state()
        with patch.object(Snapshots, "create", observe), \
                patch("dv.prove.Path.cwd", return_value=self.box.root), \
                patch.dict(os.environ, self.box.env):
            output = prove.run(shlex.join(COMMAND), timeout=1)
        self.assertFalse(output["ok"], output)
        self.assertEqual(output["red"]["status"], "timeout")
        self.assertTrue(owned)
        self.assertTrue(all(not path.exists() for path in owned))
        self.box.assert_cleaned(before)

    def test_dependency_clones_have_independent_inodes_and_writes(self):
        source = self.box.directory / "dependency.js"
        target = self.box.directory / "copy.js"
        source.write_bytes(b"original")
        source.chmod(0o755)
        copier = DependencyCopier(self.box.directory, [])
        copier(source, target)
        self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertNotEqual(source.stat().st_ino, target.stat().st_ino)
        self.assertEqual(target.stat().st_mode, source.stat().st_mode)
        target.write_bytes(b"changed")
        self.assertEqual(source.read_bytes(), b"original")
        self.assertEqual(copier.cloned_files + copier.copied_files, 1)

    def test_copy_fallback_checks_the_whole_tree_before_writing(self):
        source = self.box.directory / "dependencies"
        source.mkdir()
        for name in ("a.js", "b.js"):
            (source / name).write_bytes(b"x" * 8192)
        target = self.box.directory / "copy.js"
        copier = DependencyCopier(self.box.directory, [source])
        disk = shutil.disk_usage(self.box.directory)
        disk = disk._replace(free=copier.reserve + 8192)
        unsupported = OSError(errno.ENOTSUP, "no cloning")
        with patch("dv.snapshots.clone_file", side_effect=unsupported), \
                patch("dv.snapshots.shutil.disk_usage", return_value=disk):
            with self.assertRaises(DVError) as failure:
                copier(source / "a.js", target)
        self.assertEqual(failure.exception.code, "insufficient_space")
        self.assertFalse(target.exists())
        with patch("dv.snapshots.clone_file", side_effect=unsupported):
            copier(source / "a.js", target)
        self.assertEqual(target.read_bytes(), (source / "a.js").read_bytes())
        self.assertEqual(copier.copied_files, 1)

    def test_clone_permission_errors_do_not_fall_back(self):
        source = self.box.directory / "dependency.js"
        source.write_bytes(b"original")
        copier = DependencyCopier(self.box.directory, [])
        denied = PermissionError(errno.EACCES, "denied")
        with patch("dv.snapshots.clone_file", side_effect=denied), \
                patch("dv.snapshots.shutil.copy2") as copy:
            with self.assertRaises(PermissionError):
                copier(source, self.box.directory / "copy.js")
        copy.assert_not_called()

    def test_linux_reflink_failure_removes_only_the_new_destination(self):
        source = self.box.directory / "dependency.js"
        target = self.box.directory / "copy.js"
        source.write_bytes(b"original")
        with patch("dv.snapshots.sys.platform", "linux"), \
                patch("fcntl.ioctl", side_effect=OSError(errno.ENOTTY, "no reflinks")) as ioctl:
            with self.assertRaises(OSError):
                clone_file(source, target)
        self.assertEqual(ioctl.call_args.args[1], 0x40049409)
        self.assertFalse(target.exists())
        self.assertEqual(source.read_bytes(), b"original")

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
