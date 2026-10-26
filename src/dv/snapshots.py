"""Owned detached worktrees, materialized from raw local Git blobs without filters."""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from dv import config, hygiene
from dv.common import DVError, digest, write_json
from dv.git import Repo
from dv.redgreen import pure_explicit_test
from dv.runners import command_path_indices, vitest_index


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
        if any(part.rstrip(". ").casefold() == ".git" for part in Path(path).parts):
            raise DVError(f"Reserved Git administrative path: {path}", 2, "unsupported_path")
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
        return hygiene.category(path, policy, explicit) in (
            "test", "snapshot")

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


_CLONEFILE = (getattr(ctypes.CDLL(None, use_errno=True), "clonefile", None)
              if sys.platform == "darwin" else None)
if _CLONEFILE is not None:
    _CLONEFILE.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    _CLONEFILE.restype = ctypes.c_int


def clone_file(source: Path, target: Path) -> None:
    if sys.platform == "darwin" and _CLONEFILE is not None:
        if _CLONEFILE(os.fsencode(source), os.fsencode(target), 0):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(source))
    elif sys.platform.startswith("linux"):
        import fcntl

        with source.open("rb") as origin, target.open("xb") as destination:
            try:
                fcntl.ioctl(destination.fileno(), 0x40049409, origin.fileno())  # FICLONE
            except OSError:
                target.unlink()
                raise
    else:
        raise OSError(errno.ENOSYS, "Filesystem cloning unavailable")


class DependencyCopier:
    reserve = 64 * 1024 * 1024
    excluded = {".vite", ".vitest", ".cache"}

    def __init__(self, root: Path, sources: list[Path]):
        self.root = root
        self.sources = sources
        self.fallback_checked = False
        self.cloned_files = 0
        self.copied_files = 0

    def required_space(self) -> int:
        block = os.statvfs(self.root).f_frsize or 4096
        required = self.reserve
        for source in self.sources:
            for directory, directories, files in os.walk(source):
                directories[:] = [name for name in directories if name not in self.excluded]
                required += block
                for name in directories + files:
                    if name in self.excluded:
                        continue
                    info = (Path(directory) / name).lstat()
                    if stat.S_ISREG(info.st_mode):
                        required += ((info.st_size + block - 1) // block) * block
                    elif stat.S_ISLNK(info.st_mode):
                        required += block
        return required

    def require_space(self, required: int) -> None:
        free = shutil.disk_usage(self.root).free
        if free < required:
            raise DVError(
                f"Dependency cloning is unsupported and copying needs {required} free bytes "
                f"(including reserve), but only {free} are available; free space or use a "
                "clone-capable filesystem", 3, "insufficient_space",
            )

    def __call__(self, source, target):
        source, target = Path(source), Path(target)
        try:
            clone_file(source, target)
        except OSError as error:
            if error.errno not in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS,
                                   errno.ENOTTY, errno.EXDEV, errno.EINVAL):
                raise
            if not self.fallback_checked:
                self.require_space(self.required_space())
                self.fallback_checked = True
            self.require_space(source.stat().st_size + self.reserve)
            shutil.copy2(source, target)
            self.copied_files += 1
        else:
            shutil.copystat(source, target)
            self.cloned_files += 1
        return str(target)


