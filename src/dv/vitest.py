"""Validate the bundled Vitest reporter and test-body probe as one receipt."""

from __future__ import annotations

import json
import re
from pathlib import Path

from dv.common import digest

PREFIX = "DV-VITEST-REPORT "


def parse_report(text: str) -> tuple[dict, bool, bool, str]:
    counts = {"executed": 0, "passed": 0, "failed": 0, "skipped": 0}
    assertions = True
    seen = set()

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def errors(value):
        require(isinstance(value, list), "missing error list")
        require(all(
            isinstance(error, dict)
            and isinstance(error.get("name"), str)
            and isinstance(error.get("message"), str)
            for error in value
        ), "malformed error record")
        return value

    def reads(value):
        require(isinstance(value, list) and all(
            isinstance(read, dict) and isinstance(read.get("path"), str)
            and Path(read["path"]).is_absolute()
            and (read.get("sha256") is None or isinstance(read["sha256"], str)
                 and re.fullmatch(r"[0-9a-f]{64}", read["sha256"]))
            and "sha256" in read for read in value
        ), "missing or malformed snapshot reads")
        return {(read["path"], read["sha256"]) for read in value}

    def task(value, names=None):
        nonlocal assertions
        require(isinstance(value, dict), "malformed task")
        require(isinstance(value.get("name"), str), "missing task name")
        names = () if names is None else (*names, value["name"])
        identifier = value.get("id")
        require(isinstance(identifier, str) and identifier and identifier not in seen,
                "missing or duplicate task identity")
        seen.add(identifier)
        kind, mode = value.get("type"), value.get("mode")
        require(kind in ("suite", "test") and mode in ("run", "skip", "todo"),
                "unsupported task type or mode")
        children = value.get("tasks")
        require(isinstance(children, list) and (kind == "suite" or not children),
                "malformed task children")
        result = value.get("result")
        if result is None:
            require(mode in ("skip", "todo"), "missing task result")
            state, failures = mode, []
        else:
            require(isinstance(result, dict), "malformed task result")
            state = result.get("state")
            require(state in ("pass", "fail", "skip", "todo"), "unfinished task result")
            failures = errors(result.get("errors"))
            hooks = result.get("hooks")
            require(isinstance(hooks, dict) and all(
                name in ("beforeAll", "afterAll", "beforeEach", "afterEach") and state == "pass"
                for name, state in hooks.items()
            ), "failed or unfinished hook")
            require(all(type(result.get(key)) is int and result[key] == 0
                        for key in ("retryCount", "repeatCount")),
                    "retries and repeats are not assertion proof")
        require(value.get("fails") is False, "expected-failure tests are not proof")
        failed_children = sum(task(child, names) for child in children)
        if kind == "suite":
            require(not failures, "file/suite load, collection or lifecycle error")
            require((state == "fail") == bool(failed_children),
                    "suite state does not match failed descendants")
            return failed_children
        full_name = " > ".join(names)
        test_names[full_name] = test_names.get(full_name, 0) + 1
        if state in ("skip", "todo"):
            require(not failures, "skipped test has errors")
            counts["skipped"] += 1
            return 0
        test_reads = reads(value.get("snapshots"))
        require(test_reads <= file_reads,
                "test snapshot inputs are absent from the file receipt")
        identity = value.get("snapshotIdentity")
        require(isinstance(identity, dict) and isinstance(identity.get("names"), list)
                and identity["names"] and all(isinstance(name, str) for name in identity["names"])
                and type(identity.get("occurrence")) is int and identity["occurrence"] > 0,
                "missing snapshot test identity")
        require(tuple(identity["names"]) == names, "snapshot identity does not match test name")
        test_identity = (tuple(identity["names"]), identity["occurrence"])
        require(test_identity not in snapshot_identities, "duplicate snapshot test identity")
        snapshot_identities.add(test_identity)
        entries = value.get("snapshotEntries")
        reads(entries)
        require(all(isinstance(entry.get("key"), str) and entry["key"]
                    and entry.get("kind") in ("external", "raw", "inline")
                    and (entry["kind"] == "inline"
                         or entry["path"] in {path for path, _ in test_reads})
                    for entry in entries), "missing or malformed snapshot entries")
        if entries:
            snapshot_names.add(full_name)
        body = value.get("body")
        require(isinstance(body, dict) and body.get("nonce") == nonce,
                "missing test-body probe activation")
        if version == "4.1.12":
            require(body.get("task") == identifier and type(body.get("entered")) is int
                    and body["entered"] == 1 and type(body.get("finished")) is int
                    and body["finished"] == 1, "missing or incomplete test-body invocation")
            require(body.get("pendingSnapshots") == 0, "unbound custom snapshot matcher result")
        else:
            require(body.get("fixtures") is False, "extended test fixtures are unsupported")
        body_errors = errors(body.get("errors"))
        keys = ("name", "message", "token") if version == "4.1.12" else ("name", "message")
        if version == "4.1.12":
            require(all(isinstance(error.get("token"), str) and error["token"]
                        for error in body_errors), "missing live body-error token")
        require([{key: error.get(key) for key in keys} for error in body_errors]
                == failures, "errors outside the test body (hook, cleanup or callback)")
        counts["executed"] += 1
        if state == "pass":
            require(not failures, "passing test has errors")
            counts["passed"] += 1
            return 0
        require(bool(failures), "failed test has no error")
        counts["failed"] += 1
        assertions &= all(error.get("assertion") in ("chai", "node", "snapshot")
                          for error in body_errors)
        return 1

    try:
        records = re.findall(r"^" + PREFIX + r"(.+)$", text, re.M)
        require(len(records) == 1, "missing or duplicate machine report")
        record = json.loads(records[0])
        require(isinstance(record, dict), "malformed machine report")
        require(record.get("schema_version") == 7 and record.get("runner") == "vitest",
                "unsupported machine report schema")
        version = record.get("version")
        require(isinstance(version, str) and (re.fullmatch(r"3\.2\.\d+", version)
                                             or version == "4.1.12"),
                f"Unsupported Vitest version {version}; supported: 3.2.x")
        require(record.get("complete") is True,
                str(record.get("error", "incomplete machine report")))
        if version == "4.1.12":
            require(record.get("sourceVerified") is True, "unverified Vitest 4.1.12 sources")
            require(not errors(record.get("postCloseErrors")),
                    "post-close Vitest error (file/suite or worker teardown)")
            require(not errors(record.get("finalErrors")), "final Vitest error")
        require(record.get("shutdownComplete") is True, "shutdown did not complete")
        require(not errors(record.get("lateErrors")), "late process or shutdown error")
        require(record.get("lateStderr") == [], "stderr during or after shutdown")
        require(type(record.get("exitCode")) is int and record["exitCode"] in (0, 1),
                "abnormal process exit")
        nonce = record.get("nonce")
        require(isinstance(nonce, str) and re.fullmatch(r"[0-9a-f]{32}", nonce),
                "missing probe nonce")
        require(record.get("reason") in ("passed", "failed"), "interrupted test run")
        require(not errors(record.get("unhandledErrors")), "unhandled runner error")
        files = record.get("files")
        require(isinstance(files, list), "missing test files")
        file_identities = set()
        for file in files:
            require(isinstance(file, dict) and file.get("type") == "suite"
                    and file.get("probe") == nonce, "missing file probe activation")
            require(isinstance(file.get("filepath"), str)
                    and Path(file["filepath"]).is_absolute()
                    and isinstance(file.get("project"), str), "missing file identity")
            identity = (file["filepath"], file["project"])
            require(identity not in file_identities, "duplicate file identity")
            file_identities.add(identity)
            if version == "4.1.12":
                require(file.get("unsupportedSnapshots") is False,
                        "domain and polling snapshots are unsupported")
            file_reads = reads(file.get("snapshots"))
            snapshot_identities = set()
            test_names, snapshot_names = {}, set()
            task(file)
            for name in sorted(snapshot_names):
                require(test_names[name] == 1,
                        f"duplicate full name {name!r} for a snapshot test; "
                        "interpolate the case into the name, e.g. test.each(...)('value %s', ...)")
        require((record["reason"] == "failed") == bool(counts["failed"]),
                "run result does not match test results")
        require(record["exitCode"] == (1 if counts["failed"] else 0),
                "process exit does not match test results")
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        return counts, False, False, f"vitest: {error}"
    red = assertions and counts["failed"] > 0
    if not counts["executed"]:
        detail = "zero executed tests (empty, all skipped or todo)"
    elif not assertions:
        detail = "non-assertion test error (including runtime errors and timeouts)"
    else:
        detail = f"complete body-phase report, tests={counts}"
    return counts, True, red, f"vitest: {detail}"


