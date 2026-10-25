"""Setuptools builds with deterministic timestamps and anonymous archive headers."""

from __future__ import annotations

import gzip
import os
import tarfile
import tempfile
from pathlib import Path

from setuptools import build_meta
from setuptools.build_meta import build_editable as build_editable
from setuptools.build_meta import build_wheel as build_wheel
from setuptools.build_meta import get_requires_for_build_editable as get_requires_for_build_editable
from setuptools.build_meta import get_requires_for_build_sdist as get_requires_for_build_sdist
from setuptools.build_meta import get_requires_for_build_wheel as get_requires_for_build_wheel
from setuptools.build_meta import (
    prepare_metadata_for_build_editable as prepare_metadata_for_build_editable,
)
from setuptools.build_meta import (
    prepare_metadata_for_build_wheel as prepare_metadata_for_build_wheel,
)

os.environ.setdefault("SOURCE_DATE_EPOCH", "946684800")


def build_sdist(sdist_directory, config_settings=None):
    name = build_meta.build_sdist(sdist_directory, config_settings)
    archive = Path(sdist_directory) / name
    epoch = int(os.environ["SOURCE_DATE_EPOCH"])
    with tempfile.NamedTemporaryFile(dir=archive.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with tarfile.open(archive, "r:gz") as source, temporary_path.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed:
                with tarfile.open(
                    fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
                ) as target:
                    for member in sorted(source.getmembers(), key=lambda member: member.name):
                        member.uid = member.gid = 0
                        member.uname = member.gname = "root"
                        member.mtime = epoch
                        member.mode = 0o755 if member.isdir() or member.mode & 0o111 else 0o644
                        member.pax_headers = {}
                        stream = source.extractfile(member) if member.isfile() else None
                        try:
                            target.addfile(member, stream)
                        finally:
                            if stream is not None:
                                stream.close()
        temporary_path.replace(archive)
    finally:
        temporary_path.unlink(missing_ok=True)
    return name
