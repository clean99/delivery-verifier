"""Owned detached worktrees, materialized from raw local Git blobs without filters."""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from dv import config, hygiene
from dv.common import DVError, digest, write_json
from dv.git import Repo
from dv.redgreen import pure_explicit_test


class LocalRepo(Repo):
    def run(self, *args: str, check: bool = True):
        return super().run(
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "submodule.recurse=false",
            *args,
            check=check,
        )


@dataclass(frozen=True)
class Entry:
    mode: str
    oid: str


def tree(repo: Repo, ref: str) -> dict[str, Entry]:
    entries = {}
    for record in repo.run("ls-tree", "-r", "-z", ref).stdout.split(b"\0"):
        if not record:
            continue
        header, raw_path = record.split(b"\t", 1)
        mode, kind, oid = header.decode("ascii").split()
        path = os.fsdecode(raw_path)
        config.relative(path, "tracked path")
        if kind != "blob" or mode not in ("100644", "100755", "120000"):
            raise DVError(
                f"Unsupported materialization (including submodules): {path}", 2, "unsupported_path"
            )
        entries[path] = Entry(mode, oid)
    return entries


def content(repo: Repo, entry: Entry) -> bytes:
    data = repo.run("cat-file", "blob", entry.oid).stdout
    if data.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
        raise DVError(
            "LFS pointers are unsupported; no hydration is performed", 2, "unsupported_path"
        )
    return data


def retained_inputs(repo, base, head, policy, explicit):
    for path in explicit:
        config.relative(path, "test file")
        entry = head.get(path)
        if entry is None or entry.mode not in ("100644", "100755"):
            raise DVError(f"Test file must be regular and tracked at head: {path}")
        if not hygiene.TEST_PATH.search(path):
            file = hygiene.DiffFile(path, "M", "", content(repo, entry).decode("utf-8", "replace"))
            if not pure_explicit_test(file):
                raise DVError(
                    f"Cannot retain mixed/unrecognized test file: {path}", 2, "mixed_test_file"
                )

    def is_test(path):
        return hygiene.category(path, policy, explicit) in ("test", "snapshot")

    retained = {path: entry for path, entry in head.items() if is_test(path)}
    if any(entry.mode not in ("100644", "100755") for entry in retained.values()):
        raise DVError("Retained tests must be regular files, not symlinks", 2, "unsupported_path")
    production = [
        path
        for path in base.keys() | head.keys()
        if base.get(path) != head.get(path)
        and not is_test(path)
        and hygiene.category(path, policy) != "doc"
    ]
    if not production or not retained:
        raise DVError(
            "Proof needs a production difference and retained tests", 1, "missing_proof_inputs"
        )
    for path in production:
        if path.endswith(".rs"):
            for entries in (base, head):
                if path in entries:
                    start, ambiguous = hygiene.rust_test_start(
                        content(repo, entries[path]).decode("utf-8", "replace")
                    )
                    if start or ambiguous:
                        raise DVError(
                            f"Mixed Rust source/test proof is unsupported: {path}",
                            2,
                            "mixed_test_file",
                        )
    red = {path: entry for path, entry in base.items() if not is_test(path)}
    red.update(retained)
    # A retained file may not replace a production directory (or vice versa).
    for path in red:
        if any(str(parent) in red for parent in Path(path).parents if str(parent) != "."):
            raise DVError(
                f"Test overlay has a file/directory collision: {path}", 2, "unsupported_path"
            )
    return red, retained


def file_state(root: Path, paths) -> dict:
    records = {}
    for path in paths:
        target = root / path
        # Never follow a changed parent symlink while inspecting mutations.
        if any(p.is_symlink() for p in target.parents if p != root and root in p.parents):
            records[path] = {"kind": "symlink-parent"}
            continue
        try:
            mode = target.lstat().st_mode
        except FileNotFoundError:
            records[path] = {"kind": "absent"}
            continue
        if stat.S_ISREG(mode):
            value = {"kind": "file", "sha256": digest(target.read_bytes())}
        elif stat.S_ISLNK(mode):
            value = {"kind": "symlink", "target": os.readlink(target)}
        else:
            value = {"kind": "special"}
        records[path] = {**value, "mode": stat.S_IMODE(mode)}
    return records


class Snapshots:
    def __init__(self, repo: LocalRepo, directory: Path):
        self.repo = repo
        self.directory = directory
        self.owned: list[Path] = []
        self.caches: list[Path] = []
        self.recovery = directory / "recovery.json"

    def save_recovery(self):
        write_json(
            self.recovery,
            {
                "worktrees": [str(path) for path in self.owned],
                "caches": [str(path) for path in self.caches],
                "instruction": "Inspect these owned paths and registrations; never prune globally.",
            },
        )

    def create(self, phase: str, ref: str, entries: dict[str, Entry]) -> tuple[LocalRepo, Path]:
        path, cache = self.directory / phase, self.directory / f"{phase}-cache"
        self.owned.append(path)
        self.caches.append(cache)
        self.save_recovery()
        cache.mkdir()
        self.repo.run("worktree", "add", "--detach", "--no-checkout", str(path), ref)
        worktree = LocalRepo(path)
        worktree.run("read-tree", ref)
        links = []
        for name, entry in entries.items():
            target = path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            data = content(self.repo, entry)
            if entry.mode == "120000":
                destination = os.fsdecode(data)
                if not (target.parent / destination).resolve().is_relative_to(path):
                    raise DVError(
                        f"Symlink escapes execution worktree: {name}", 2, "unsupported_path"
                    )
                links.append((target, destination))
            else:
                target.write_bytes(data)
                target.chmod(0o755 if entry.mode == "100755" else 0o644)
        # Raw blobs avoid smudge filters, checkout hooks, and external downloads.
        for target, destination in links:
            target.symlink_to(destination)
        return worktree, cache

    def registrations(self) -> set[Path]:
        output = self.repo.run("worktree", "list", "--porcelain", "-z").stdout
        return {
            Path(os.fsdecode(field[len(b"worktree ") :])).resolve()
            for field in output.split(b"\0")
            if field.startswith(b"worktree ")
        }

    def cleanup(self) -> list[str]:
        remaining = []
        for path in self.owned:
            try:
                if path.resolve() in self.registrations():
                    self.repo.run("worktree", "remove", "--force", str(path))
                elif path.exists():
                    shutil.rmtree(path)
                if path.exists() or path.resolve() in self.registrations():
                    remaining.append(str(path))
            except (DVError, OSError):
                remaining.append(str(path))
        for path in self.caches:
            try:
                if path.exists():
                    shutil.rmtree(path)
            except OSError:
                remaining.append(str(path))
        if not remaining:
            self.recovery.unlink(missing_ok=True)
        return remaining
