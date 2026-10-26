"""Replay repository-owned synthetic cases and retain observed commands and raw logs."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures")
CASES = ("hollow-test", "wrong-failure", "not-run", "stale-review")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, required=True)
    parser.add_argument("--backend", choices=("native", "dv"), required=True)
    parser.add_argument(
        "--output", required=True, type=Path, help="new directory; never overwritten"
    )
    parser.add_argument(
        "--dv", type=Path, help="installed dv executable (otherwise current Python)"
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    repo = output / "repository"
    repo.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        PYTHONDONTWRITEBYTECODE="1",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        HUSKY="0",
        LEFTHOOK="0",
        UV_NO_CACHE="1",
    )
    dv = [str(args.dv.resolve())] if args.dv else [sys.executable, "-m", "dv"]
    if args.dv:
        env.pop("PYTHONPATH", None)
    elif importlib.util.find_spec("dv") is None:
        env["PYTHONPATH"] = str(ROOT / "src")
    commands, raw = [], []

    def run(argv, *, required=False):
        proc = subprocess.run(
            argv,
            cwd=repo,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=45,
        )
        commands.append({"argv": argv, "exit_code": proc.returncode})
        raw.append("$ " + shlex.join(argv) + "\n" + proc.stdout + f"\nexit={proc.returncode}\n")
        if required and proc.returncode:
            (output / "raw.log").write_text("\n".join(raw))
            raise RuntimeError(proc.stdout)
        return proc

    def git(*argv):
        return run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=clean99",
                "-c",
                "user.email=53188310+clean99@users.noreply.github.com",
                *argv,
            ],
            required=True,
        )

    def commit(message):
        git("add", ".")
        git("commit", "-m", message)

    git("init", "-b", "main")
    (repo / ".gitignore").write_text("__pycache__/\n.dv/\n")
    (repo / "tests").mkdir()
    test_file = {"hollow-test": "hollow.py", "not-run": "not_run.py"}.get(
        args.case, "regression.py"
    )
    baseline = "runtime_value.py" if args.case == "wrong-failure" else "base_value.py"
    shutil.copyfile(FIXTURES / baseline, repo / "value.py")
    shutil.copyfile(FIXTURES / test_file, repo / "tests/test_value.py")
    commit("test: define synthetic baseline")
    base = git("rev-parse", "HEAD").stdout.strip()
    git("switch", "-c", "fix")
    shutil.copyfile(FIXTURES / "fixed_value.py", repo / "value.py")
    commit("fix: return the corrected value")
    recipe = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
    observations = {}
    if args.case == "stale-review":
        run([*dv, "init", "--implementer", "model-a", "--json"], required=True)
        item_path = repo / ".dv/item.json"
        item = json.loads(item_path.read_text())
        item.update(summary="Synthetic review-binding fixture", test_command=recipe)
        item_path.write_text(json.dumps(item))
        report = repo / ".dv/report.txt"
        report.write_text(
            "Synthetic review fixture: the numeric assertion distinguishes these "
            "two implementations. This is not a real independent review or approval."
        )
        reviewed = git("rev-parse", "HEAD").stdout.strip()
        run(
            [
                *dv,
                "review",
                "record",
                str(item_path),
                "--reviewer",
                "model-b",
                "--head",
                reviewed,
                "--verdict",
                "pass",
                "--file",
                str(report),
                "--json",
            ],
            required=True,
        )
        git("commit", "--amend", "--allow-empty", "-m", "fix: amend reviewed commit")
        observations["review_head"] = json.loads((repo / ".dv/review.json").read_text())["head_sha"]
    head = git("rev-parse", "HEAD").stdout.strip()
    observations["head"] = head
    if args.backend == "native":
        git("switch", "--detach", base)
        red = run(recipe)
        git("switch", "fix")
        green = run(recipe)
        (output / "native-red.log").write_text(red.stdout)
        (output / "native-green.log").write_text(green.stdout)
        observations.update(red_exit=red.returncode, green_exit=green.returncode)
        ran = re.findall(r"^Ran (\d+) tests? in ", green.stdout, re.M)
        observations["green_executed"] = int(ran[0]) if len(ran) == 1 else 0
        exit_code = (
            int(not (red.returncode != 0 and green.returncode == 0))
            if args.case == "wrong-failure"
            else green.returncode
        )
        completed = exit_code == 0
    else:
        argv = [*dv, "prove", "--test", shlex.join(recipe), "--json"]
        if args.case == "stale-review":
            argv += ["--require-review", str(repo / ".dv/item.json")]
        proc = run(argv)
        record = json.loads(proc.stdout)
        (output / "proof-result.json").write_text(json.dumps(record, indent=2) + "\n")
        for phase in ("red", "green"):
            log = Path(record["evidence_dir"]) / f"{phase}.log"
            if log.exists():
                raw.append(phase + " log\n" + log.read_text())
        observations.update(
            red_exit=record["red"]["exit_code"],
            green_exit=record["green"]["exit_code"],
            green_executed=record["green"]["tests"]["executed"],
            optional_checks=record["optional_checks"],
            checker_sha256=record["checker_sha256"],
        )
        exit_code = proc.returncode
        reason = {"stale-review": "review", "not-run": "green execution"}.get(
            args.case, "red assertion"
        )
        completed = exit_code == 1 and any(
            reason in check["check"] and check["outcome"] == "fail" for check in record["checks"]
        )
    completed &= {
        "hollow-test": observations["red_exit"] == 0,
        "wrong-failure": observations["red_exit"] == 1 and "RuntimeError" in "\n".join(raw),
        "not-run": observations["green_executed"] == 0 and observations["green_exit"] == 0,
        "stale-review": observations.get("review_head") != head,
    }[args.case]
    version = run([*dv, "--version"], required=True).stdout.strip()
    try:
        pytest = importlib.metadata.version("pytest")
    except importlib.metadata.PackageNotFoundError:
        pytest = "not-installed"
    node = run(["node", "--version"]).stdout.strip() if shutil.which("node") else "not-installed"
    record = {
        "case": args.case,
        "backend": args.backend,
        "completed": bool(completed),
        "exit_code": exit_code,
        "log": "raw.log",
        "commands": commands,
        "toolchain": {
            "python": sys.version.split()[0],
            "pytest": pytest,
            "node": node,
            "dv_version": version,
        },
        "observations": observations,
    }
    (output / "raw.log").write_text("\n".join(raw))
    (output / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record) if args.json else f"{args.case}: completed={completed}; {output}")
    return 0 if completed else 1


if __name__ == "__main__":
    raise SystemExit(main())
