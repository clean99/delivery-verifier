"""Executable distribution and handbook acceptance, without publishing anything."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import test_first_usable_spec as proof_spec
from support import ROOT
from test_first_usable_spec import COMMAND, environment

CASES = ("hollow-test", "wrong-failure", "not-run", "stale-review")


class FirstUsableHandbookSpec(unittest.TestCase):
    def test_A19_handbook_has_pinned_licensed_cases_and_fair_comparisons(self):
        handbook = ROOT / "docs/handbook/green-tests.md"
        manifest = ROOT / "docs/handbook/cases.json"
        self.assertTrue(handbook.is_file(), "SPEC pending: reproducible handbook is absent")
        self.assertTrue(manifest.is_file(), "SPEC pending: case manifest is absent")
        text = handbook.read_text(encoding="utf-8")
        self.assertIn("What a green test run still misses", text)
        for tool in ("trustbut", "Bugsweep", "Trail of Bits"):
            self.assertIn(tool, text)
        data = json.loads(manifest.read_text())
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["toolchain"]["python"], "3.12.13")
        self.assertEqual(data["toolchain"]["pytest"], "8.4.2")
        self.assertEqual(data["toolchain"]["node"], "20.20.2")
        self.assertRegex(data["toolchain"]["dv_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual({case["id"] for case in data["cases"]}, set(CASES))
        for case in data["cases"]:
            self.assertEqual(case["license"], "Apache-2.0")
            self.assertEqual(case["origin"], "synthetic")
            self.assertTrue(case["native_command"])
            self.assertTrue(case["dv_command"])
            self.assertTrue(case["expected_output"])
            self.assertTrue(case["dv_limit"])
            self.assertTrue(case["comparisons"])
            for comparison in case["comparisons"]:
                self.assertIn(comparison["evidence"], ("documentation-only", "reproduced"))
                self.assertRegex(comparison["source"], r"https://github.com/.+/blob/[0-9a-f]{40}/")
                self.assertTrue(comparison["scope"])

    def test_A20_all_four_cases_replay_native_and_dv_with_raw_logs(self):
        runner = ROOT / "examples/handbook/run.py"
        self.assertTrue(runner.is_file(), "SPEC pending: handbook case runner is absent")
        with tempfile.TemporaryDirectory(prefix="dv-handbook-spec-") as temporary:
            for case in CASES:
                for backend in ("native", "dv"):
                    with self.subTest(case=case, backend=backend):
                        output = Path(temporary) / case / backend
                        proc = subprocess.run(
                            [
                                sys.executable,
                                str(runner),
                                "--case",
                                case,
                                "--backend",
                                backend,
                                "--output",
                                str(output),
                                "--json",
                            ],
                            cwd=temporary,
                            env=environment(),
                            capture_output=True,
                            text=True,
                            timeout=60,
                        )
                        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                        record = json.loads(proc.stdout)
                        self.assertEqual(record["case"], case)
                        self.assertEqual(record["backend"], backend)
                        self.assertEqual(record["exit_code"], 0 if backend == "native" else 1)
                        self.assertTrue(record["toolchain"]["python"])
                        log = (output / record["log"]).resolve()
                        self.assertTrue(log.is_relative_to(output.resolve()))
                        self.assertTrue(log.read_text().strip())
                        observed = record["observations"]
                        if case == "hollow-test":
                            self.assertEqual(observed["red_exit"], 0)
                        elif case == "wrong-failure":
                            self.assertEqual(observed["red_exit"], 1)
                            self.assertIn("RuntimeError", log.read_text())
                        elif case == "not-run":
                            self.assertEqual(observed["green_executed"], 0)
                        else:
                            self.assertNotEqual(observed["review_head"], observed["head"])

    def test_exact_documented_loop_with_relative_pythonpath(self):
        text = (ROOT / "docs/handbook/green-tests.md").read_text()
        command = re.search(r"```sh\n(.*?)```", text, re.S)[1]
        with tempfile.TemporaryDirectory(prefix="dv-handbook-command-") as temporary:
            directory = Path(temporary)
            shutil.copytree(ROOT / "examples/handbook", directory / "examples/handbook")
            (directory / "src").symlink_to(ROOT / "src", target_is_directory=True)
            bins = directory / "bin"
            bins.mkdir()
            (bins / "python").symlink_to(sys.executable)
            env = environment()
            env["PATH"] = str(bins) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(
                ["sh", "-c", command], cwd=directory, env=env,
                capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            records = [json.loads(line) for line in proc.stdout.splitlines()]
            self.assertEqual(
                {(record["case"], record["backend"]) for record in records},
                {(case, backend) for case in CASES for backend in ("native", "dv")},
            )
            self.assertEqual(len(records), 8)
            for record in records:
                self.assertTrue(record["completed"], record)
                self.assertEqual(record["exit_code"], 0 if record["backend"] == "native" else 1)
                output = directory / ".dv/handbook" / f"{record['case']}-{record['backend']}"
                self.assertEqual(json.loads((output / "result.json").read_text()), record)
                self.assertTrue((output / "raw.log").read_text().strip())


class FirstUsableInstallSpec(unittest.TestCase):
    def test_A21_wheel_sdist_and_git_install_with_pip_and_pipx(self):
        probe = subprocess.run(
            [sys.executable, "-m", "dv", "prove", "--help"],
            cwd=ROOT,
            env=environment(),
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            probe.returncode, 0, "SPEC pending: installed prove contract\n" + probe.stderr
        )
        if os.environ.get("DV_SPEC_INSTALL") != "1":
            self.skipTest("set DV_SPEC_INSTALL=1 for isolated build/install acceptance")
        pipx = shutil.which("pipx")
        self.assertIsNotNone(pipx, "install acceptance requires pipx 1.7.1")
        with tempfile.TemporaryDirectory(prefix="dv-install-spec-") as temporary:
            directory = Path(temporary)
            source = directory / "source"
            shutil.copytree(
                ROOT,
                source,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".venv",
                    ".dv",
                    "build",
                    "dist",
                    "*.egg-info",
                    "__pycache__",
                    ".ruff_cache",
                    "artifacts",
                    "dv-sandbox-*",
                ),
            )
            env = environment()
            env.pop("PYTHONPATH", None)
            env.update(PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_CACHE_DIR="1")

            def run(argv, cwd=directory, extra_env=None):
                proc = subprocess.run(
                    argv,
                    cwd=cwd,
                    env={**env, **(extra_env or {})},
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                return proc

            run(
                [
                    sys.executable,
                    "-c",
                    "import build_backend; build_backend.build_sdist('dist'); "
                    "build_backend.build_wheel('dist')",
                ],
                cwd=source,
            )
            (wheel,) = (source / "dist").glob("*.whl")
            (sdist,) = (source / "dist").glob("*.tar.gz")
            # The local Git transport tests the VCS installer without a remote write.
            # Release rehearsal can substitute immutable public URLs for all sources.
            inputs = {
                "wheel": os.environ.get("DV_SPEC_WHEEL_URL", str(wheel)),
                "sdist": os.environ.get("DV_SPEC_SDIST_URL", str(sdist)),
                "git": os.environ.get("DV_SPEC_SOURCE_URL", "git+" + ROOT.as_uri()),
            }
            fixture = proof_spec.FirstUsableProofSpec()
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            fixture.fixture()
            for installer in ("pip", "pipx"):
                for kind, target in inputs.items():
                    with self.subTest(installer=installer, source=kind):
                        home = directory / f"{installer}-{kind}"
                        if installer == "pip":
                            run([sys.executable, "-m", "venv", str(home)])
                            python = home / "bin/python"
                            run([str(python), "-m", "pip", "install", target])
                            executable = home / "bin/dv"
                        else:
                            bins = home / "bin"
                            run(
                                [pipx, "install", "--python", sys.executable, target],
                                extra_env={
                                    "PIPX_HOME": str(home / "home"),
                                    "PIPX_BIN_DIR": str(bins),
                                    "PIPX_MAN_DIR": str(home / "man"),
                                },
                            )
                            executable = bins / "dv"
                        version = run([str(executable), "--version"], cwd=directory)
                        self.assertTrue(version.stdout.startswith("dv "))
                        import shlex

                        proc = run(
                            [str(executable), "prove", "--test", shlex.join(COMMAND), "--json"],
                            cwd=fixture.root,
                        )
                        self.assertTrue(json.loads(proc.stdout)["ok"])
                        prefix = executable.resolve().parent.parent
                        resources = prefix / "share/delivery-verifier"
                        for name in (
                            "docs/handbook/green-tests.md",
                            "docs/handbook/cases.json",
                            "examples/handbook/run.py",
                        ):
                            self.assertTrue((resources / name).is_file(), name)
                        self.assertTrue((prefix / "lib").is_dir())


if __name__ == "__main__":
    unittest.main()
