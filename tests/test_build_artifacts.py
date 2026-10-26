from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from support import ROOT


def machine_path_patterns() -> list[str]:
    prefixes = [("Users",), ("home",), ("var", "folders"),
                ("private", "var"), ("private", "tmp")]
    patterns = ["/" + "/".join(parts) + "/" for parts in prefixes]
    patterns.append("C:" + chr(92) + "Users")
    temporary = str(Path(tempfile.gettempdir()).resolve()).rstrip("/\\")
    if temporary not in ("/tmp", "/var/tmp"):
        patterns.append(temporary + os.sep)
    return patterns + [pattern.replace(chr(92), chr(92) * 2) for pattern in patterns]


def machine_path_hits(content: bytes) -> list[str]:
    return sorted({pattern for pattern in machine_path_patterns() if pattern.encode() in content})


def copy_build_source(target: Path) -> None:
    shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns(
        ".git", ".dv", ".venv", "artifacts", "build", "dist", "*.egg-info",
        "__pycache__", ".ruff_cache", "test-sandbox-*", "dv-sandbox-*"))


def build_distributions(root: Path) -> tuple[Path, Path]:
    source, output = root / "source", root / "dist"
    copy_build_source(source)
    subprocess.run([sys.executable, "-c", "import build_backend, sys; output = sys.argv[1]; "
                    "build_backend.build_wheel(output); "
                    "build_backend.build_sdist(output)", str(output)],
                   cwd=source, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                   timeout=120)
    wheel, = output.glob("*.whl")
    sdist, = output.glob("*.tar.gz")
    return wheel, sdist


class FixturePrivacyTests(unittest.TestCase):
    def test_every_committed_runner_fixture_has_no_machine_paths(self):
        fixtures = [path for path in (ROOT / "tests/fixtures/runners").rglob("*") if path.is_file()]
        self.assertGreater(len(fixtures), 50)
        for path in fixtures:
            with self.subTest(path=path.name):
                self.assertEqual(machine_path_hits(path.read_bytes()), [])

    def test_privacy_scan_recognizes_every_machine_path_form(self):
        for pattern in machine_path_patterns():
            with self.subTest(pattern=pattern):
                self.assertTrue(machine_path_hits((pattern + "fixture.js").encode()))
        self.assertFalse(machine_path_hits(b"<tmp>/node_modules/dv-probe/_vitest_runner.mjs"))


@unittest.skipUnless(importlib.util.find_spec("setuptools"), "build tests need setuptools")
class BuildArtifactTests(unittest.TestCase):
    def test_built_wheel_and_sdist_contain_no_machine_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            wheel, sdist = build_distributions(Path(directory))
            with zipfile.ZipFile(wheel) as bundle:
                for name in bundle.namelist():
                    with self.subTest(archive="wheel", member=name):
                        self.assertFalse(machine_path_hits(name.encode()))
                        self.assertFalse(machine_path_hits(bundle.read(name)))
            with tarfile.open(sdist) as bundle:
                for member in bundle.getmembers():
                    with self.subTest(archive="sdist", member=member.name):
                        self.assertFalse(machine_path_hits(member.name.encode()))
                        if member.isfile():
                            with bundle.extractfile(member) as stream:
                                self.assertFalse(machine_path_hits(stream.read()))

    def test_sdist_has_anonymous_owners_and_reproducible_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            copy_build_source(source)
            env = {key: value for key, value in os.environ.items() if key != "SOURCE_DATE_EPOCH"}
            archives = []
            for timestamp in (1000000000, 1700000000):
                for path in source.rglob("*"):
                    if path.is_file():
                        os.utime(path, (timestamp, timestamp))
                output = root / str(timestamp)
                proc = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import build_backend, sys; build_backend.build_sdist(sys.argv[1])",
                        str(output),
                    ],
                    cwd=source,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=60,
                )
                self.assertEqual(proc.returncode, 0, proc.stdout.decode())
                (archive,) = output.glob("*.tar.gz")
                archives.append(archive.read_bytes())
                self.assertEqual(int.from_bytes(archives[-1][4:8], "little"), 946684800)
                with tarfile.open(archive) as bundle:
                    members = bundle.getmembers()
                    self.assertGreater(len(members), 20)
                    self.assertTrue(any(m.name.endswith("/build_backend.py") for m in members))
                    for member in members:
                        self.assertEqual((member.uid, member.gid), (0, 0), member.name)
                        self.assertEqual(
                            (member.uname, member.gname), ("root", "root"), member.name
                        )
                        self.assertEqual(member.mtime, 946684800, member.name)
                        self.assertFalse(
                            set(member.pax_headers) - {"path", "linkpath"}, member.name
                        )
            self.assertEqual(archives[0], archives[1])
