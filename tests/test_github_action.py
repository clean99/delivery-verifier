from __future__ import annotations

import copy
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import Sandbox, git

from dv import github_action as action
from dv import prove


def passing_proof():
    return {
        "schema_version": 1, "command": "prove", "ok": True,
        "state": "complete", "exit_code": 0,
        "red": {"exit_code": 1, "status": "normal", "report_complete": True},
        "green": {"exit_code": 0, "status": "normal", "report_complete": True},
        "checks": [
            {"check": "red assertion", "outcome": "pass", "detail": "Assertion failure"},
            {"check": "green execution", "outcome": "pass", "detail": "One passed test"},
        ],
    }


class GithubActionTests(unittest.TestCase):
    def test_release_url_is_pinned_and_local_means_action_source(self):
        source = Path("/action source")
        url = (
            "https://github.com/clean99/delivery-verifier/releases/download/"
            "v0.2.0/delivery_verifier-0.2.0-py3-none-any.whl"
        )
        for version in ("0.2.0", "v0.2.0"):
            self.assertEqual(action.install_source(version, source), url)
        self.assertEqual(action.install_source("local", source), str(source))
        for version in ("", "latest", "../../other", "--editable=.", "1.0.0; touch marker",
                        "$(touch marker)", "https://example.invalid/tool.whl", "0.2.0\n"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                action.install_source(version, source)

    def test_summary_requires_successful_complete_proof_and_both_checks(self):
        proof = passing_proof()
        report = action.summarize(proof, 0)
        self.assertEqual(report["verdict"], "PASS")
        self.assertTrue(report["ok"])
        self.assertEqual(report["red"], "pass (exit 1, normal)")
        self.assertEqual(report["green"], "pass (exit 0, normal)")
        for field, value in (("ok", False), ("ok", "true"), ("state", "running"),
                             ("exit_code", 3), ("exit_code", False), ("red", None),
                             ("green", None), ("checks", []), ("command", "gate"),
                             ("schema_version", 2), ("error", {"message": "cleanup failed"})):
            with self.subTest(field=field, value=value):
                invalid = dict(proof, **{field: value})
                self.assertFalse(action.summarize(invalid, 0)["ok"])
        for code in (1, 2, 3, 130, -9):
            self.assertFalse(action.summarize(proof, code)["ok"])
        for phase in ("red", "green"):
            for field, value in (("status", "timeout"), ("report_complete", False),
                                 ("exit_code", None), ("exit_code", True)):
                invalid = copy.deepcopy(proof)
                invalid[phase][field] = value
                self.assertFalse(action.summarize(invalid, 0)["ok"])

    def test_failures_and_warnings_are_not_confused(self):
        proof = passing_proof()
        proof["checks"].append({"check": "previous recovery", "outcome": "warn"})
        self.assertTrue(action.summarize(proof, 0)["ok"])
        proof["checks"].append({"check": "cleanup", "outcome": "fail", "detail": "Leftover tree"})
        report = action.summarize(proof, 0)
        self.assertFalse(report["ok"])
        self.assertIn("Leftover tree", report["reason"])
        proof = passing_proof()
        proof["red"]["exit_code"] = 0
        proof["checks"][0].update(outcome="fail", detail="Red passed without the fix")
        report = action.summarize(proof, 1)
        self.assertFalse(report["ok"])
        self.assertEqual(report["red"], "fail (exit 0, normal)")
        self.assertIn("Red passed without the fix", report["reason"])

    def test_malformed_or_ambiguous_results_fail_closed(self):
        for proof in (None, [], "PASS", {"checks": None}, {"checks": [1]},
                      {"checks": [{"outcome": "skip"}]}):
            with self.subTest(proof=proof), self.assertRaises(ValueError):
                action.summarize(proof, 0)
        proof = passing_proof()
        proof["checks"].append(proof["checks"][0])
        self.assertFalse(action.summarize(proof, 0)["ok"])
        error = {"ok": False, "error": {"code": "invalid_input", "message": "Bad base"}}
        report = action.summarize(error, 2)
        self.assertEqual(report["red"], "not run")
        self.assertEqual(report["reason"], "Bad base")

    def test_summary_escapes_messages_and_outputs_have_delimiters(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = action.summarize(passing_proof(), 0)
            report["reason"] = '</pre><script>alert(1)</script>\n::error::untrusted\n[link](url)'
            action.publish(report, root, root / "outputs", root / "summary", "unique-id")
            summary = (root / "summary").read_text()
            self.assertIn("## dv prove: PASS", summary)
            self.assertIn("&lt;script&gt;", summary)
            self.assertNotIn("<script>", summary)
            lines = (root / "outputs").read_text().splitlines()
            values = {}
            for index in range(0, len(lines), 3):
                key, delimiter = lines[index].split("<<")
                self.assertEqual(lines[index + 2], delimiter)
                values[key] = lines[index + 1]
            self.assertEqual(values, {"ok": "true", "verdict": "PASS", "evidence-path": str(root),
                                      "artifact-id": "unique-id"})
            self.assertEqual(json.loads((root / "action-result.json").read_text()), report)

    def test_fetch_base_uses_origin_and_rejects_missing_or_shallow_history(self):
        box = Sandbox()
        self.addCleanup(box.close)
        with tempfile.TemporaryDirectory() as temp:
            remote = Path(temp) / "remote.git"
            git(box.root, "clone", "--bare", str(box.root), str(remote))
            git(box.root, "remote", "add", "origin", str(remote))
            expected = git(box.root, "rev-parse", "main")
            before = box.head
            for base in ("", "main", "refs/heads/main"):
                self.assertEqual(action.fetch_base(box.root.resolve(), base, "main"), expected)
            self.assertEqual(box.head, before)
            for base in ("nonexistent", "main:refs/heads/other", "$(touch marker)",
                         "--upload-pack=x"):
                with self.subTest(base=base), self.assertRaises(ValueError):
                    action.fetch_base(box.root.resolve(), base, "main")
            with self.assertRaisesRegex(ValueError, "repository root"):
                action.fetch_base((box.root / "tests").resolve(), "main", "")
            shallow = Path(temp) / "shallow"
            git(box.root, "clone", "--depth=1", remote.as_uri(), str(shallow))
            with self.assertRaisesRegex(ValueError, "fetch-depth: 0"):
                action.fetch_base(shallow.resolve(), "main", "")

    def test_branch_tag_collision_cannot_prove_a_test_only_change(self):
        box = Sandbox(
            old={"words.py": 'def split_words(text):\n    return text.split(" ")\n'},
            new={"words.py": "def split_words(text):\n    return text.split()\n"},
        )
        self.addCleanup(box.close)
        buggy = git(box.root, "rev-parse", "refs/heads/main")
        fixed = box.head
        git(box.root, "branch", "-f", "main", fixed)
        box.write("tests/test_words.py", (
            "import unittest\nfrom words import split_words\n"
            "class WordsTests(unittest.TestCase):\n"
            "    def test_repeated_spaces(self):\n"
            '        self.assertEqual(split_words("red   green"), ["red", "green"])\n'
        ))
        box.commit("Add only a regression test")
        command = shlex.join([sys.executable, "-m", "unittest", "discover", "-s", "tests",
                              "-p", "test_words.py", "-v"])
        with tempfile.TemporaryDirectory() as temp:
            remote = Path(temp) / "remote.git"
            git(box.root, "clone", "--bare", str(box.root), str(remote))
            git(remote, "tag", "main", buggy)
            git(box.root, "remote", "add", "origin", str(remote))
            for base in ("main", "refs/heads/main", ""):
                with self.subTest(base=base):
                    selected = action.fetch_base(box.root.resolve(), base, "main")
                    with patch("dv.prove.Path.cwd", return_value=box.root):
                        proof = prove.run(command, base_ref=selected)
                    self.assertFalse(proof["ok"], proof)
                    self.assertEqual(selected, fixed)
                    self.assertEqual(proof["base_tip_sha"], fixed)
                    self.assertIn("production difference", proof["error"]["message"])
                    self.assertIsNone(proof["red"])

    def test_missing_origin_branch_does_not_fall_back_to_a_tag_or_commit(self):
        box = Sandbox()
        self.addCleanup(box.close)
        with tempfile.TemporaryDirectory() as temp:
            remote = Path(temp) / "remote.git"
            git(box.root, "clone", "--bare", str(box.root), str(remote))
            git(remote, "tag", "missing", box.head)
            git(box.root, "remote", "add", "origin", str(remote))
            for base in ("missing", "refs/heads/missing", box.head):
                with self.subTest(base=base), self.assertRaisesRegex(ValueError, "base branch"):
                    action.fetch_base(box.root.resolve(), base, "main")
            for base in ("refs/tags/missing", "refs/remotes/origin/main", "refs/pull/1/head"):
                with self.subTest(base=base), self.assertRaisesRegex(ValueError, "refs/heads/"):
                    action.fetch_base(box.root.resolve(), base, "main")

    def test_leading_dash_branches_are_rejected_before_fetch(self):
        box = Sandbox()
        self.addCleanup(box.close)
        with tempfile.TemporaryDirectory() as temp:
            remote = Path(temp) / "remote.git"
            git(box.root, "clone", "--bare", str(box.root), str(remote))
            git(box.root, "remote", "add", "origin", str(remote))
            for name in ("--help", "-base"):
                git(remote, "update-ref", f"refs/heads/{name}", box.head)
            for branch in ("--help", "-base", "refs/heads/--help", "refs/heads/-base"):
                for base, default in ((branch, "main"), ("", branch)):
                    with self.subTest(base=base, default=default), patch.object(
                        action, "git", wraps=action.git
                    ) as calls:
                        with self.assertRaisesRegex(ValueError, "must not begin with '-'"):
                            action.fetch_base(box.root.resolve(), base, default)
                        self.assertFalse(any(call.args[1] == "fetch"
                                             for call in calls.call_args_list))

    def test_slash_branch_is_fetched_in_the_heads_namespace(self):
        box = Sandbox()
        self.addCleanup(box.close)
        git(box.root, "branch", "release/topic", box.head)
        with tempfile.TemporaryDirectory() as temp:
            remote = Path(temp) / "remote.git"
            git(box.root, "clone", "--bare", str(box.root), str(remote))
            git(box.root, "remote", "add", "origin", str(remote))
            for base in ("release/topic", "refs/heads/release/topic", ""):
                self.assertEqual(action.fetch_base(box.root.resolve(), base, "release/topic"),
                                 box.head)

    def test_evidence_copy_requires_owned_path_and_matching_saved_proof(self):
        box = Sandbox()
        self.addCleanup(box.close)
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp)
            source = box.root / ".git/dv/proofs" / ("a" * 32)
            source.mkdir(parents=True)
            proof = dict(passing_proof(), evidence_dir=str(source))
            (source / "proof.json").write_text(json.dumps(proof))
            for name in ("red.log", "green.log"):
                (source / name).write_text(name)
            action.collect_evidence(proof, box.root, destination)
            self.assertEqual((destination / "green.log").read_text(), "green.log")
            with self.assertRaisesRegex(ValueError, "unexpected"):
                action.collect_evidence(dict(proof, evidence_dir=temp), box.root, destination)
            (source / "red.log").unlink()
            (source / "red.log").symlink_to(destination / "green.log")
            with self.assertRaisesRegex(ValueError, "regular files"):
                action.collect_evidence(proof, box.root, destination)
            (source / "red.log").unlink()
            (source / "red.log").write_text("red")
            (source / "proof.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "differs"):
                action.collect_evidence(proof, box.root, destination)

    def test_early_failure_still_publishes_failure_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = {"RUNNER_TEMP": temp, "DV_TEST": "", "DV_TIMEOUT": "60",
                   "GITHUB_OUTPUT": str(root / "outputs"),
                   "GITHUB_STEP_SUMMARY": str(root / "summary")}
            with patch.dict(os.environ, env):
                action.main()
            report = json.loads(next(root.glob("dv-evidence-*/action-result.json")).read_text())
            self.assertFalse(report["ok"])
            self.assertIn("nonblank", report["reason"])
            self.assertIn("FAIL", (root / "summary").read_text())

    def test_final_shell_gate_fails_for_missing_or_false_output(self):
        for ok in ("true", "false", "", "true\ntrue", '$(touch marker)'):
            result = subprocess.run(["bash", "-c", 'test "$DV_OK" = true'],
                                    env=dict(os.environ, DV_OK=ok), capture_output=True)
            self.assertEqual(result.returncode == 0, ok == "true")


if __name__ == "__main__":
    unittest.main()
