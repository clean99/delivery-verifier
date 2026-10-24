from __future__ import annotations

import ast
import copy
import os
import unittest
from unittest.mock import patch

from support import ROOT, Sandbox, git

from dv import config
from dv._vendor import tomllib as compat
from dv.common import DVError, canonical, decode_json, read_json
from dv.item import Item, initialize


class ValidationTests(unittest.TestCase):
    def box(self):
        box = Sandbox()
        self.addCleanup(box.close)
        return box

    def test_strict_json_duplicate_and_nonfinite_values(self):
        for content in ('{"x": 1, "x": 2}', '{"x": NaN}', '{"x": Infinity}', "{"):
            with self.assertRaises(DVError):
                decode_json(content)
        self.assertEqual(canonical({"b": 2, "a": 1}), b'{"a":1,"b":2}')

    def test_item_unknown_schema_types_and_blank_fields(self):
        for change in (
            {"schema_version": True},
            {"schema_version": 2},
            {"implementer": " "},
            {"test_command": "sh test.sh"},
            {"test_command": []},
            {"test_files": "x"},
            {"exceptions": {"no_test_reason": True}},
            {"extra": "ignored?"},
            {"status": "blocked"},
            {"blocked_reason": "unexpected"},
            {"kind": "huge"},
            {"test_cwd": "../escape"},
        ):
            box = self.box()
            box.update(**change)
            with self.subTest(change=change), self.assertRaises(DVError):
                box.item

    def test_context_excludes_only_status_and_blocked_reason(self):
        box = self.box()
        before = box.item.scope
        box.update(status="blocked", blocked_reason="Waiting for a local prerequisite")
        self.assertEqual(box.item.scope, before)
        box.update(summary="A different change")
        self.assertNotEqual(box.item.scope, before)

    def test_custom_item_must_be_ignored(self):
        box = self.box()
        path = box.root / "state/item.json"
        box.write("state/item.json", box.path.read_text())
        with self.assertRaises(DVError):
            Item(path)
        with (box.root / ".git/info/exclude").open("a") as stream:
            stream.write("/state/\n")
        self.assertEqual(Item(path).head, box.head)

    def test_tracked_metadata_and_sidecar_symlinks_are_rejected(self):
        box = self.box()
        git(box.root, "add", "-f", ".dv/item.json")
        box.commit()
        with self.assertRaises(DVError):
            box.item
        box = self.box()
        (box.root / ".dv/gate.json").symlink_to("notes.txt")
        with self.assertRaises(DVError):
            box.item.file("gate.json")

    def test_symlink_escape_and_metadata_parent_symlinks(self):
        box = self.box()
        (box.root / ".dv/outside").symlink_to(box.root.parent, target_is_directory=True)
        box.update(description_file=".dv/outside/escape.txt")
        with self.assertRaises(DVError):
            box.item
        box = self.box()
        (box.root / ".dv/link").symlink_to(box.root / ".dv", target_is_directory=True)
        box.update(description_file=".dv/link/notes.txt")
        with self.assertRaises(DVError):
            box.item

    def test_description_cannot_overlap_outputs(self):
        for path in (".dv/item.json", ".dv/review.txt", ".dv/checks/static_commands-0.log"):
            box = self.box()
            box.update(description_file=path)
            with self.assertRaises(DVError):
                box.item

    def test_test_files_must_exist_at_head(self):
        box = self.box()
        box.write(".dv/untracked-test.py", "assert True\n")
        box.update(test_files=[".dv/untracked-test.py"])
        with self.assertRaises(DVError):
            box.item

    def test_missing_optional_description_has_null_hash(self):
        box = self.box()
        self.assertIsNone(box.item.attachments["description"])
        box.update(description_file=".dv/not-yet-written.txt")
        self.assertIsNone(box.item.attachments["description"])
        box.write(".dv/not-yet-written.txt", "")
        self.assertIsNotNone(box.item.attachments["description"])

    def test_configuration_recursive_merge_and_unrelated_tables(self):
        box = self.box()
        box.write(
            "pyproject.toml",
            "[tool.other]\nany_key = true\n[tool.dv]\ntimeout_seconds = 30\n"
            "[tool.dv.limits]\nwarn_source_lines = 100\n",
        )
        box.write(
            ".dv.toml",
            "[irrelevant]\nkey = 1\n[dv]\ntimeout_seconds = 40\n"
            'static_commands = [["check", "one"]]\n[dv.limits]\nmax_source_lines = 500\n',
        )
        cfg = config.load(box.root)
        self.assertEqual(cfg["timeout_seconds"], 40)
        self.assertEqual(cfg["limits"]["warn_source_lines"], 100)
        self.assertEqual(cfg["limits"]["max_source_lines"], 500)
        self.assertEqual(cfg["limits"]["max_source_lines_large"], 900)

    def test_invalid_config_never_silently_weakens_gate(self):
        invalid = [
            "unknown = true",
            "timeout_seconds = true",
            "timeout_seconds = 0",
            "schema_version = 2",
            'generated_paths = "*"',
            'static_commands = ["check"]',
            "test_commands = [[]]",
            "allowed_prose_lines = [false]",
            'generated_paths = ["../*"]',
            "[dv.limits]\nmax_source_lines_large = 1",
            "[dv.limits]\nunknown = 3",
            "[dv.hooks]\nmax_stop_blocks = -1",
            "[dv.hooks]\nitems = []",
            '[dv.hooks]\nitems = ["../item.json"]',
        ]
        for text in invalid:
            box = self.box()
            box.write(".dv.toml", "[dv]\n" + text + "\n")
            with self.subTest(text=text), self.assertRaises(DVError):
                config.load(box.root)

    def test_no_policy_environment_overrides(self):
        box = self.box()
        before = config.load(box.root)
        with patch.dict(os.environ, {"DV_MAX_SOURCE_LINES": "999999", "DV_CONFIG": "/not-read"}):
            self.assertEqual(config.load(box.root), before)

    def test_toml_compatibility_matches_standard_parser(self):
        try:
            import tomllib
        except ModuleNotFoundError:
            self.skipTest("Cross-parser comparison requires Python 3.11 or newer")

        valid = [
            "[dv]\ntimeout_seconds=42\n",
            'a=[1,2,3]\nb={x="text",y=true}\n',
            'quoted="""multi\nline"""\ndate=2026-01-01\n',
            '[[commands]]\nargv=["check"]\n',
            '"dotted.key" = "unicode \\u03bb"\n',
        ]
        for fixture in valid:
            self.assertEqual(compat.loads(fixture), tomllib.loads(fixture))
        for fixture in ("x=1\nx=2", "[a\nx=1", "x=[", 'x="\\q"', "[a]\n[a]\n"):
            for parser in (compat, tomllib):
                with self.assertRaises(ValueError):
                    parser.loads(fixture)

    def test_python310_syntax_for_all_runtime_modules(self):
        for path in (ROOT / "src/dv").rglob("*.py"):
            with self.subTest(path=path):
                ast.parse(path.read_text(), feature_version=(3, 10))

    def test_init_is_local_and_never_overwrites(self):
        box = self.box()
        box.path.unlink()
        exclude = box.root / ".git/info/exclude"
        before = exclude.read_text()
        with patch("pathlib.Path.cwd", return_value=box.root):
            output = initialize(box.path, "model-a", "main", "small")
            self.assertTrue(output["ok"])
            with self.assertRaises(DVError):
                initialize(box.path, "model-a", "main", "small")
        self.assertEqual(exclude.read_text(), before)
        self.assertEqual(read_json(box.path)["status"], "preparing")
        self.assertFalse((box.root / ".claude").exists())

    def test_init_missing_base_is_error(self):
        box = self.box()
        with patch("pathlib.Path.cwd", return_value=box.root), self.assertRaises(DVError):
            initialize(box.root / ".dv/new/item.json", "model-a", "missing", "small")

    def test_defaults_not_mutated_by_merging(self):
        before = copy.deepcopy(config.DEFAULTS)
        box = self.box()
        config.load(box.root)
        self.assertEqual(config.DEFAULTS, before)