def snapshot_inputs(content: bytes, root: Path, repo, head: str) -> tuple[bool, str]:
    text = content.decode("utf-8", errors="replace")
    _, complete, _, detail = parse_report(text)
    if not complete:
        return False, detail
    record = json.loads(re.findall(r"^" + PREFIX + r"(.+)$", text, re.M)[0])
    root = root.resolve()
    checked = set()
    for file in record["files"]:
        if not Path(file["filepath"]).is_relative_to(root):
            return False, "Test file is outside execution worktree"
        pending = [file]
        while pending:
            task = pending.pop()
            pending.extend(task["tasks"])
            for entry in task.get("snapshotEntries") or []:
                path = Path(entry["path"])
                if not path.is_relative_to(root) or {"..", ".git"}.intersection(path.parts):
                    return False, f"Snapshot entry outside execution worktree: {path}"
        for read in file["snapshots"]:
            path = Path(read["path"])
            try:
                relative = path.relative_to(root)
            except ValueError:
                return False, f"Snapshot read outside execution worktree: {path}"
            if ".." in relative.parts or ".git" in relative.parts:
                return False, f"Unsupported snapshot path: {relative}"
            name = relative.as_posix()
            entry = repo.entry(head, name)
            if entry and not entry.startswith((b"100644 blob ", b"100755 blob ")):
                return False, f"Snapshot is not a regular head file: {name}"
            expected = digest(repo.blob(head, name)) if entry else None
            if read["sha256"] != expected:
                return False, f"Snapshot read differs from head: {name}"
            if path.is_symlink() or any(parent.is_symlink() for parent in path.parents
                                        if parent != root and root in parent.parents):
                return False, f"Snapshot read follows a symlink: {name}"
            actual = digest(path.read_bytes()) if path.is_file() else None
            if actual != expected or path.exists() and not path.is_file():
                return False, f"Snapshot file differs from head: {name}"
            checked.add(name)
    return True, f"Snapshot reads match head ({len(checked)} paths, including absent files)"


