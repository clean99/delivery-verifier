"""Conservative assertion-only red contracts for supported test reporters."""

from __future__ import annotations

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


def unittest_assertions(text: str) -> bool:
    summaries = re.findall(r"^FAILED \(([^\n]+)\)$", text, re.M)
    if len(summaries) != 1 or re.search(r"^ERROR: |unexpected successes=", text, re.M):
        return False
    counts = dict(re.findall(r"(\w+)=(\d+)", summaries[0]))
    failures = int(counts.get("failures", 0))
    if not failures or int(counts.get("errors", 0)):
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
    if len(blocks) != failures or not failures:
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
    blocks = re.findall(r"^([ \t]*)not ok [^\n]+\n\1  ---\n(.*?)^\1  \.\.\.$", text, re.M | re.S)
    if len(blocks) != len(re.findall(r"^\s*not ok ", text, re.M)):
        return False
    assertions = 0
    for indent, block in blocks:
        fields = dict(re.findall(r"^" + indent + r"  (\w+): '([^'\n]+)'$", block, re.M))
        if fields.get("failureType") == "subtestsFailed":
            continue
        if fields.get("failureType") != "testCodeFailure" or fields.get("code") != "ERR_ASSERTION":
            return False
        assertions += 1
    return assertions == int(failures[0])


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
            "import, runtime, collection, setup/teardown errors "
            "and incomplete reports are not proof"
        )
    return True, f"{runner}: red failed at assertions with no runner errors"
