"""One-command regression proof over committed snapshots, separate from delivery gates."""

from __future__ import annotations

import os
import shlex
import signal
import uuid
from pathlib import Path
from types import SimpleNamespace

from dv import config, evidence, hygiene, process
from dv.checker import checker_digest
from dv.common import DVError, canonical, digest, nonblank, result, row, write_json
from dv.execution import green_execution, receipt
from dv.item import Item
from dv.redgreen import interrupted_signals
from dv.runners import instrument, red_assertion, runner_for
from dv.snapshots import LocalRepo, Snapshots, content, file_state, retained_inputs, tree


def review_input(path, repo, head, base, policy, command, runner, explicit, timeout):
    try:
        item = Item(path)
        if (
            item.root != repo.root
            or item.head != head
            or item.base != base
            or item.config != policy
            or item.meta.get("test_command") != command
            or item.meta["test_cwd"] != "."
            or runner_for(command, item.meta["test_runner"]) != runner
            or item.config["timeout_seconds"] != timeout
            or {p for p in item.meta["test_files"] if not hygiene.TEST_PATH.search(p)}
            != {p for p in explicit if not hygiene.TEST_PATH.search(p)}
        ):
            raise DVError("Review scope is stale or does not match this proof's focused recipe")
        checks = evidence.review_checks(item)
        binding = {"scope": item.scope, **evidence.review_hashes(item)}
        return checks, binding
    except (DVError, OSError) as error:
        return [row("V4 independent review", "fail", str(error))], {"error": str(error)}