def snapshot_oracles(content: bytes, root: Path) -> dict:
    record = json.loads(re.findall(r"^" + PREFIX + r"(.+)$", content.decode("utf-8", "replace"),
                                   re.M)[0])
    oracles = {}

    def inputs(reads):
        return frozenset((Path(read["path"]).relative_to(root).as_posix(), read["sha256"])
                         for read in reads)

    def visit(task, key):
        identity = task.get("snapshotIdentity")
        if task["type"] == "test" and identity:
            test_key = (*key, tuple(identity["names"]), identity["occurrence"])
            entries = tuple((Path(entry["path"]).relative_to(root).as_posix(), entry["kind"],
                             entry["key"], entry["sha256"])
                            for entry in task["snapshotEntries"])
            oracles[test_key] = (inputs(task["snapshots"]), entries)
        for child in task["tasks"]:
            visit(child, key)

    for file in record["files"]:
        key = (Path(file["filepath"]).relative_to(root).as_posix(), file["project"])
        oracles[(*key, (), 0)] = (inputs(file["snapshots"]), ())
        visit(file, key)
    return oracles


def snapshot_parity(red: dict, green: dict) -> tuple[bool, str]:
    for identity in sorted(red.keys() | green.keys()):
        left, right = red.get(identity), green.get(identity)
        if left == right:
            continue
        file, project, names, occurrence = identity
        label = f"{file} [{project}] test {' > '.join(names)!r} occurrence {occurrence}"
        if left is None or right is None:
            return False, f"Snapshot oracle differs for {label}: test missing in one phase"
        if left[1] != right[1]:
            for index in range(max(len(left[1]), len(right[1]))):
                before = left[1][index] if index < len(left[1]) else None
                after = right[1][index] if index < len(right[1]) else None
                if before != after:
                    return False, (f"Snapshot oracle differs for {label}, entry {index + 1}: "
                                   f"red={before!r}, green={after!r}")
        return False, f"Snapshot paths or raw hashes differ for {label}"
    return True, "Snapshot paths, raw hashes and ordered per-test entries match"


