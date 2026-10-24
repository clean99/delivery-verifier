from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from dv.common import write_json
from dv.item import Item

ROOT = Path(__file__).resolve().parents[1]
REPORT = (
    "Inspected the diff and both regression logs. The failing assertio"
    "n exercises the fixed value. No correctness defects found."
)


def git(root: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(
        ["git", "-C", str(root), *args], env=env, capture_output=True, text=True, check=True
    ).stdout.strip()


class Sandbox:
    def __init__(
        self,
        *,
        old: dict[str, str | bytes] | None = None,
        new: dict[str, str | bytes | None] | None = None,
        command: list[str] | None = None,
    ):
        self.tmp = tempfile.TemporaryDirectory(prefix="dv-sandbox-", dir=ROOT / "tests")
        self.root = Path(self.tmp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test User")
        git(self.root, "config", "user.email", "test@example.invalid")
        git(self.root, "config", "commit.gpgsign", "false")
        self.write(".gitignore", "__pycache__/\n")
        self.write(
            ".dv.toml",
            '[dv]\nstatic_commands = [["' + sys.executable + '", "-c", "pass"]]\n'
            'test_commands = [["'
            + sys.executable
            + '", "-m", "unittest", "discover", "-s", "tests"]]\n',
        )
        self.write("src/value.ts", "broken\n")
        self.write(
            "tests/test_value.py",
            (
                "import unittest\n\nclass ValueTests(unittest.TestCase):\n    def tes"
                "t_initial(self):\n        self.assertEqual(1, 1)\n"
            ),
        )
        for path, content in (old or {}).items():
            self.write(path, content)
        self.commit("Initial behavior")
        git(self.root, "checkout", "-b", "fix")
        self.write("src/value.ts", "fixed\n")
        self.write(
            "tests/test_value.py",
            (
                "from pathlib import Path\nimport unittest\n\nclass ValueTests(unitte"
                "st.TestCase):\n    def test_value(self):\n        self.assertEqual("
                "Path('src/value.ts').read_text().strip(), 'fixed')\n"
            ),
        )
        for path, content in (new or {}).items():
            self.write(path, content)
        self.commit("Return the corrected value")
        self.path = self.root / ".dv/item.json"
        (self.root / ".dv").mkdir()
        with (self.root / ".git/info/exclude").open("a") as stream:
            stream.write("\n/.dv/\n")
        write_json(
            self.path,
            {
                "schema_version": 1,
                "summary": "Return the corrected value",
                "base_ref": "main",
                "implementer": "model-a",
                "test_command": command
                or [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                "test_files": ["tests/test_value.py"],
            },
        )

    def write(self, path: str, content: str | bytes | None) -> None:
        target = self.root / path
        if content is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content if isinstance(content, bytes) else content.encode())

    def commit(self, message: str = "Adjust behavior") -> None:
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", message)

    def update(self, **values: object) -> None:
        meta = json.loads(self.path.read_text())
        meta.update(values)
        write_json(self.path, meta)

    @property
    def item(self) -> Item:
        return Item(self.path)

    @property
    def head(self) -> str:
        return git(self.root, "rev-parse", "HEAD")

    def ready(self) -> Item:
        from dv import evidence, redgreen

        item = self.item
        proof = redgreen.run(item)
        assert proof["ok"], proof
        report = self.root / ".dv/notes.txt"
        report.write_text(REPORT)
        evidence.record_review(item, "model-b", item.head, "pass", report)
        return self.item

    def close(self) -> None:
        self.tmp.cleanup()
