from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import ROOT

from dv.runners import red_assertion

NESTED = """const {test, describe, it} = require('node:test');
const assert = require('node:assert/strict');
test('parent', async t => {
    await t.test('child', async t => {
        await t.test('grandchild', () => assert.equal(1, 2));
    });
});
describe('suite', () => {
    it('child', () => assert.equal(1, 2));
});
"""


class NodeRunnerTests(unittest.TestCase):
    def valid(self, content):
        return red_assertion({"status": "normal", "exit_code": 1}, content, "node-test")[0]

    def test_captured_nested_tap_counts_parents_but_not_suites(self):
        content = (ROOT / "tests/fixtures/runners/node-nested.log").read_bytes()
        self.assertTrue(self.valid(content))
        self.assertFalse(self.valid(content.replace(b"# fail 4", b"# fail 2")))
        self.assertFalse(self.valid(content.replace(b"ERR_ASSERTION", b"ERR_TEST_FAILURE", 1)))
        start = content.index(b"    # Subtest: child")
        end = content.index(b"not ok 1 - parent")
        self.assertFalse(self.valid(content[:start] + content[end:]))

    @unittest.skipUnless(shutil.which("node"), "Node is optional runner test tooling")
    def test_real_nested_assertions_and_child_errors(self):
        for source, expected in (
            (NESTED, True),
            (NESTED.replace("assert.equal(1, 2)", "{ throw new Error('broken'); }", 1), False),
            (NESTED.replace("await t.test('child'", "t.test('child'", 1), False),
        ):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                path = Path(directory, "value.test.cjs")
                path.write_text(source)
                proc = subprocess.run(
                    ["node", "--test", "--test-reporter=tap", str(path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=30,
                )
                self.assertEqual(proc.returncode, 1, proc.stdout.decode())
                self.assertEqual(self.valid(proc.stdout), expected, proc.stdout.decode())
