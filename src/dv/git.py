"""Local Git access; no branch switching or remote operations."""

from __future__ import annotations

import os
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dv.common import DVError

SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


class Repo:
    def __init__(self, location: Path):
        location = location.absolute()
        while not location.is_dir() and location != location.parent:
            location = location.parent
        self.root = location
        found = self.run("rev-parse", "--show-toplevel", check=False)
        if found.returncode:
            raise DVError(f"Not a Git worktree: {location}")
        self.root = Path(os.fsdecode(found.stdout).strip()).resolve()
        partial = self.run(
            "config",
            "--get-regexp",
            r"^(remote\..*\.promisor|extensions\.partialclone)$",
            check=False,
        )
        if (
            partial.stdout
            and self.run("--no-lazy-fetch", "rev-parse", "--git-dir", check=False).returncode
        ):
            raise DVError(
                "Partial clones require Git with --no-lazy-fetch support; "
                "upgrade Git or prepare a complete local clone yourself.",
                3,
                "unsupported_partial_clone",
            )

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_",))}
        env.update(
            GIT_TERMINAL_PROMPT="0",
            GIT_OPTIONAL_LOCKS="0",
            GIT_NO_LAZY_FETCH="1",
            GIT_ALLOW_PROTOCOL="",
        )
        if args[0] != "check-ignore":
            env["GIT_LITERAL_PATHSPECS"] = "1"
        try:
            proc = subprocess.run(
                ["git", "-C", str(self.root), *args],
                capture_output=True,
                env=env,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DVError(f"Git operation failed: {error}", 3, "git_error") from error
        if check and proc.returncode:
            message = os.fsdecode(proc.stderr).strip()
            raise DVError(f"git {args[0]} failed: {message}", 3, "git_error")
        return proc

    def text(self, *args: str) -> str:
        return os.fsdecode(self.run(*args).stdout).strip()

    def resolve(self, ref: str) -> str:
        proc = self.run(
            "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}", check=False
        )
        sha = proc.stdout.decode("ascii", "replace").strip()
        if proc.returncode or not SHA.fullmatch(sha):
            raise DVError(f"Not a local commit/ref: {ref}")
        return sha

    def scope(self, base_ref: str) -> tuple[str, str, str]:
        head, tip = self.resolve("HEAD"), self.resolve(base_ref)
        base = self.text("merge-base", head, tip)
        if not SHA.fullmatch(base):
            raise DVError("HEAD and base have no unique merge base", 3, "git_error")
        return head, base, tip

    def git_path(self, name: str) -> Path:
        path = Path(self.text("rev-parse", "--git-path", name))
        return path if path.is_absolute() else self.root / path

    def clean_problem(self) -> str:
        for name in (
            "MERGE_HEAD",
            "rebase-merge",
            "rebase-apply",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "sequencer",
        ):
            if self.git_path(name).exists():
                return f"Git operation in progress: {name}"
        flags = self.run("ls-files", "-v", "-z").stdout.split(b"\0")
        if any(entry and (entry[:1].islower() or entry[:1] == b"S") for entry in flags):
            return "Clear assume-unchanged/skip-worktree flags before verification"
        status = self.run(
            "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none"
        ).stdout
        return (
            "Commit or remove worktree/index changes and nonignored untracked files"
            if status
            else ""
        )

    def require_clean(self) -> None:
        problem = self.clean_problem()
        if problem:
            raise DVError(problem, 1, "dirty_worktree")

    def changes(self, base: str, head: str) -> list[tuple[str, str]]:
        parts = self.run("diff", "--name-status", "-z", "--no-renames", base, head).stdout
        tokens = parts.split(b"\0")[:-1]
        return [
            (os.fsdecode(tokens[i]), os.fsdecode(tokens[i + 1])) for i in range(0, len(tokens), 2)
        ]

    def blob(self, ref: str, path: str) -> bytes:
        proc = self.run("show", f"{ref}:{path}", check=False)
        if proc.returncode:
            if self.entry(ref, path):
                raise DVError(
                    f"Cannot read local committed blob: {ref}:{path}. "
                    "Automatic fetching is disabled; prepare missing objects yourself.",
                    3,
                    "git_error",
                )
            return b""
        return proc.stdout

    def entry(self, ref: str, path: str) -> bytes:
        return self.run("ls-tree", "-z", ref, "--", path).stdout

    def index(self) -> bytes:
        return self.run("ls-files", "--stage", "-z").stdout

    def ignored(self, path: Path) -> bool:
        relative = str(path.relative_to(self.root))
        tracked = self.run("ls-files", "-z", "--", relative).stdout
        if tracked:
            return False
        return self.run("check-ignore", "-q", "--", relative, check=False).returncode == 0

    @contextmanager
    def lock(self) -> Iterator[None]:
        if os.name != "posix":
            raise DVError("Local worktree locking requires POSIX", 3, "unsupported_platform")
        import fcntl

        path = self.git_path("dv-worktree.lock")
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise DVError("Another dv operation owns this worktree", 3, "locked") from error
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
