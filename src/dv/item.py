"""Work-item validation and content-bound scopes."""

from __future__ import annotations

import copy
from pathlib import Path

from dv import config
from dv.checker import checker_digest
from dv.common import DVError, argv, canonical, digest, keys, nonblank, read_json, write_json
from dv.git import Repo
from dv.runners import RUNNERS

DEFAULTS = {
    "kind": "small",
    "status": "preparing",
    "test_cwd": ".",
    "test_runner": "auto",
    "test_files": [],
    "runtime_files": [],
    "exceptions": {},
}
FIELDS = set(DEFAULTS) | {
    "schema_version",
    "summary",
    "base_ref",
    "implementer",
    "blocked_reason",
    "test_command",
    "description_file",
}
SIDECARS = {
    "redgreen.json",
    "redgreen-red.log",
    "redgreen-green.log",
    "restoration.json",
    "redgreen-mutations.patch",
    "redgreen-mutations.json",
    "review.json",
    "review.txt",
    "approval.json",
    "hygiene.json",
    "gate.json",
}


def safe_path(root: Path, value: str, *, metadata: bool = False) -> Path:
    config.relative(value, "path")
    path = root / value
    if not path.resolve().is_relative_to(root):
        raise DVError(f"Path escapes repository: {value}")
    if metadata:
        for part in (path, *path.parents):
            if part == root:
                break
            if part.is_symlink():
                raise DVError(f"Symlinked metadata path: {value}")
    return path


class Item:
    def __init__(self, path: Path, *, allow_incomplete: bool = False):
        self.allow_incomplete = allow_incomplete
        self.repo = Repo(path.parent)
        self.root = self.repo.root
        try:
            relative = str(path.absolute().relative_to(self.root))
        except ValueError as error:
            raise DVError("Item must be inside its repository") from error
        self.path = safe_path(self.root, relative, metadata=True)
        self.directory = self.path.parent
        if self.path.name in SIDECARS or self.directory == self.root:
            raise DVError("Use a dedicated ignored item directory and a non-reserved item filename")
        self.metadata_path(self.path)
        raw = keys(read_json(self.path), FIELDS, "item")
        self.meta = {**copy.deepcopy(DEFAULTS), **raw}
        self.config = config.load(self.root)
        self.validate()
        self.head, self.base, self.base_tip = self.repo.scope(self.meta["base_ref"])
        for value in self.meta["test_files"]:
            entry = self.repo.entry(self.head, value)
            if not entry or not entry.startswith((b"100644 ", b"100755 ")):
                raise DVError(f"test_files must name regular tracked files at HEAD: {value}")
        self.attachments = self.attachment_hashes()
        bound = {
            key: value
            for key, value in self.meta.items()
            if key not in {"status", "blocked_reason"}
        }
        checker = checker_digest()
        self.scope = {
            "schema_version": 1,
            "checker_sha256": checker,
            "head_sha": self.head,
            "base_sha": self.base,
            "context_sha256": digest(
                canonical(
                    {
                        "item": bound,
                        "config": self.config,
                        "checker_sha256": checker,
                        "head_sha": self.head,
                        "base_sha": self.base,
                        "attachments": self.attachments,
                    }
                )
            ),
        }

    def metadata_path(self, path: Path) -> Path:
        safe_path(self.root, str(path.relative_to(self.root)), metadata=True)
        if not self.repo.ignored(path):
            raise DVError(f"Metadata must be untracked and ignored: {path.relative_to(self.root)}")
        return path

    def file(self, name: str) -> Path:
        return self.metadata_path(self.directory / name)

    def validate(self) -> None:
        m = self.meta
        if type(m.get("schema_version")) is not int or m["schema_version"] != 1:
            raise DVError("Item schema_version must be 1")
        for key in ("summary", "base_ref", "implementer"):
            nonblank(m.get(key), key)
        if m["kind"] not in ("small", "large"):
            raise DVError("kind must be small or large")
        if m["status"] not in ("preparing", "ready", "approved", "blocked"):
            raise DVError("Invalid item status")
        if m["status"] == "blocked":
            nonblank(m.get("blocked_reason"), "blocked_reason")
        elif "blocked_reason" in m:
            raise DVError("blocked_reason is only valid for status=blocked")
        keys(
            m["exceptions"],
            {
                "no_test_reason",
                "comment_justification",
                "lockfile_reason",
                "weak_test_justification",
            },
            "exceptions",
        )
        for key, value in m["exceptions"].items():
            nonblank(value, key)
        if not isinstance(m["test_runner"], str) or m["test_runner"] not in RUNNERS:
            raise DVError("test_runner must be auto, unittest, pytest, or node-test")
        if "test_command" in m:
            argv(m["test_command"], "test_command")
        elif not m["exceptions"].get("no_test_reason") and not (
            self.allow_incomplete and m["status"] in ("preparing", "blocked")
        ):
            raise DVError("Fill in test_command or a reasoned exceptions.no_test_reason")
        cwd = safe_path(self.root, m["test_cwd"])
        if not cwd.is_dir():
            raise DVError("test_cwd must be an existing directory")
        for key in ("test_files", "runtime_files"):
            if not isinstance(m[key], list):
                raise DVError(f"{key} must be an array")
            for value in m[key]:
                path = safe_path(self.root, value, metadata=key == "runtime_files")
                if not path.is_file():
                    raise DVError(f"{key} file does not exist: {value}")
                if key == "runtime_files":
                    self.metadata_path(path)
        if "description_file" in m:
            path = safe_path(self.root, m["description_file"], metadata=True)
            self.metadata_path(path)
        inputs = [self.root / p for p in m["runtime_files"]]
        if "description_file" in m:
            inputs.append(self.root / m["description_file"])
        reserved = {self.path} | {self.directory / name for name in SIDECARS}
        if any(p in reserved or (self.directory / "checks") in p.parents for p in inputs):
            raise DVError("Description/runtime inputs must not overlap item or output files")

    def attachment_hashes(self) -> dict:
        description = self.meta.get("description_file")
        path = self.root / description if description else None
        return {
            "description": digest(path.read_bytes()) if path and path.is_file() else None,
            "runtime": {
                p: digest((self.root / p).read_bytes()) for p in self.meta["runtime_files"]
            },
        }

    def ensure_current(self, *, clean: bool = True) -> None:
        current = Item(self.path)
        if current.scope != self.scope or current.base_tip != self.base_tip:
            raise DVError(
                "Scope changed while checking; rerun for the current inputs", 1, "scope_changed"
            )
        if clean:
            self.repo.require_clean()

    def set_status(self, status: str) -> None:
        self.ensure_current()
        meta = read_json(self.path)
        meta["status"] = status
        meta.pop("blocked_reason", None)
        write_json(self.path, meta)


