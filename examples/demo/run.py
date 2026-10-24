"""Exercise the real CLI in a disposable repository, without downloads or model calls."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
BROKEN = 'def split_words(text):\n    return text.split(" ")\n'
FIXED = 'def split_words(text):\n    return text.split()\n'
BASE_TEST = '''import unittest

from words import split_words


class WordTests(unittest.TestCase):
    def test_single_space(self):
        self.assertEqual(split_words("red green"), ["red", "green"])
'''
HOLLOW_TEST = '''
    def test_repeated_spaces(self):
        self.assertIsNotNone(split_words("red   green"))
'''
REAL_TEST = '''
    def test_repeated_spaces(self):
        self.assertEqual(split_words("red   green"), ["red", "green"])
'''


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run_demo(root: Path) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        PYTHONPATH=str(PROJECT / "src"),
        PYTHONDONTWRITEBYTECODE="1",
    )

    def run(*argv: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            argv, cwd=root, env=env, capture_output=True, text=True, timeout=120
        )
        require(
            result.returncode == expected,
            f"{shlex.join(argv)}: expected exit {expected}, got {result.returncode}\n"
            f"{result.stdout}{result.stderr}",
        )
        return result

    def git(*args: str) -> str:
        return run("git", *args).stdout.strip()

    def write(path: str, text: str) -> None:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(message: str) -> None:
        git("add", ".")
        git("commit", "-m", message)

    def dv(*args: str, expected: int = 0) -> dict:
        print(f"\n$ dv {shlex.join(args)} --json", flush=True)
        result = run(sys.executable, "-m", "dv", *args, "--json", expected=expected)
        output = json.loads(result.stdout)
        require(output["ok"] == (expected == 0), "CLI result disagrees with its exit code")
        print(f"{output['command']}: {'PASS' if output['ok'] else 'FAIL'} (exit {expected})")
        if output["command"] == "redgreen":
            print(f"  red={output['red']['exit_code']}, green={output['green']['exit_code']}")
        else:
            for check in output.get("checks", []):
                print(f"  [{check['outcome'].upper()}] {check['check']}: {check['detail']}")
        return output

    git("init", "--template=", "-b", "main")
    git("config", "user.name", "Demo User")
    git("config", "user.email", "demo@example.invalid")
    git("config", "commit.gpgsign", "false")
    write(".gitignore", "__pycache__/\n")
    python = json.dumps(sys.executable)
    write(
        ".dv.toml",
        f'[dv]\nstatic_commands = [[{python}, "-m", "compileall", "-q", "words.py"]]\n'
        f'test_commands = [[{python}, "-m", "unittest", "discover", "-s", "tests"]]\n',
    )
    write("words.py", BROKEN)
    write("tests/test_words.py", BASE_TEST)
    commit("Handle words separated by one space")
    git("switch", "-c", "fix-spaces")

    print("An agent fixes split_words(), but adds a test that only checks existence.")
    print('  self.assertIsNotNone(split_words("red   green"))')
    write("words.py", FIXED)
    write("tests/test_words.py", BASE_TEST + HOLLOW_TEST)
    commit("Handle repeated spaces")
    dv("init", "--implementer", "demo-implementer", "--base", "main")
    item_path = root / ".dv/item.json"
    item = json.loads(item_path.read_text(encoding="utf-8"))
    item.update(
        summary="Split repeated spaces without empty words",
        test_command=[sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        test_files=["tests/test_words.py"],
    )
    write(".dv/item.json", json.dumps(item, indent=2) + "\n")

    hollow = dv("redgreen", ".dv/item.json", expected=1)
    require(hollow["red"]["exit_code"] == 0, "The hollow test should pass on broken code")
    require(hollow["green"]["exit_code"] == 0, "The hollow test should pass on fixed code")
    require(git("status", "--porcelain") == "", "Red/green did not restore the clean tree")
    print("Hollow test rejected: it passes even with the fix removed.")

    print('\nReplace it with self.assertEqual(split_words("red   green"), ["red", "green"]).')
    write("tests/test_words.py", BASE_TEST + REAL_TEST)
    commit("Assert the words returned for repeated spaces")
    proof = dv("redgreen", ".dv/item.json")
    require(proof["red"]["exit_code"] == 1, "The regression must fail normally on broken code")
    require(proof["green"]["exit_code"] == 0, "The regression must pass on fixed code")
    red_log = (root / ".dv/redgreen-red.log").read_text(encoding="utf-8")
    require("AssertionError" in red_log, "Red must fail at the assertion, not at import/setup")
    print("Meaningful regression proved: red=1, green=0.")
    dv("hygiene", ".dv/item.json")

    print("\nRecord a SYNTHETIC review fixture, solely to demonstrate the gate contract.")
    print("This is not an independent review. Real changes need a separate reviewer.")
    write(
        ".dv/demo-review.txt",
        "Synthetic review fixture for this demo only. The red assertion reports empty words "
        "between repeated spaces; the green run passes after split() replaces split(' '). "
        "This fixture is not an independent review.\n",
    )
    dv(
        "review", "record", ".dv/item.json", "--reviewer", "demo-reviewer",
        "--head", git("rev-parse", "HEAD"), "--verdict", "pass",
        "--file", ".dv/demo-review.txt",
    )
    dv("gate", ".dv/item.json")
    delivery = dv("gate", ".dv/item.json", "--require-approval", expected=1)
    failures = {row["check"] for row in delivery["checks"] if row["outcome"] == "fail"}
    require(failures == {"V5 human approval"}, "Delivery should fail only for absent approval")
    require(git("status", "--porcelain") == "", "Demo left a dirty worktree")
    print("\nDemo complete: readiness passes; delivery still requires real human approval.")


def main() -> int:
    if sys.version_info < (3, 10) or os.name != "posix" or not shutil.which("git"):
        print("Use Python 3.10+, Git 2.38+, and a POSIX system (Linux/macOS).", file=sys.stderr)
        return 2
    scratch = PROJECT / ".dv"
    scratch.mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="demo-", dir=scratch) as directory:
            run_demo(Path(directory).resolve())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Demo failed: {error}", file=sys.stderr)
        return 1
    print("The throwaway repository and its logs have been removed. No network calls were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
