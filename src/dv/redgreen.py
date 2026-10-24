"""Revert production paths without changing the index; always restore before green."""

from __future__ import annotations

import ast
import base64
import os
import signal
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dv import hygiene, process
from dv.common import DVError, atomic_bytes, digest, result, row, write_json
from dv.item import Item


def pure_explicit_test(file: hygiene.DiffFile) -> bool:
    if file.path.endswith(".py"):
        try:
            tree = ast.parse(file.new)
        except SyntaxError:
            return False
        found = False
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test_"
            ):
                found = True
                continue
            if isinstance(node, ast.ClassDef) and all(
                isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                and member.name.startswith(("test_", "setUp", "tearDown"))
                for member in node.body
            ):
                found = True
                continue
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue
            return False
        return found
    if file.path.endswith(".rs"):
        start, ambiguous = hygiene.rust_test_start(file.new)
        return bool(start == 1 and not ambiguous)
    return False


def plan(item: Item) -> tuple[list[str], list[str]]:
    reverted, retained = [], []
    for file in hygiene.diff_files(item):
        path = file.path
        kind = hygiene.category(path, item.config, item.meta["test_files"])
        if path in item.meta["test_files"] and not hygiene.TEST_PATH.search(path):
            if not pure_explicit_test(file):
                raise DVError(
                    f"Cannot retain mixed/unrecognized production file as a test: {path}. "
                    "Use a separately retained regression test.",
                    1,
                    "mixed_test_file",
                )
        if path.endswith(".rs") and kind == "src":
            start, ambiguous = hygiene.rust_test_start(file.new)
            old_start, old_ambiguous = hygiene.rust_test_start(file.old)
            if start or old_start or ambiguous or old_ambiguous:
                raise DVError(
                    f"Mixed Rust source/test proof is not supported: {path}. "
                    "This includes files with existing inline tests, even when inline tests "
                    "are unchanged. A separate regression test alone is insufficient; "
                    "separate all inline tests from changed production files first.",
                    1,
                    "mixed_test_file",
                )
        if kind in ("test", "snapshot"):
            if file.status != "D" and file.added and not file.binary:
                retained.append(path)
        elif kind != "doc":
            for ref in (item.head, item.base):
                entry = item.repo.entry(ref, path)
                if entry.startswith((b"160000 ", b"040000 ")):
                    raise DVError(
                        f"Cannot safely revert submodule/directory: {path}", 3, "unsupported_path"
                    )
            reverted.append(path)
    if not reverted or not retained:
        raise DVError(
            "Red/green needs changed production paths and a retained regression test",
            1,
            "missing_proof_inputs",
        )
    return reverted, retained


def restore_path(item: Item, ref: str, path: str) -> None:
    target = item.root / path
    for parent in target.parents:
        if parent == item.root:
            break
        if parent.is_symlink():
            raise DVError(
                f"Refusing restoration through symlinked parent: {path}", 3, "restoration_failed"
            )
    if item.repo.entry(ref, path):
        item.repo.run("restore", f"--source={ref}", "--worktree", "--", path)
    elif target.is_file() or target.is_symlink():
        target.unlink()
    elif target.exists():
        raise DVError(
            f"Expected file, found directory while restoring {path}", 3, "restoration_failed"
        )


