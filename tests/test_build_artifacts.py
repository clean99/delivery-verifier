from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from support import ROOT


@unittest.skipUnless(importlib.util.find_spec("setuptools"), "sdist tests need setuptools")
class BuildArtifactTests(unittest.TestCase):
    def test_sdist_has_anonymous_owners_and_reproducible_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            shutil.copytree(
                ROOT,
                source,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".dv",
                    ".venv",
                    "artifacts",
                    "build",
                    "dist",
                    "*.egg-info",
                    "__pycache__",
                    ".ruff_cache",
                    "test-sandbox-*",
                    "dv-sandbox-*",
                ),
            )
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