def harness_inputs(content: bytes, root: Path, repo, entries: dict) -> tuple[bool, dict, str]:
    inputs = {}
    try:
        records = re.findall(r"^" + PREFIX + r"(.+)$", content.decode("utf-8", "replace"), re.M)
        if len(records) != 1:
            raise ValueError("missing or duplicate harness report")
        record = json.loads(records[0])
        projects = record.get("harness") if isinstance(record, dict) else None
        if record.get("schema_version") != 7 or not isinstance(projects, list) or not projects:
            raise ValueError("missing resolved Vitest harness inputs")
        for project in projects:
            identity = tuple(project.get(key) for key in ("kind", "project", "root"))
            if not all(isinstance(value, str) for value in identity) or identity in inputs:
                raise ValueError("missing or duplicate harness project identity")
            hashes = project.get("configHashes")
            if not isinstance(hashes, dict) or not hashes or not all(
                isinstance(key, str) and isinstance(value, str)
                and re.fullmatch(r"[0-9a-f]{64}", value) for key, value in hashes.items()
            ):
                raise ValueError("missing effective harness configuration hashes")
            files = project.get("inputs")
            if not isinstance(files, list):
                raise ValueError("missing harness input list")
            paths = {}
            inputs[identity] = {"files": paths, "config": hashes}
            for file in files:
                name, expected = file["path"], file["sha256"]
                if not isinstance(name, str) or not name or Path(name).is_absolute() or any(
                    part in ("..", ".git") for part in Path(name).parts
                ) or name in paths:
                    raise ValueError("invalid or duplicate harness path")
                if expected is not None and (not isinstance(expected, str) or not re.fullmatch(
                    r"[0-9a-f]{64}", expected
                )):
                    raise ValueError("invalid harness input hash")
                external = file.get("external")
                if not isinstance(external, bool):
                    raise ValueError("missing harness input location")
                if external:
                    if not re.fullmatch(r"<(?:home|absolute)>/[0-9a-f]{64}/[^/\\\\]+", name):
                        raise ValueError("invalid external harness label")
                    paths[name] = (expected, external)
                    continue
                path = root / name
                if path.is_symlink() or not path.resolve().is_relative_to(root) or any(
                    parent.is_symlink() for parent in path.parents if root in parent.parents
                ):
                    raise ValueError(f"Harness input follows a symlink: {name}")
                actual = digest(path.read_bytes()) if path.is_file() else None
                if actual != expected or path.exists() and not path.is_file():
                    raise ValueError(f"Harness input changed during execution: {name}")
                entry = entries.get(name)
                if entry and (entry.mode not in ("100644", "100755") or
                              digest(repo.run("cat-file", "blob", entry.oid).stdout) != expected):
                    raise ValueError(f"Harness input differs from committed input: {name}")
                paths[name] = (expected, external)
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as error:
        return False, {}, f"vitest: {error}"
    return True, inputs, "Resolved harness paths and raw-byte hashes match execution inputs"


def harness_parity(red: dict, green: dict) -> tuple[bool, str]:
    changed = set()
    for project in red.keys() | green.keys():
        left, right = red.get(project), green.get(project)
        if left == right:
            continue
        if left is None or right is None:
            changed.update((left or right)["files"])
            changed.add(f"project {project[1]!r} ({project[2]})")
        else:
            keys = left["config"].keys() | right["config"].keys()
            changed.update(f"config key {key!r} (project {project[1]!r})" for key in keys
                           if left["config"].get(key) != right["config"].get(key))
            before, after = left["files"], right["files"]
            changed.update(
                path for path in before.keys() | after.keys()
                if path not in before or path not in after or before[path] != after[path])
    if changed:
        files = ", ".join(sorted(changed))
        return False, (
            "Change touches reported Vitest harness inputs or its effective configuration: " + files
            + "; dv cannot attribute the red result to product code")
    return True, "Reported Vitest harness inputs and effective configuration match"