@contextmanager
def interrupted_signals() -> Iterator[None]:
    def interrupt(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def phase_ok(phase: object, *, red: bool = False) -> bool:
    if not isinstance(phase, dict) or phase.get("status") != "normal":
        return False
    code = phase.get("exit_code")
    return type(code) is int and (0 < code < 124 if red else code == 0)


def path_state(path: Path) -> dict:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return {"kind": "absent"}
    content = b""
    if stat.S_ISLNK(mode):
        kind, content = "symlink", os.fsencode(os.readlink(path))
    elif stat.S_ISREG(mode):
        kind, content = "file", path.read_bytes()
    else:
        kind = "directory" if stat.S_ISDIR(mode) else "special"
    return {
        "kind": kind,
        "mode": mode,
        "content": base64.b64encode(content).decode("ascii"),
    }


def run_phase(item: Item, phase: str, command: list[str], cwd: Path, timeout: int) -> dict:
    with tempfile.TemporaryDirectory(prefix=f".{phase}-cache-", dir=item.path.parent) as cache:
        env = {**os.environ, "PYTHONPYCACHEPREFIX": cache, "PYTHONDONTWRITEBYTECODE": "1"}
        return process.run(command, cwd, item.file(f"redgreen-{phase}.log"), timeout, env=env)


def run(item: Item, timeout: int | None = None) -> dict:
    if os.name != "posix":
        raise DVError("Red/green restoration requires POSIX", 3, "unsupported_platform")
    with item.repo.lock(), interrupted_signals():
        item.ensure_current()
        if "test_command" not in item.meta:
            raise DVError("No focused test command configured")
        write_json(
            item.file("redgreen.json"),
            result(
                "redgreen",
                item.scope,
                [row("attempt", "fail", "Proof in progress")],
                state="running",
            ),
        )
        reverted, retained = plan(item)
        for path in reverted:
            if not item.repo.entry(item.head, path) and os.path.lexists(item.root / path):
                raise DVError(
                    f"Restoration would overwrite an existing local path: {path}. "
                    "Move it aside yourself before retrying, even if Git ignores it.",
                    1,
                    "path_collision",
                )
        original_states = {path: path_state(item.root / path) for path in reverted}
        command = item.meta["test_command"]
        cwd = item.root / item.meta["test_cwd"]
        timeout = timeout or item.config["timeout_seconds"]
        index = item.repo.index()
        recovery = item.file("restoration.json")
        if recovery.exists():
            raise DVError(
                "An earlier restoration plan remains; inspect restoration.json before retrying",
                3,
                "restoration_pending",
            )
        write_json(
            recovery,
            {
                **item.scope,
                "reverted_paths": reverted,
                "restore_to": item.head,
                "index_changed_by_dv": False,
            },
        )
        red_mutated = False
        try:
            for path in reverted:
                restore_path(item, item.base, path)
            expected = item.repo.run(
                "diff", "--binary", "--no-ext-diff", "--no-textconv", item.head
            ).stdout
            before = {path: path_state(item.root / path) for path in reverted}
            try:
                red = run_phase(item, "red", command, cwd, timeout)
            finally:
                after = {path: path_state(item.root / path) for path in reverted}
                actual = item.repo.run(
                    "diff", "--binary", "--no-ext-diff", "--no-textconv", item.head
                ).stdout
                mutations = [
                    {"path": path, "before": before[path], "after": after[path]}
                    for path in reverted
                    if before[path] != after[path]
                ]
                red_mutated = bool(mutations) or digest(expected) != digest(actual)
                if red_mutated:
                    atomic_bytes(item.file("redgreen-mutations.patch"), actual)
                    write_json(item.file("redgreen-mutations.json"), {"paths": mutations})
        finally:
            problems = []
            for path in reverted:
                try:
                    restore_path(item, item.head, path)
                    if original_states[path]["kind"] == "file":
                        (item.root / path).chmod(stat.S_IMODE(original_states[path]["mode"]))
                except (OSError, DVError) as error:
                    problems.append(f"{path}: {error}")
            if problems:
                raise DVError(
                    "Restoration failed; keep restoration.json and inspect " + "; ".join(problems),
                    3,
                    "restoration_failed",
                )
            for path in reverted:
                proc = item.repo.run("diff", "--quiet", item.head, "--", path, check=False)
                if proc.returncode or path_state(item.root / path) != original_states[path]:
                    raise DVError(
                        f"Restoration verification failed: {path}; inspect restoration.json",
                        3,
                        "restoration_failed",
                    )
            recovery.unlink()
        checks = []
        if red_mutated:
            checks.append(
                row(
                    "red mutations",
                    "fail",
                    "Test changed content, type, or mode during red; "
                    "saved redgreen-mutations.patch and redgreen-mutations.json",
                    "Inspect the saved patch; production paths were restored to HEAD.",
                )
            )
        if item.repo.index() != index:
            checks.append(row("index unchanged", "fail", "Focused test mutated the index"))
        problem = item.repo.clean_problem()
        if problem:
            checks.append(
                row(
                    "red cleanup",
                    "fail",
                    problem,
                    "Inspect test-created files; no unrelated files were removed.",
                )
            )
        item.ensure_current(clean=False)
        green = None
        if not checks and red["status"] not in ("timeout", "launch_error", "signal"):
            green = run_phase(item, "green", command, cwd, timeout)
        checks.append(
            row(
                "red/green",
                "pass" if phase_ok(red, red=True) and phase_ok(green) else "fail",
                f"red={red['exit_code']} ({red['status']}), green={green}",
                (
                    "Inspect the logs: the focused regression must fail without the fi"
                    "x and pass with it."
                ),
            )
        )
        problem = item.repo.clean_problem()
        if problem:
            checks.append(
                row("green cleanup", "fail", problem, "Inspect mutations left by the test command.")
            )
        if item.repo.index() != index:
            checks.append(row("index unchanged", "fail", "Test command mutated the index"))
        item.ensure_current(clean=False)
        operational = any(
            p and p["status"] in ("timeout", "launch_error", "signal") for p in (red, green)
        )
        output = result(
            "redgreen",
            item.scope,
            checks,
            argv=command,
            cwd=item.meta["test_cwd"],
            retained_tests=retained,
            reverted_paths=reverted,
            timeout_seconds=timeout,
            state="complete",
            red=red,
            green=green,
            exit_code=3
            if operational
            else (1 if any(r["outcome"] == "fail" for r in checks) else 0),
        )
        write_json(item.file("redgreen.json"), output)
        return output
