"""Conservative assertion-only red contracts for supported test reporters."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

RUNNERS = {"auto", "unittest", "pytest", "node-test"}
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
    return "unsupported"


def instrument(command: list[str], env: dict[str, str]) -> list[str]:
    runner = runner_for(command)
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


def body_assertions(text: str, runner: str, failures: int) -> bool:
    records = re.findall(r"^DV-RUNNER-PHASES (.+)$", text, re.M)
    if len(records) != 1:
        return False
    try:
        record = json.loads(records[0])
    except ValueError:
        return False
    return (
        isinstance(record, dict)
        and record.get("runner") == runner
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


def pytest_assertions(text: str) -> bool:
    if re.search(r"^=+ ERRORS =+|^ERROR |\b[1-9]\d* errors?\b", text, re.M):
        return False
    summaries = re.findall(r"^(?:=+ )?(\d+) failed(?:, [^\n]+)? in [^\n]+$", text, re.M)
    sections = re.split(r"^=+ FAILURES =+\n", text, flags=re.M)
    if len(summaries) != 1 or len(sections) != 2:
        return False
    failures = int(summaries[0])
    body = re.split(r"^=+ short test summary info =+", sections[1], flags=re.M)[0]
    blocks = re.split(r"^_+ [^\n]+ _+\n", body, flags=re.M)[1:]
    if len(blocks) != failures or not failures or not body_assertions(text, "pytest", failures):
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
    validators = {
        "unittest": unittest_assertions,
        "pytest": pytest_assertions,
        "node-test": node_assertions,
    }
    if runner not in validators:
        return False, "Unsupported red runner; use unittest, pytest, or node --test with TAP output"
    text = ANSI.sub("", content.decode("utf-8", errors="replace"))
    if not validators[runner](text):
        return False, (
            f"{runner}: red has errors or lacks assertion-only failure output; "
            "import, runtime, collection, setup/teardown/cleanup errors "
            "and reports without test-body phase evidence are not proof"
        )
    return True, f"{runner}: red failed at assertions with no runner errors"
