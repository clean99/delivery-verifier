"""Prove a Vitest regression and reject an import-error red, without installing dependencies."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]


def replay(vitest: str, output: Path | None = None) -> list[dict]:
    results = []
    for case, baseline, expected in (
        ("assertion", "export const value = 1;\n", 0),
        ("import-error", "import 'nonexistent_regression_dependency';\n", 1),
    ):
        with tempfile.TemporaryDirectory(prefix="dv-vitest-demo-") as directory:
            root = Path(directory)
            env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
            env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                       PYTHONPATH=str(PROJECT / "src"), HUSKY="0", LEFTHOOK="0")

            def command(*args, expected=0):
                proc = subprocess.run(args, cwd=root, env=env, capture_output=True,
                                      text=True, timeout=90)
                if proc.returncode != expected:
                    raise RuntimeError(proc.stdout + proc.stderr)
                return proc.stdout.strip()

            def commit(message):
                command("git", "add", ".")
                command("git", "commit", "-m", message)

            command("git", "init", "--template=", "-b", "main")
            command("git", "config", "user.name", "Demo User")
            command("git", "config", "user.email", "demo@example.invalid")
            command("git", "config", "commit.gpgsign", "false")
            (root / "value.js").write_text(baseline)
            commit("test: initial behavior")
            command("git", "switch", "-c", "fix")
            (root / "value.js").write_text("export const value = 2;\n")
            (root / "value.test.js").write_text(
                "import {value} from './value.js';\n"
                "test('value', () => expect(value).toBe(2));\n"
            )
            commit("fix: return the corrected value")
            recipe = shlex.join([vitest, "run", "--globals", "--maxWorkers=1"])
            proof = json.loads(command(sys.executable, "-m", "dv", "prove", "--test", recipe,
                                       "--timeout", "30", "--json", expected=expected))
            if proof["ok"] != (expected == 0):
                raise RuntimeError("Proof result disagrees with exit status")
            result = {"case": case, "exit_code": proof["exit_code"], "ok": proof["ok"],
                      "red": proof["red"], "green": proof["green"], "checks": proof["checks"]}
            if output is not None:
                target = output / case
                target.mkdir(parents=True, exist_ok=False)
                for name in ("proof.json", "red.log", "green.log"):
                    shutil.copy2(Path(proof["evidence_dir"]) / name, target / name)
                result["saved_evidence"] = str(target)
            results.append(result)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vitest", default=os.environ.get("DV_VITEST") or shutil.which("vitest"),
                        help="already installed Vitest 3.2.x executable")
    parser.add_argument("--output", type=Path, help="save proof JSON and both logs per case")
    args = parser.parse_args()
    if not args.vitest:
        parser.error("set --vitest or DV_VITEST to an installed executable; no automatic installs")
    executable = str(Path(args.vitest).absolute())
    print(json.dumps(replay(executable, args.output.resolve() if args.output else None), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