class Snapshots:
    def __init__(self, repo: LocalRepo, directory: Path):
        self.repo = repo
        self.directory = directory
        self.owned: list[Path] = []
        self.caches: list[Path] = []
        self.recovery = directory / "recovery.json"
        self.execution_root: Path | None = None

    def save_recovery(self):
        write_json(
            self.recovery,
            {
                "worktrees": [str(path) for path in self.owned],
                "caches": [str(path) for path in self.caches],
                "execution_root": str(self.execution_root) if self.execution_root else None,
                "instruction": "Inspect these owned paths and registrations; never prune globally.",
            },
        )

    def create(self, phase: str, ref: str, entries: dict[str, Entry]) -> tuple[LocalRepo, Path]:
        if self.execution_root is None:
            temporary = Path(tempfile.gettempdir()).resolve()
            if temporary.is_relative_to(self.repo.root) or ".git" in temporary.parts:
                raise DVError("Proof temporary directory must be outside the repository and .git")
            self.execution_root = Path(tempfile.mkdtemp(prefix="dv-proof-", dir=temporary))
        path = self.execution_root / phase
        cache = self.execution_root / f"{phase}-cache"
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
                with target.open("xb") as stream:
                    stream.write(data)
                target.chmod(0o755 if entry.mode == "100755" else 0o644)
        # Raw blobs avoid smudge filters, checkout hooks, and external downloads.
        for target, destination in links:
            target.symlink_to(destination)
        return worktree, cache

    def copy_dependencies(self, root: Path, entries: dict[str, Entry]) -> DependencyCopier:
        parents = {parent for name in entries for parent in Path(name).parents}
        copies = []
        for parent in sorted(parents):
            target = root / parent / "node_modules"
            if os.path.lexists(target) and (target.is_symlink() or not target.is_dir()):
                continue
            source = self.repo.root / parent / "node_modules"
            if parent == Path(".") and not source.is_dir():
                source = next((directory / "node_modules" for directory in self.repo.root.parents
                               if (directory / "node_modules").is_dir()), source)
            if source.is_dir():
                copies.append((source.resolve(), target))
        mappings = sorted([*copies, (self.repo.root, root)],
                          key=lambda pair: len(pair[0].parts), reverse=True)
        copier = DependencyCopier(root, [source for source, _ in copies])
        def merge(source, target):
            if os.path.lexists(target):
                if target.is_symlink() or not target.is_dir() or not source.is_dir():
                    return
                if source.is_symlink() and not any(
                    source.resolve().is_relative_to(original) for original, _ in copies
                ):
                    raise DVError("Tracked dependency directory conflicts with a link outside "
                                  "copied dependencies", 2, "unsupported_path")
            elif source.is_symlink():
                target.symlink_to(os.readlink(source))
                return
            elif not source.is_dir():
                copier(source, target)
                return
            else:
                target.mkdir()
            for child in source.iterdir():
                if child.name not in copier.excluded:
                    merge(child, target / child.name)

        for source, target in copies:
            merge(source, target)
        for source, target in copies:
            for directory, directories, files in os.walk(target):
                for name in directories + files:
                    path = Path(directory) / name
                    if path.relative_to(root).as_posix() in entries:
                        continue
                    if path.is_symlink():
                        original = (source / path.relative_to(target)).resolve()
                        mapped = next((new / original.relative_to(old) for old, new in mappings
                                       if original.is_relative_to(old)), None)
                        if mapped is None:
                            raise DVError(f"Dependency symlink leaves copied inputs: {original}",
                                          2, "unsupported_path")
                        path.unlink()
                        path.symlink_to(os.path.relpath(mapped, path.parent))
                    elif path.parent.name == ".bin" and path.is_file():
                        # pnpm shims embed NODE_PATH; never leave a fallback into the caller's tree.
                        data = path.read_bytes()
                        if b"\0" not in data:
                            for old, new in mappings:
                                data = data.replace(os.fsencode(old), os.fsencode(new))
                            path.write_bytes(data)
        return copier

    def command(self, command: list[str], root: Path) -> list[str]:
        rewritten = []
        paths = command_path_indices(command)
        for index, argument in enumerate(command):
            if index not in paths:
                rewritten.append(argument)
                continue
            prefix, separator, value = argument.partition("=") if argument.startswith("-") else (
                "", "", argument
            )
            if argument.startswith("-") and not separator:
                rewritten.append(argument)
                continue
            if not value:
                rewritten.append(argument)
                continue
            candidate = (shutil.which(value) if index == 0 and "/" not in value else value)
            path = Path(os.path.abspath(self.repo.root / (candidate or value)))
            resolved = path.resolve()
            if index > 0 and vitest_index(command) is not None and (
                not path.is_relative_to(self.repo.root)
                or not resolved.is_relative_to(self.repo.root)
            ):
                raise DVError(f"Vitest path must stay inside the repository: {value!r}",
                              2, "unsupported_path")
            local = path if path.is_relative_to(self.repo.root) else resolved
            if local.is_relative_to(self.repo.root) and (
                path.exists() or Path(value).is_absolute()
            ):
                target = root / local.relative_to(self.repo.root)
                if (not target.exists() or not target.resolve().is_relative_to(root)
                        or index == 0 and target.name != Path(value).name):
                    raise DVError(
                        f"Cannot isolate command path {value!r}; use a repo-relative path "
                        "to a committed file or prepared Node dependency", 2, "unsupported_path"
                    )
                if index == 0 or Path(value).is_absolute() or ".." in Path(value).parts:
                    value = str(target)
            elif str(self.repo.root) in value:
                raise DVError(
                    f"Ambiguous checkout path in argument {argument!r}; use a repo-relative path",
                    2, "unsupported_path",
                )
            rewritten.append(prefix + separator + value)
        return rewritten

    def registrations(self) -> set[Path]:
        output = self.repo.run("worktree", "list", "--porcelain", "-z").stdout
        return {
            Path(os.fsdecode(field[len(b"worktree ") :])).absolute()
            for field in output.split(b"\0")
            if field.startswith(b"worktree ")
        }

    def cleanup(self) -> list[str]:
        remaining = []
        for path in self.owned:
            try:
                if path.is_symlink():
                    remaining.append(str(path))
                    continue
                if path in self.registrations():
                    self.repo.run("worktree", "remove", "--force", str(path))
                elif path.exists():
                    shutil.rmtree(path)
                if path.exists() or path in self.registrations():
                    remaining.append(str(path))
            except (DVError, OSError):
                remaining.append(str(path))
        for path in self.caches:
            try:
                if path.exists():
                    shutil.rmtree(path)
            except OSError:
                remaining.append(str(path))
        if not remaining and self.execution_root:
            try:
                self.execution_root.rmdir()
            except OSError:
                remaining.append(str(self.execution_root))
        if not remaining:
            self.recovery.unlink(missing_ok=True)
        return remaining
