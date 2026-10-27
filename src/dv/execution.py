"""Execution receipts for proof-only green validation; legacy gates are unchanged."""

from __future__ import annotations

import json
import re

from dv.runners import ANSI
from dv.vitest import parse_report


def receipt(content: bytes, runner: str) -> tuple[dict, bool]:
    counts = {"executed": 0, "passed": 0, "failed": 0, "skipped": 0}
    text = ANSI.sub("", content.decode("utf-8", errors="replace"))
    if runner == "vitest":
        counts, complete, _, _ = parse_report(text)
        return counts, complete
    if runner == "node-test":
        return tap_receipt(text)
    records = re.findall(r"^DV-RUNNER-PHASES (.+)$", text, re.M)
    if len(records) != 1:
        return counts, False
    try:
        record = json.loads(records[0])
        supplied = record["tests"]
        if not isinstance(supplied, dict):
            return counts, False
        if (
            record["runner"] != runner
            or set(supplied) != set(counts)
            or any(type(n) is not int or n < 0 for n in supplied.values())
            or supplied["passed"] > supplied["executed"]
        ):
            return counts, False
        counts = supplied
        complete = record.get("complete") is True
    except (ValueError, KeyError, TypeError):
        return counts, False
    if runner == "unittest":
        summaries = re.findall(r"^Ran (\d+) tests? in [^\n]+$", text, re.M)
        endings = re.findall(r"^(?:OK(?: \([^\n]*\))?|FAILED \([^\n]*\))$", text, re.M)
        complete &= (
            len(summaries) == 1
            and len(endings) == 1
            and int(summaries[0]) == counts["executed"] + counts["skipped"]
        )
    elif runner == "pytest":
        summaries = re.findall(
            r"^(?:=+ )?((?:\d+ (?:passed|failed|skipped|deselected|xfailed|xpassed|errors?"
            r"|warnings?)(?:, )?)+) in [^\n]+$",
            text,
            re.M,
        )
        complete &= len(summaries) == 1
        if len(summaries) == 1:
            reported = dict(
                (label, int(n)) for n, label in re.findall(r"(\d+) (\w+)", summaries[0])
            )
            complete &= reported.get("passed", 0) == counts["passed"]
            complete &= not reported.get("xpassed", 0)
    else:
        complete = False
    return counts, bool(complete)


def tap_receipt(text: str) -> tuple[dict, bool]:
    counts = {"executed": 0, "passed": 0, "failed": 0, "skipped": 0}
    fields = {}
    for name in ("tests", "pass", "fail", "cancelled", "skipped", "todo"):
        matches = re.findall(r"^# " + name + r" (\d+)$", text, re.M)
        if len(matches) != 1:
            return counts, False
        fields[name] = int(matches[0])
    plans = re.findall(r"^1\.\.(\d+)$", text, re.M)
    top = re.findall(r"^(?:not )?ok (\d+)(?:[ \n]|$)", text, re.M)
    complete = (
        text.startswith("TAP version 13\n")
        and len(plans) == 1
        and len(top) == int(plans[0])
        and [int(n) for n in top] == list(range(1, len(top) + 1))
        and fields["tests"] == sum(fields[key] for key in fields if key != "tests")
        and not re.search(r"^\s*Bail out!|^# (?:Warning|Error):", text, re.M)
        and len(re.findall(r"^# duration_ms [\d.]+$", text, re.M)) == 1
        and fields["cancelled"] == 0
        and (
            fields["fail"] > 0
            or not any(
                "# TODO" not in line and "# SKIP" not in line
                for line in re.findall(r"^\s*not ok [^\n]+$", text, re.M)
            )
        )
    )
    # Node 20 reports an empty file as a passing test named by its absolute path.
    # TAP cannot distinguish that from a user test with the same name; neither
    # supplies unambiguous test-body evidence. Suite containers are already excluded.
    file_passes = 0
    for match in re.finditer(
        r"^ok \d+ - (/[^\n]*)\n(?:  ---\n(.*?)^  \.\.\.$)?", text, re.M | re.S
    ):
        if re.search(r" # (?:SKIP|TODO)\b", match[1]):
            continue
        if not re.search(r"^  type: 'suite'$", match[2] or "", re.M):
            file_passes += 1
    passed = fields["pass"] - file_passes
    complete &= passed >= 0
    counts.update(
        executed=max(0, passed) + fields["fail"] + fields["cancelled"],
        passed=max(0, passed),
        failed=fields["fail"] + fields["cancelled"],
        skipped=fields["skipped"] + fields["todo"],
    )
    return counts, bool(complete)


def green_execution(phase: dict, content: bytes, runner: str) -> tuple[bool, str]:
    counts, complete = receipt(content, runner)
    valid = (
        phase.get("status") == "normal"
        and phase.get("exit_code") == 0
        and complete
        and counts["executed"] > 0
        and counts["passed"] > 0
        and counts["failed"] == 0
    )
    if runner == "vitest" and not valid:
        _, _, _, detail = parse_report(content.decode("utf-8", errors="replace"))
        return False, detail + "; requires exit 0 and at least one passed test body, with no errors"
    return valid, (
        f"{runner}: complete={complete}, tests={counts}; "
        "requires a normal exit 0 and at least one passed test body, with no errors"
    )
