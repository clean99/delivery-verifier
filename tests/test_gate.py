from __future__ import annotations

import sys
import unittest

from support import REPORT, Sandbox, git

from dv import evidence, gate
from dv.common import DVError, read_json, write_json


class GateTests(unittest.TestCase):
    def box(self, ready=True):
        box = Sandbox()
        self.addCleanup(box.close)
        if ready:
            box.ready()
        return box

    def fails(self, output, name):
        return any(r["check"] == name and r["outcome"] == "fail" for r in output["checks"])

    def review(self, box, reviewer="model-b", verdict="pass", fresh=False):
        report = box.root / ".dv/new-report.txt"
        report.write_text(REPORT)
        return evidence.record_review(box.item, reviewer, box.head, verdict, report, fresh)

    def test_complete_gate_recomputes_and_marks_ready(self):
        box = self.box()
        output = gate.run(box.item)
        self.assertTrue(output["ok"], output)
        self.assertEqual(box.item.meta["status"], "ready")
        self.assertTrue(output["commands"])

    def test_missing_evidence_fails(self):
        output = gate.run(self.box(False).item)
        self.assertFalse(output["ok"])
        self.assertTrue(self.fails(output, "V2 red/green"))
        self.assertTrue(self.fails(output, "V4 independent review"))

    def test_approval_is_required_only_for_delivery(self):
        box = self.box()
        self.assertTrue(gate.run(box.item)["ok"])
        output = gate.run(box.item, True)
        self.assertTrue(self.fails(output, "V5 human approval"))

    def test_approve_and_repeat_gate_do_not_create_hash_cycle(self):
        box = self.box()
        output = evidence.approve(
            box.item, box.head, "Approved this exact change after inspecting the regression."
        )
        self.assertTrue(output["ok"], output)
        approved = (box.root / ".dv/approval.json").read_bytes()
        for _ in range(2):
            output = gate.run(box.item, True)
            self.assertTrue(output["ok"], output)
        self.assertEqual((box.root / ".dv/approval.json").read_bytes(), approved)
        self.assertEqual(box.item.meta["status"], "approved")

    def test_same_model_requires_explicit_fresh_context_and_small_kind(self):
        cases = [
            ("model-a", "model-a", "small", False, "fail"),
            ("MODEL-A (fresh context)", "model-a", "small", False, "fail"),
            ("[MODEL-A] (reviewer)", "model-a", "small", True, "warn"),
            ("model-a", "model-a", "large", True, "fail"),
            ("model-b", "model-a", "large", False, "pass"),
            ("", "model-a", "small", True, "fail"),
            ("model-b", "", "small", False, "fail"),
        ]
        for reviewer, implementer, kind, fresh, expected in cases:
            self.assertEqual(
                evidence.review_independence(reviewer, implementer, kind, fresh)[0], expected
            )
        box = self.box()
        self.review(box, "MODEL-A (fresh context)")
        self.assertTrue(self.fails(gate.run(box.item), "V4 independent review"))
        self.review(box, "model-a", fresh=True)
        self.assertTrue(gate.run(box.item)["ok"])

    def test_negative_reviews_are_recorded_but_never_approved(self):
        for verdict in ("fix-needed", "drop"):
            box = self.box()
            self.assertTrue(self.review(box, verdict=verdict)["ok"])
            output = evidence.approve(box.item, box.head, "Approve")
            self.assertFalse(output["ok"])
            self.assertFalse((box.root / ".dv/approval.json").exists())

    def test_review_full_head_required(self):
        box = self.box()
        report = box.root / ".dv/notes.txt"
        for head in (box.head[:7], "a" * 40):
            with self.assertRaises(DVError):
                evidence.record_review(box.item, "model-b", head, "pass", report)
            with self.assertRaises(DVError):
                evidence.approve(box.item, head, "Approve")

    def test_substantive_report_required(self):
        box = self.box()
        report = box.root / ".dv/empty.txt"
        for content in ("", "pass", "A short report."):
            report.write_text(content)
            with self.assertRaises(DVError):
                evidence.record_review(box.item, "model-b", box.head, "pass", report)

    def test_amended_commit_invalidates_all_evidence(self):
        box = self.box()
        evidence.approve(box.item, box.head, "Approve")
        git(box.root, "commit", "--amend", "-m", "Reword the change")
        output = gate.run(box.item, True)
        for check in ("V2 red/green", "V4 independent review", "V5 human approval"):
            self.assertTrue(self.fails(output, check), output)
        self.assertEqual(box.item.meta["status"], "approved")

    def test_changed_description_invalidates_approval_and_review(self):
        box = self.box(False)
        box.write(".dv/description.txt", "Return the corrected value for the affected input.")
        box.update(description_file=".dv/description.txt")
        box.ready()
        evidence.approve(box.item, box.head, "Approved code and description")
        box.write(".dv/description.txt", "Return a different value for every input.")
        output = gate.run(box.item, True)
        self.assertTrue(self.fails(output, "V5 human approval"))
        self.assertTrue(self.fails(output, "V4 independent review"))

    def test_changed_runtime_content_invalidates_review(self):
        box = self.box(False)
        box.write(".dv/runtime.txt", "Observed the corrected value in the local program.")
        box.update(runtime_files=[".dv/runtime.txt"])
        box.ready()
        evidence.approve(box.item, box.head, "Approved runtime evidence")
        box.write(".dv/runtime.txt", "Observed another run.")
        output = gate.run(box.item, True)
        self.assertTrue(self.fails(output, "V4 independent review"))
        self.assertTrue(self.fails(output, "V5 human approval"))

    def test_missing_or_empty_runtime_is_not_a_pass(self):
        box = self.box(False)
        box.write(".dv/runtime.txt", "")
        box.update(runtime_files=[".dv/runtime.txt"])
        self.assertTrue(self.fails(gate.run(box.item), "V3 runtime attachments"))

    def test_changed_review_report_and_record_invalidate_approval(self):
        box = self.box()
        evidence.approve(box.item, box.head, "Approve")
        box.write(".dv/review.txt", REPORT + " Additional finding.")
        output = gate.run(box.item, True)
        self.assertTrue(self.fails(output, "V4 independent review"))
        self.assertTrue(self.fails(output, "V5 human approval"))
        self.review(box)
        output = gate.run(box.item, True)
        self.assertFalse(self.fails(output, "V4 independent review"))
        self.assertTrue(self.fails(output, "V5 human approval"))

    def test_changed_recipe_exception_or_config_invalidates_context(self):
        for field in ("recipe", "exception", "config"):
            box = self.box()
            if field == "recipe":
                box.update(test_command=[sys.executable, "-c", "pass"])
            elif field == "exception":
                box.update(exceptions={"comment_justification": "Explain a external invariant"})
            else:
                with (box.root / ".dv.toml").open("a") as stream:
                    stream.write("timeout_seconds = 99\n")
                box.commit()
            output = gate.run(box.item)
            self.assertTrue(self.fails(output, "V4 independent review"), field)

    def test_changed_merge_base_invalidates_evidence(self):
        box = self.box()
        git(box.root, "branch", "--force", "main", "HEAD")
        output = gate.run(box.item)
        self.assertTrue(self.fails(output, "V4 independent review"))

    def test_ok_flags_and_saved_gate_cannot_forge_proof(self):
        box = self.box()
        path = box.root / ".dv/redgreen.json"
        record = read_json(path)
        record["ok"] = True
        record["red"]["exit_code"] = 0
        write_json(path, record)
        write_json(box.root / ".dv/gate.json", {"ok": True})
        self.assertTrue(self.fails(gate.run(box.item), "V2 red/green"))

    def test_log_hashes_and_malformed_evidence_fail_closed(self):
        box = self.box()
        box.write(".dv/redgreen-red.log", "Forged output")
        self.assertTrue(self.fails(gate.run(box.item), "V2 red/green"))
        for content in ('{"ok": true, "ok": false}', "[]", '{"red": true}'):
            box.write(".dv/redgreen.json", content)
            self.assertTrue(self.fails(gate.run(box.item), "V2 red/green"))

    def test_configured_commands_rerun_and_failure_is_honest(self):
        box = self.box(False)
        box.write(
            ".dv.toml",
            '[dv]\nstatic_commands = [["' + sys.executable + '", "-c", "raise SystemExit(1)"]]\n'
            'test_commands = [["' + sys.executable + '", "-c", "pass"]]\n',
        )
        box.commit()
        box.ready()
        output = gate.run(box.item)
        self.assertTrue(self.fails(output, "V1 static"))
        self.assertEqual(output["exit_code"], 1)

    def test_missing_check_executable_is_operational_failure(self):
        box = self.box(False)
        box.write(".dv.toml", '[dv]\nstatic_commands = [["dv-missing-check-command"]]\n')
        box.commit()
        output = gate.run(box.item)
        self.assertFalse(output["ok"])
        self.assertEqual(output["exit_code"], 3)

    def test_waiver_is_visible_but_does_not_waive_static(self):
        box = self.box(False)
        box.write(".dv.toml", "[dv]\n")
        box.commit()
        box.update(exceptions={"no_test_reason": "Only changes the public prose examples"})
        self.review(box)
        output = gate.run(box.item)
        self.assertFalse(output["ok"])
        self.assertTrue(self.fails(output, "V1 static"))
        self.assertFalse(self.fails(output, "V2 red/green"))
        self.assertIn("WAIVED", str(output))

    def test_dirty_gate_does_not_downgrade_ready_claim(self):
        box = self.box()
        gate.run(box.item)
        box.write("dirty.txt", "uncommitted")
        output = gate.run(box.item)
        self.assertFalse(output["ok"])
        self.assertEqual(box.item.meta["status"], "ready")

    def test_check_commands_cannot_mutate_tree_and_still_pass(self):
        box = self.box(False)
        content = (
            (box.root / ".dv.toml")
            .read_text()
            .replace(
                '"-c", "pass"',
                '''"-c", "from pathlib import Path; Path('src/value.ts').write_text('mutated')"''',
            )
        )
        box.write(".dv.toml", content)
        box.commit()
        box.ready()
        output = gate.run(box.item)
        self.assertFalse(output["ok"])
        self.assertTrue(self.fails(output, "scope unchanged"))

    def test_merge_conflict_is_checked_without_switching_branches(self):
        box = self.box(False)
        git(box.root, "checkout", "main")
        box.write("src/value.ts", "different\n")
        box.commit("Change base behavior")
        git(box.root, "checkout", "fix")
        box.ready()
        output = gate.run(box.item)
        self.assertTrue(self.fails(output, "mergeability"))
        self.assertEqual(git(box.root, "branch", "--show-current"), "fix")

    def test_redgreen_ok_false_is_not_authoritative(self):
        box = self.box()
        path = box.root / ".dv/redgreen.json"
        record = read_json(path)
        record["ok"] = False
        write_json(path, record)
        self.assertFalse(self.fails(gate.run(box.item), "V2 red/green"))