def run(
    command_text: str,
    base_ref: str = "main",
    head_ref: str = "HEAD",
    *,
    explicit: list[str] = (),
    timeout: int = 1800,
    check_hygiene: bool = False,
    review: Path | None = None,
) -> dict:
    if os.name != "posix":
        raise DVError("Proof worktrees require Linux/macOS", 3, "unsupported_platform")
    nonblank(command_text, "test command")
    try:
        command = shlex.split(command_text)
    except ValueError as error:
        raise DVError(f"Invalid test command: {error}") from error
    if not command or any("\x00" in arg for arg in command) or not command[0]:
        raise DVError("Test command needs an executable")
    runner = runner_for(command)
    if runner == "unsupported":
        raise DVError(
            "Use direct unittest, pytest, or node --test with TAP; "
            "Jest/Vitest and wrapper discovery are unsupported",
            2,
            "unsupported_runner",
        )
    repo = LocalRepo(Path.cwd())
    head, tip = repo.resolve(head_ref), repo.resolve(base_ref)
    bases = repo.text("merge-base", "--all", head, tip).splitlines()
    if len(bases) != 1:
        raise DVError("Base and head need one unique merge base")
    base = bases[0]
    directory = repo.git_path("dv/proofs") / uuid.uuid4().hex
    for parent in (directory.parent, *directory.parent.parents):
        if parent.is_symlink():
            raise DVError(f"Refusing symlinked proof directory: {parent}")
    directory.mkdir(parents=True, mode=0o700)
    directory = directory.resolve()
    snapshots = Snapshots(repo, directory)
    output = result(
        "prove",
        {"head_sha": head, "base_sha": base, "base_tip_sha": tip},
        [],
        ok=False,
        state="running",
        argv=command,
        runner=runner,
        retained_tests={},
        red=None,
        green=None,
        exit_code=3,
        checker_sha256=checker_digest(),
        context_sha256=None,
        evidence_dir=str(directory),
        isolation="git-worktree",
        security_sandbox=False,
        timeout_seconds=timeout,
        optional_checks={"hygiene": "not_requested", "review": "not_requested"},
        scope_note="Committed snapshots only; not a security sandbox or delivery approval.",
    )
    write_json(directory / "proof.json", output)
    checks = output["checks"]
    for recovery in sorted(directory.parent.glob("*/recovery.json")):
        if recovery.parent != directory:
            checks.append(
                row(
                    "previous recovery",
                    "warn",
                    f"Inspect {recovery}; do not prune or delete ambiguous leftovers.",
                )
            )
    with interrupted_signals():
        try:
            head_tree, base_tree = tree(repo, head), tree(repo, base)
            sources = {}
            for name in ("pyproject.toml", ".dv.toml"):
                if name in head_tree:
                    if head_tree[name].mode not in ("100644", "100755"):
                        raise DVError(f"Committed policy must be a regular file: {name}")
                    sources[name] = content(repo, head_tree[name])
            policy = config.from_sources(sources)
            red_tree, retained = retained_inputs(repo, base_tree, head_tree, policy, explicit)
            output["retained_tests"] = {
                path: {"sha256": digest(content(repo, entry)), "mode": entry.mode}
                for path, entry in sorted(retained.items())
            }
            review_args = (review, repo, head, base, policy, command, runner, explicit, timeout)
            review_checks, binding = review_input(*review_args) if review else ([], None)
            bound = {
                "head": head,
                "base": base,
                "base_tip": tip,
                "argv": command,
                "retained_tests": output["retained_tests"],
                "policy": policy,
                "timeout": timeout,
                "hygiene": check_hygiene,
                "review": binding,
                "checker": output["checker_sha256"],
            }
            output["context_sha256"] = digest(canonical(bound))
            write_json(directory / "proof.json", output)
            for phase, ref, entries in (("red", base, red_tree), ("green", head, head_tree)):
                worktree, cache = snapshots.create(phase, ref, entries)
                env = {
                    key: value for key, value in os.environ.items() if not key.startswith("GIT_")
                }
                env.update(PYTHONPYCACHEPREFIX=str(cache), PYTHONDONTWRITEBYTECODE="1")
                before = file_state(worktree.root, entries)
                index = worktree.git_path("index").read_bytes()
                log = directory / f"{phase}.log"
                try:
                    executed = process.run(
                        instrument(command, env), worktree.root, log, timeout, env=env
                    )
                    executed["tests"], executed["report_complete"] = receipt(
                        log.read_bytes(), runner
                    )
                    output[phase] = executed
                finally:
                    after = file_state(worktree.root, entries)
                    changed = before != after or index != worktree.git_path("index").read_bytes()
                    changed |= worktree.resolve("HEAD") != ref
                    if changed:
                        write_json(
                            directory / f"{phase}-mutations.json",
                            {
                                "before": before,
                                "after": after,
                                "index_changed": index != worktree.git_path("index").read_bytes(),
                            },
                        )
                        checks.append(
                            row(
                                f"{phase} mutations",
                                "fail",
                                "Test changed tracked files, index, or HEAD",
                            )
                        )
                if executed["status"] != "normal":
                    break
            red = output["red"]
            valid, detail = red_assertion(red, (directory / "red.log").read_bytes(), runner)
            checks.append(row("red assertion", "pass" if valid else "fail", detail))
            green = output["green"]
            valid, detail = green_execution(
                green or {}, (directory / "green.log").read_bytes() if green else b"", runner
            )
            checks.append(row("green execution", "pass" if valid else "fail", detail))
            if check_hygiene:
                view = SimpleNamespace(
                    repo=repo,
                    root=repo.root,
                    head=head,
                    base=base,
                    config=policy,
                    scope={"head_sha": head, "base_sha": base},
                    meta={"test_files": list(explicit), "kind": "small", "exceptions": {}},
                )
                report = hygiene.inspect(view)
                write_json(directory / "hygiene.json", report)
                checks.extend(report["checks"])
                output["optional_checks"]["hygiene"] = "pass" if report["ok"] else "fail"
            if review:
                final_checks, final_binding = review_input(*review_args)
                if final_binding != binding:
                    final_checks.append(row("review scope", "fail", "Review changed during proof"))
                checks.extend(review_checks + final_checks)
                output["optional_checks"]["review"] = (
                    "fail"
                    if any(c["outcome"] == "fail" for c in review_checks + final_checks)
                    else "pass"
                )
            if (
                repo.resolve(head_ref) != head
                or repo.resolve(base_ref) != tip
                or checker_digest() != output["checker_sha256"]
            ):
                checks.append(
                    row("scope unchanged", "fail", "Refs or checker changed during proof")
                )
            output["exit_code"] = (
                3
                if any(p and p["status"] != "normal" for p in (red, green))
                else int(any(c["outcome"] == "fail" for c in checks))
            )
            output["state"] = "complete"
        except KeyboardInterrupt:
            output.update(state="interrupted", exit_code=130)
            checks.append(row("interrupted", "fail", "Interrupted; child process group reaped"))
        except (DVError, OSError, UnicodeError) as error:
            output.update(
                state="failed", exit_code=error.exit_code if isinstance(error, DVError) else 3
            )
            output["error"] = {
                "code": error.code if isinstance(error, DVError) else "io_error",
                "message": str(error),
            }
            checks.append(row("proof", "fail", str(error)))
        finally:
            # A second catchable signal must not interrupt owned-resource cleanup.
            previous = {
                sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM)
            }
            try:
                remaining = snapshots.cleanup()
                if remaining:
                    output.update(exit_code=3, state="cleanup_failed", remaining_paths=remaining)
                    checks.append(
                        row("cleanup", "fail", "Inspect owned paths: " + ", ".join(remaining))
                    )
                for key, requested in (("hygiene", check_hygiene), ("review", bool(review))):
                    if requested and output["optional_checks"][key] == "not_requested":
                        output["optional_checks"][key] = "fail"
                output["ok"] = output["exit_code"] == 0
                write_json(directory / "proof.json", output)
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
    return output
