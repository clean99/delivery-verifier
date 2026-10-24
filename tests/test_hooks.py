from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from support import Sandbox

from dv import evidence, hooks
from dv.common import DVError, read_json, write_json


class HookTests(unittest.TestCase):
    def box(self, ready=False):
        box = Sandbox()
        self.addCleanup(box.close)
        if ready:
            box.ready()
            evidence.approve(box.item, box.head, "Approved this exact change")
        return box

    def push(self, box, command, **extra):
        return hooks.pre_tool_use(
            {
                "cwd": str(box.root),
                "tool_name": "Bash",
                "tool_input": {"command": command},
                **extra,
            },
            box.root,
        )

    def denied(self, output):
        return output.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def stop(self, box, active=False, session="session-a"):
        return hooks.stop(
            {"cwd": str(box.root), "session_id": session, "stop_hook_active": active}, box.root
        )

    def test_raw_push_requires_current_delivery_gate(self):
        box = self.box()
        self.assertTrue(self.denied(self.push(box, "git push origin HEAD:main")))
        box.ready()
        evidence.approve(box.item, box.head, "Approve")
        self.assertFalse(self.denied(self.push(box, "git push origin HEAD:main")))
        self.assertFalse(self.denied(self.push(box, f"git -C '{box.root}' push origin fix:main")))
        self.assertFalse(self.denied(self.push(box, f"git push origin {box.head}:refs/heads/main")))

    def test_stale_approval_is_not_bypassed(self):
        box = self.box(True)
        box.write("src/another.ts", "new\n")
        box.commit()
        self.assertTrue(self.denied(self.push(box, "git push origin HEAD:main")))

    def test_unsafe_or_ambiguous_push_syntax_is_denied(self):
        box = self.box(True)
        commands = [
            "git push",
            "git push origin",
            "git push --force origin HEAD:main",
            "git push origin +HEAD:main",
            "git push origin :main",
            "git push origin main",
            "git push origin HEAD:main fix:other",
            "git push --all origin",
            "git -c alias.other=x push origin HEAD:main",
            "git commit -am x && git push origin HEAD:main",
            "git push origin HEAD:main; echo finished",
            "git push origin HEAD~1:main",
            "git push origin HEAD:main && git push other HEAD:main",
            "git push origin $(git rev-parse HEAD):main",
            "env MODE=1 git push origin HEAD:main",
            "git push origin 'HEAD:main",
            "git push origin HEAD:main > output.txt",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(self.denied(self.push(box, command)))

    def test_gh_write_shortcuts_deny_reads_are_allowed(self):
        box = self.box()
        for command in (
            "gh pr create --title x",
            "gh issue comment 1 -b text",
            "gh api repos/example/project/issues -X POST -f title=x",
            "gh api repos/example/project/issues -f title=x",
        ):
            self.assertTrue(self.denied(self.push(box, command)), command)
        for command in (
            "git status",
            "git commit -am change",
            "gh pr list",
            "dv gate .dv/item.json",
            "gh api repos/example/project/issues",
            "echo done",
        ):
            self.assertFalse(self.denied(self.push(box, command)), command)

    def test_no_wrapper_exemption_for_combined_write(self):
        box = self.box()
        self.assertTrue(
            self.denied(self.push(box, "dv gate .dv/item.json && gh pr create --title x"))
        )

    def test_exact_allowlist_is_visible_and_not_a_prefix_match(self):
        box = self.box()
        with (box.root / ".dv.toml").open("a") as stream:
            stream.write('\n[dv.hooks]\nallow_commands = ["gh pr create --title x"]\n')
        output = self.push(box, "gh pr create --title x")
        self.assertFalse(self.denied(output))
        self.assertIn("exemption", output["systemMessage"])
        self.assertTrue(self.denied(self.push(box, "gh pr create --title x; git push")))

    def test_cross_repository_target_and_payload_cwd_are_denied(self):
        box, other = self.box(True), self.box()
        self.assertTrue(self.denied(self.push(box, f"git -C {other.root} push origin HEAD:main")))
        self.assertTrue(
            self.denied(self.push(box, "git push origin HEAD:main", cwd=str(other.root)))
        )

    def test_malformed_payload_config_and_gate_crash_fail_closed(self):
        box = self.box()
        self.assertTrue(self.denied(hooks.run("pre-tool-use", box.root, "{")))
        self.assertTrue(self.denied(hooks.pre_tool_use({}, box.root)))
        box.write(".dv.toml", "malformed")
        self.assertTrue(self.denied(self.push(box, "git push origin HEAD:main")))
        box = self.box(True)
        with patch("dv.hooks.gate.run", side_effect=RuntimeError("failed")):
            self.assertTrue(self.denied(self.push(box, "git push origin HEAD:main")))

    def test_stop_skips_preparing_and_honestly_blocked(self):
        box = self.box()
        self.assertEqual(self.stop(box), {})
        box.update(status="blocked", blocked_reason="Local dependency not available")
        self.assertEqual(self.stop(box), {})

    def test_ready_and_approved_claims_are_rechecked(self):
        box = self.box()
        box.update(status="ready")
        self.assertEqual(self.stop(box)["decision"], "block")
        box.ready()
        self.assertEqual(self.stop(box), {})
        box.update(status="approved")
        self.assertEqual(self.stop(box)["decision"], "block")

    def test_stop_retry_bound_is_per_session_and_sequence(self):
        box = self.box()
        box.update(status="ready")
        self.assertEqual(self.stop(box)["decision"], "block")
        self.assertEqual(self.stop(box, True)["decision"], "block")
        self.assertEqual(self.stop(box, True)["decision"], "block")
        fourth = self.stop(box, True)
        self.assertIn("without approval", fourth["systemMessage"])
        self.assertEqual(self.stop(box, True, "another-session")["decision"], "block")
        self.assertEqual(self.stop(box, False)["decision"], "block")
        self.assertEqual(box.item.meta["status"], "ready")

    def test_stop_success_resets_counter(self):
        box = self.box()
        box.update(status="ready")
        self.stop(box)
        self.stop(box, True)
        box.update(status="preparing")
        self.assertEqual(self.stop(box, True), {})
        box.update(status="ready")
        self.assertEqual(self.stop(box, True)["decision"], "block")

    def test_stop_missing_item_and_malformed_records_are_problems(self):
        box = self.box()
        box.path.unlink()
        self.assertEqual(self.stop(box)["decision"], "block")
        box.write(".dv/item.json", "{}")
        self.assertEqual(self.stop(box)["decision"], "block")
        box.write(".dv/item.json", "not JSON")
        self.assertEqual(self.stop(box)["decision"], "block")

    def test_custom_item_location_and_zero_stop_blocks(self):
        box = self.box()
        box.write(".dv/other/item.json", box.path.read_text())
        write_json(box.root / ".dv/other/item.json", {**read_json(box.path), "status": "ready"})
        with (box.root / ".dv.toml").open("a") as stream:
            stream.write('\n[dv.hooks]\nitems = [".dv/other/item.json"]\nmax_stop_blocks = 0\n')
        self.assertIn("systemMessage", self.stop(box))

    def test_install_dry_run_and_idempotent_merge(self):
        box = self.box()
        settings = {
            "permissions": {"allow": ["Read"]},
            "theme": "dark",
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo existing"}]}]},
        }
        path = box.root / ".claude/settings.json"
        write_json(path, settings)
        with patch("dv.hooks.shutil.which", return_value="/example path/bin/dv"):
            preview = hooks.install(box.root, True)
            self.assertEqual(read_json(path), settings)
            installed = hooks.install(box.root)
            repeated = hooks.install(box.root)
        self.assertEqual(installed["hooks"], repeated["hooks"])
        self.assertEqual(installed["hooks"], preview["hooks"])
        saved = read_json(path)
        self.assertEqual(saved["permissions"], settings["permissions"])
        command = installed["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertIn("'/example path/bin/dv'", command)
        self.assertIn("--project", command)
        self.assertEqual(len(saved["hooks"]["Stop"]), 2)

    def test_install_refuses_bad_settings_and_symlinks(self):
        box = self.box()
        path = box.root / ".claude/settings.json"
        write_json(path, {"hooks": []})
        with self.assertRaises(DVError):
            hooks.install(box.root)
        path.unlink()
        path.symlink_to(box.root / ".dv/item.json")
        with self.assertRaises(DVError):
            hooks.install(box.root)

    def test_hook_run_json_is_parseable(self):
        box = self.box()
        text = json.dumps({"cwd": str(box.root), "session_id": "test"})
        self.assertEqual(hooks.run("stop", box.root, text), {})
