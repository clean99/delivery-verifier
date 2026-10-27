"""Conservative assertion-only red contracts for supported test reporters."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from dv import process
from dv.common import DVError, digest
from dv.vitest import PREFIX, parse_report

RUNNERS = {"auto", "unittest", "pytest", "node-test", "vitest"}
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def runner_for(command: list[str], declared: str = "auto") -> str:
    if declared != "auto":
        return declared
    executable = Path(command[0]).name
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable):
        if "-m" in command:
            index = command.index("-m")
            if index + 1 < len(command) and command[index + 1] in ("unittest", "pytest"):
                return command[index + 1]
    if executable in ("pytest", "py.test"):
        return "pytest"
    if executable in ("node", "nodejs") and "--test" in command:
        return "node-test"
    if vitest_index(command) is not None:
        return "vitest"
    return "unsupported"


def legacy_runner(command: list[str], declared: str = "auto") -> str:
    runner = runner_for(command, declared)
    if runner == "vitest":
        raise DVError("Vitest is supported only by dv prove; use dv prove --test 'vitest run ...'",
                      2, "unsupported_runner")
    return runner


def vitest_index(command: list[str]) -> int | None:
    executable = Path(command[0]).name
    if executable == "vitest":
        return 0
    if executable in ("npx", "pnpm"):
        index = 2 if command[1:2] == ["exec"] and executable == "pnpm" else 1
        if command[index:index + 1] == ["vitest"]:
            return index
    return None


VITEST_PATH_OPTIONS = {"-c", "--config", "-r", "--root", "--dir"}
VITEST_VALUE_OPTIONS = VITEST_PATH_OPTIONS | {
    "-t", "--testNamePattern", "--project", "--maxWorkers", "--minWorkers",
}
VITEST_FLAGS = {"--run", "--globals"}


def validate_vitest_args(args: list[str]) -> None:
    first_positional = True
    index = 0
    while index < len(args):
        arg = args[index]
        option, separator, value = arg.partition("=")
        if arg.startswith("-"):
            if option in VITEST_VALUE_OPTIONS:
                if not separator:
                    index += 1
                    value = args[index] if index < len(args) else ""
                if not value or value.startswith("-"):
                    raise DVError(f"Vitest option {option} requires a value")
            elif arg not in VITEST_FLAGS:
                raise DVError(f"Unsupported Vitest option {option}; use only the documented "
                              "proof argument allowlist", 2, "unsupported_runner_option")
        else:
            if first_positional and arg in ("watch", "dev", "bench", "list", "init", "related"):
                raise DVError(f"Unsupported Vitest command {arg}; use vitest run [filters]")
            first_positional = False
        index += 1


def command_path_indices(command: list[str]) -> set[int]:
    paths = {0}
    index = vitest_index(command)
    if index is not None:
        index += 1
        path_options = VITEST_PATH_OPTIONS
        value_options = VITEST_VALUE_OPTIONS
    elif runner_for(command) == "node-test":
        index = 1
        path_options = {"-r", "--require", "--import"}
        value_options = path_options | {
            "--test-name-pattern", "--test-skip-pattern", "--test-reporter",
            "--test-reporter-destination", "--test-concurrency", "--test-timeout",
            "--test-shard", "--conditions", "-C", "--input-type", "--inspect-port",
        }
    else:
        return paths
    first_positional = True
    while index < len(command):
        argument = command[index]
        option = argument.split("=", 1)[0]
        if argument.startswith("-"):
            if option in path_options:
                paths.add(index if "=" in argument else index + 1)
            if option in value_options and "=" not in argument:
                index += 1
        else:
            if not (first_positional and argument == "run" and vitest_index(command) is not None):
                paths.add(index)
            first_positional = False
        index += 1
    return paths


def instrument(command: list[str], env: dict[str, str], declared: str = "auto", *,
               reporter: Path | None = None, version: str | None = None) -> list[str]:
    runner = "vitest" if declared == "vitest" else runner_for(command)
    if runner == "vitest":
        index = vitest_index(command)
        if index is None:
            raise DVError(
                "Vitest requires a direct vitest, npx vitest or pnpm [exec] vitest command"
            )
        validate_vitest_args(command[index + 1:])
        from dv.vitest4 import validate_options

        validate_options(command, version)
        env["CI"] = "true"
        env.pop("UPDATE_SNAPSHOT", None)
        reporter = reporter or Path(__file__).with_name("_vitest_reporter.mjs")
        prefix = command
        if Path(command[0]).name == "npx":
            prefix = [command[0], "--no-install", *command[1:]]
        run_flag = [] if "--run" in command[index + 1:] else ["--run"]
        update = "--update=none" if version == "4.1.12" else "--update=false"
        return [*prefix, *run_flag, "--watch=false", "--coverage.enabled=false", update,
                "--reporter=default", f"--reporter={reporter}"]
    if runner == "unittest":
        index = command.index("-m")
        return [
            *command[:index],
            str(Path(__file__).with_name("_runner_probe.py")),
            *command[index + 2 :],
        ]
    if runner == "pytest":
        package_root = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (package_root, env.get("PYTHONPATH"))))
        index = 1 if Path(command[0]).name in ("pytest", "py.test") else command.index("-m") + 2
        return [*command[:index], "-p", "dv._runner_probe", *command[index:]]
    return command


def run_test(
    command: list[str], cwd: Path, log: Path, timeout: int, env: dict[str, str],
    declared: str = "auto", *, phase_owned: bool = False,
) -> dict:
    if runner_for(command, declared) == "pytest":
        return run_pytest(command, cwd, log, timeout, env, declared)
    argv = instrument(command, env, declared)
    if runner_for(command, declared) != "vitest":
        return process.run(argv, cwd, log, timeout, env=env)
    log.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".vitest-report-", dir=log.parent) as directory, \
            tempfile.TemporaryDirectory(prefix="dv-vitest-probe-") as probe_directory:
        report = Path(directory) / "report.json"
        probe = Path(probe_directory).resolve() / "node_modules" / "dv-probe"
        if probe.is_relative_to(cwd.resolve()):
            raise DVError("Vitest probe temporary directory must be outside the execution checkout")
        probe.mkdir(parents=True)
        for source in Path(__file__).parent.glob("_vitest*.mjs"):
            shutil.copyfile(source, probe / source.name)
        from dv.vitest4 import prepare, select

        selection = select(command, cwd)
        reporter = probe / "_vitest_reporter.mjs"
        argv = instrument(command, env, declared, reporter=reporter, version=selection["version"])
        if selection["version"] == "4.1.12":
            prepared = prepare(selection, cwd, Path(probe_directory).resolve(),
                               phase_owned=phase_owned)
            direct = instrument(["vitest", *command[vitest_index(command) + 1:]], env,
                                reporter=reporter, version=selection["version"])
            argv = ["node", prepared["cli"], *direct[1:]]
            env.update(DV_VITEST4_PACKAGE=prepared["package"],
                       DV_VITEST4_SOURCES=prepared["manifest"])
        else:
            env.pop("DV_VITEST4_PACKAGE", None)
            env.pop("DV_VITEST4_SOURCES", None)
        nonce = uuid.uuid4().hex
        temporary = str(Path(probe_directory).resolve())
        env.update(DV_VITEST_REPORT=str(report), DV_VITEST_NONCE=nonce,
                   DV_VITEST_ROOT=str(cwd.resolve()), DV_VITEST_TEMP=temporary,
                   TMPDIR=temporary, TEMP=temporary, TMP=temporary)
        phase = process.run(argv, cwd, log, timeout, env=env)
        phase["vitest_reporter"] = str(reporter)
        try:
            if report.is_symlink() or not report.is_file():
                raise ValueError("missing or nonregular report")
            record = json.loads(report.read_bytes())
            if not isinstance(record, dict) or record.get("nonce") != nonce:
                raise ValueError("report nonce mismatch")
            if record.get("exitCode") != phase["exit_code"]:
                raise ValueError("report exit status mismatch")
        except (OSError, ValueError):
            record = None
        with log.open("ab") as stream:
            stream.write(("\n" + PREFIX + json.dumps(record) + "\n").encode())
        phase["log_sha256"] = digest(log.read_bytes())
        return phase


def run_pytest(command: list[str], cwd: Path, log: Path, timeout: int,
               env: dict[str, str], declared: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="dv-pytest-probe-") as directory:
        report = Path(directory).resolve() / "report.json"
        if report.is_relative_to(cwd.resolve()):
            raise DVError("Pytest probe temporary directory must be outside the execution checkout")
        nonce = uuid.uuid4().hex
        reader, writer = os.pipe()
        try:
            env = {**env, "DV_PYTEST_REPORT": str(report), "DV_PYTEST_NONCE": nonce,
                   "DV_PYTEST_REPORT_FD": str(writer)}
            phase = process.run(instrument(command, env, declared), cwd, log, timeout, env=env,
                                pass_fds=(writer,))
            # A fixed-size receipt cannot fill the pipe. Never wait for a missing
            # receipt or an inherited descriptor held by an escaped descendant.
            os.set_blocking(reader, False)
            try:
                sealed_digest = os.read(reader, 65)
            except BlockingIOError:
                sealed_digest = b""
        finally:
            os.close(reader)
            os.close(writer)
        try:
            if report.is_symlink() or not report.is_file():
                raise ValueError("missing or nonregular report")
            encoded = report.read_bytes()
            if sealed_digest != digest(encoded).encode():
                raise ValueError("report receipt mismatch")
            record = json.loads(encoded)
            if not isinstance(record, dict) or record.get("nonce") != nonce:
                raise ValueError("report nonce mismatch")
            if record.get("exit_code") != phase["exit_code"]:
                raise ValueError("report exit status mismatch")
        except (OSError, ValueError):
            record = None
        encoded = json.dumps(record).encode()
        # Bind only a report matching the probe's receipt, never test stdout.
        phase["runner_report_sha256"] = digest(encoded) if record is not None else None
        with log.open("ab") as stream:
            stream.write(b"\nDV-RUNNER-PHASES " + encoded + b"\n")
        phase["log_sha256"] = digest(log.read_bytes())
        return phase


def phase_record(text: str, runner: str, phase: dict | None = None) -> dict | None:
    records = re.findall(r"^DV-RUNNER-PHASES (.+)$", text, re.M)
    if len(records) != 1:
        return None
    if runner == "pytest" and (
        phase is None or phase.get("runner_report_sha256") != digest(records[0].encode())
    ):
        return None
    try:
        record = json.loads(records[0])
    except ValueError:
        return None
    if not isinstance(record, dict) or record.get("runner") != runner:
        return None
    if runner == "pytest" and record.get("exit_code") != phase.get("exit_code"):
        return None
    return record


def body_assertions(text: str, runner: str, failures: int, phase: dict | None = None) -> bool:
    record = phase_record(text, runner, phase)
    return (
        record is not None
        and (runner != "pytest" or record.get("complete") is True)
        and isinstance(record.get("body_assertions"), list)
        and len(record["body_assertions"]) == failures
        and all(value is True for value in record["body_assertions"])
    )


def unittest_assertions(text: str) -> bool:
    summaries = re.findall(r"^FAILED \(([^\n]+)\)$", text, re.M)
    if len(summaries) != 1 or re.search(r"^ERROR: |unexpected successes=", text, re.M):
        return False
    counts = dict(re.findall(r"(\w+)=(\d+)", summaries[0]))
    failures = int(counts.get("failures", 0))
    if (
        not failures
        or int(counts.get("errors", 0))
        or not body_assertions(text, "unittest", failures)
    ):
        return False
    blocks = re.findall(r"^FAIL: ([^\n]+)\n-+\n(.*?)(?=\n[-=]{5,}\n)", text, re.M | re.S)
    return len(blocks) == failures and all(
        not re.match(r"(?:setUp|tearDown)", name)
        and not re.search(r", in (?:setUp|tearDown|asyncSetUp|asyncTearDown)\n", body)
        and re.findall(r"^(\w+(?:Error|Exception))(?::|$)", body, re.M) == ["AssertionError"]
        for name, body in blocks
    )


def pytest_assertions(text: str, phase: dict | None = None) -> bool:
    if re.search(r"^=+ ERRORS =+|^ERROR |\b[1-9]\d* errors?\b", text, re.M):
        return False
    summaries = re.findall(r"^(?:=+ )?(\d+) failed(?:, [^\n]+)? in [^\n]+$", text, re.M)
    sections = re.split(r"^=+ FAILURES =+\n", text, flags=re.M)
    if len(summaries) != 1 or len(sections) != 2:
        return False
    failures = int(summaries[0])
    body = re.split(r"^=+ short test summary info =+", sections[1], flags=re.M)[0]
    blocks = re.split(r"^_+ [^\n]+ _+\n", body, flags=re.M)[1:]
    if (len(blocks) != failures or not failures
            or not body_assertions(text, "pytest", failures, phase)):
        return False
    for block in blocks:
        terminal = re.findall(r"^\S.*:\d+: (\w+)\s*$", block, re.M)
        if terminal != ["AssertionError"]:
            return False
    return True


def node_assertions(text: str) -> bool:
    if not text.startswith("TAP version 13\n") or re.search(
        r"^\s*Bail out!|^# (?:Warning|Error):", text, re.M
    ):
        return False
    failures = re.findall(r"^# fail (\d+)$", text, re.M)
    cancelled = re.findall(r"^# cancelled (\d+)$", text, re.M)
    if len(failures) != 1 or cancelled != ["0"] or int(failures[0]) == 0:
        return False
    blocks = list(
        re.finditer(r"^([ \t]*)not ok [^\n]+\n\1  ---\n(.*?)^\1  \.\.\.$", text, re.M | re.S)
    )
    if len(blocks) != len(re.findall(r"^\s*not ok ", text, re.M)):
        return False
    assertions = counted = 0
    for match in blocks:
        indent, block = match.groups()
        fields = dict(re.findall(r"^" + indent + r"  (\w+): '([^'\n]+)'$", block, re.M))
        counted += fields.get("type") != "suite"
        if fields.get("failureType") == "subtestsFailed":
            starts = list(re.finditer(r"^" + indent + r"# Subtest: ", text[: match.start()], re.M))
            if (
                fields.get("code") != "ERR_TEST_FAILURE"
                or not starts
                or not any(
                    starts[-1].start() < child.start() < match.start()
                    and len(child[1]) > len(indent)
                    for child in blocks
                )
            ):
                return False
        elif (
            fields.get("failureType") == "testCodeFailure" and fields.get("code") == "ERR_ASSERTION"
        ):
            assertions += 1
        else:
            return False
    return assertions > 0 and counted == int(failures[0])


def red_assertion(phase: dict, content: bytes, runner: str) -> tuple[bool, str]:
    if phase.get("status") != "normal" or type(phase.get("exit_code")) is not int:
        return False, "Red did not exit normally; no assertion proof"
    if phase["exit_code"] != 1:
        return False, "Red must exit 1 with assertion failures, not pass or abort"
    if runner == "vitest":
        _, complete, asserted, detail = parse_report(content.decode("utf-8", errors="replace"))
        return complete and asserted, detail
    validators = {
        "unittest": unittest_assertions,
        "pytest": lambda text: pytest_assertions(text, phase),
        "node-test": node_assertions,
    }
    if runner not in validators:
        return False, "Unsupported red runner; use unittest, pytest, node --test TAP, or Vitest"
    text = ANSI.sub("", content.decode("utf-8", errors="replace"))
    if not validators[runner](text):
        return False, (
            f"{runner}: red has errors or lacks assertion-only failure output; "
            "import, runtime, collection, setup/teardown/cleanup errors "
            "and reports without test-body phase evidence are not proof"
        )
    return True, f"{runner}: red failed at assertions with no runner errors"