def initialize(path: Path, implementer: str, base: str, kind: str) -> dict:
    repo = Repo(Path.cwd())
    nonblank(implementer, "implementer")
    head, base_sha, _ = repo.scope(base)
    path = path.absolute()
    if not path.is_relative_to(repo.root):
        raise DVError("Item must be inside the current repository")
    target = safe_path(repo.root, str(path.relative_to(repo.root)), metadata=True)
    if target.exists():
        raise DVError(f"Refusing to overwrite {target}")
    if target.name in SIDECARS or target.parent == repo.root:
        raise DVError("Use a dedicated ignored item directory")
    with repo.lock():
        if target == repo.root / ".dv/item.json":
            exclude = repo.git_path("info/exclude")
            if exclude.is_symlink():
                raise DVError("Refusing symlinked Git exclude file")
            exclude.parent.mkdir(parents=True, exist_ok=True)
            content = exclude.read_text() if exclude.exists() else ""
            if "/.dv/" not in content.splitlines():
                with exclude.open("a") as stream:
                    stream.write(
                        ("\n" if content and not content.endswith("\n") else "") + "/.dv/\n"
                    )
        if not repo.ignored(target):
            raise DVError("Custom item directory must already be ignored and untracked")
        if target.exists():
            raise DVError(f"Refusing to overwrite {target}")
        write_json(
            target,
            {
                "schema_version": 1,
                "summary": "Describe the changed behavior",
                "base_ref": base,
                "kind": kind,
                "implementer": implementer,
                **{k: v for k, v in DEFAULTS.items() if k != "kind"},
            },
        )
    from dv.common import result, row

    return result(
        "init",
        {"head_sha": head, "base_sha": base_sha},
        [
            row(
                "work item",
                "warn",
                "Fill in summary, test_command/test_files, and static/full-suite commands. "
                "The preparing template is not verification evidence.",
            )
        ],
        item=str(target),
    )
