"""Review and approval attestations bound to the exact inspected inputs."""

from __future__ import annotations

import string
from pathlib import Path

from dv.common import (
    DVError,
    atomic_bytes,
    digest,
    nonblank,
    now,
    read_json,
    result,
    row,
    write_json,
)
from dv.item import Item
from dv.redgreen import phase_ok, plan
from dv.runners import red_assertion, runner_for


def model_of(name: str) -> str:
    return name.strip().split()[0].strip(string.punctuation).casefold() if name.strip() else ""


def review_independence(
    reviewer: str, implementer: str, kind: str, fresh_context: bool = False
) -> tuple[str, str]:
    if not model_of(reviewer) or not model_of(implementer):
        return "fail", "Both reviewer and implementer model identifiers are required"
    if model_of(reviewer) != model_of(implementer):
        return "pass", "Different model identifiers (self-reported)"
    if kind == "small" and fresh_context:
        return (
            "warn",
            "Same model, explicitly declared fresh context; isolation is not authenticated",
        )
    return (
        "fail",
        "Use a different reviewer model; small items may declare a fresh context explicitly",
    )


def report_valid(content: bytes) -> bool:
    try:
        text = content.decode("utf-8").strip()
    except UnicodeError:
        return False
    return len(text) >= 40 and len(text.split()) >= 6


def matching(item: Item, record: dict) -> bool:
    return (
        all(
            type(record.get(key)) is type(value) and record.get(key) == value
            for key, value in item.scope.items()
        )
        and isinstance(record.get("at"), str)
        and bool(record["at"].strip())
    )


def record_review(
    item: Item, reviewer: str, head: str, verdict: str, report: Path, fresh_context: bool = False
) -> dict:
    nonblank(reviewer, "reviewer")
    if not model_of(reviewer):
        raise DVError("Reviewer must identify a model, not punctuation")
    if verdict not in ("pass", "fix-needed", "drop") or type(fresh_context) is not bool:
        raise DVError("Invalid review verdict or fresh-context declaration")
    with item.repo.lock():
        item.ensure_current()
        if head != item.head:
            raise DVError("--head must equal the full current HEAD", 1, "stale_head")
        content = report.read_bytes()
        if not report_valid(content):
            raise DVError(
                (
                    "Review report must contain substantive UTF-8 findings (at least 4"
                    "0 characters and six words)"
                )
            )
        atomic_bytes(item.file("review.txt"), content)
        record = {
            **item.scope,
            "at": now(),
            "reviewer": reviewer,
            "fresh_context": fresh_context,
            "verdict": verdict,
            "report_sha256": digest(content),
            "attachments": item.attachments,
        }
        item.ensure_current()
        write_json(item.file("review.json"), record)
        outcome, detail = review_independence(
            reviewer, item.meta["implementer"], item.meta["kind"], fresh_context
        )
        return result(
            "review record",
            item.scope,
            [
                row("review recorded", "pass", f"verdict={verdict}"),
                row("review independence", "warn" if outcome == "fail" else outcome, detail),
            ],
        )


def review_checks(item: Item) -> list[dict]:
    fix = "Obtain a substantive independent review of the current inputs; run dv review record."
    try:
        record = read_json(item.file("review.json"))
        content = item.file("review.txt").read_bytes()
        if not matching(item, record) or record.get("attachments") != item.attachments:
            raise ValueError("Review has stale code, base, configuration, or attachments")
        if not report_valid(content) or record.get("report_sha256") != digest(content):
            raise ValueError("Review report is missing, too short, or changed")
        if record.get("verdict") != "pass":
            raise ValueError(f"Review verdict is {record.get('verdict')!r}, not pass")
        if (
            not isinstance(record.get("reviewer"), str)
            or type(record.get("fresh_context")) is not bool
        ):
            raise ValueError("Malformed reviewer/fresh-context record")
        outcome, detail = review_independence(
            record["reviewer"], item.meta["implementer"], item.meta["kind"], record["fresh_context"]
        )
        return [row("V4 independent review", outcome, detail, fix)]
    except (DVError, OSError, ValueError) as error:
        return [row("V4 independent review", "fail", str(error), fix)]


def redgreen_checks(item: Item) -> list[dict]:
    if item.file("restoration.json").exists():
        return [row("V2 red/green", "fail", "Restoration plan remains; inspect it before retrying")]
    try:
        if item.file("redgreen.json").exists():
            attempt = read_json(item.file("redgreen.json"))
            if attempt.get("state") != "complete":
                raise ValueError("Red/green attempt is incomplete; rerun the proof")
    except (DVError, OSError, ValueError) as error:
        return [row("V2 red/green", "fail", str(error))]
    reason = item.meta["exceptions"].get("no_test_reason")
    if reason:
        return [row("V2 red/green", "warn", f"WAIVED, not reproduced: {reason}")]
    try:
        record = read_json(item.file("redgreen.json"))
        reverted, retained = plan(item)
        if not matching(item, record):
            raise ValueError("Red/green proof is stale")
        if (
            record.get("argv") != item.meta["test_command"]
            or record.get("cwd") != item.meta["test_cwd"]
        ):
            raise ValueError("Focused test recipe changed")
        if record.get("reverted_paths") != reverted or record.get("retained_tests") != retained:
            raise ValueError("Reverted paths or retained tests do not match this change")
        timeout = record.get("timeout_seconds")
        if type(timeout) is not int or timeout <= 0:
            raise ValueError("Malformed proof timeout")
        runner = runner_for(item.meta["test_command"], item.meta["test_runner"])
        if record.get("runner") != runner:
            raise ValueError("Focused test runner changed or is missing")
        for name in ("red", "green"):
            phase = record.get(name)
            if not phase_ok(phase, red=name == "red"):
                raise ValueError(
                    f"{name} is not a normal {'failing' if name == 'red' else 'passing'} command"
                )
            if (
                phase.get("argv") != item.meta["test_command"]
                or phase.get("timeout_seconds") != timeout
            ):
                raise ValueError("Phase recipe does not match recorded proof")
            content = item.file(f"redgreen-{name}.log").read_bytes()
            if digest(content) != phase.get("log_sha256"):
                raise ValueError(f"{name} log changed")
            if name == "red":
                asserted, detail = red_assertion(phase, content, runner)
                if not asserted:
                    raise ValueError(detail)
        rows = record.get("checks")
        if (
            not isinstance(rows, list)
            or not rows
            or any(
                not isinstance(r, dict) or r.get("outcome") not in ("pass", "warn") for r in rows
            )
        ):
            raise ValueError("Proof recorded a cleanup or verification failure")
        return [
            row(
                "V2 red/green",
                "pass",
                "Assertion-only red, passing green; both log hashes verified",
            )
        ]
    except (DVError, OSError, ValueError, TypeError) as error:
        return [row("V2 red/green", "fail", str(error), "Rerun dv redgreen and inspect both logs.")]


def review_hashes(item: Item) -> dict:
    return {
        "review_record_sha256": digest(item.file("review.json").read_bytes()),
        "review_report_sha256": digest(item.file("review.txt").read_bytes()),
    }


def approval_checks(item: Item) -> list[dict]:
    try:
        record = read_json(item.file("approval.json"))
        valid = matching(item, record) and record.get("by") == "human"
        valid &= record.get("attachments") == item.attachments
        valid &= all(record.get(key) == value for key, value in review_hashes(item).items())
        nonblank(record.get("note"), "approval note")
        if not valid:
            raise ValueError(
                "Approval no longer matches code, description, runtime evidence, or review"
            )
        return [row("V5 human approval", "pass", record["note"])]
    except (DVError, OSError, ValueError) as error:
        return [
            row(
                "V5 human approval",
                "fail",
                str(error),
                (
                    "Have the person inspect the exact change and record their decisio"
                    "n with dv approve."
                ),
            )
        ]


def approve(item: Item, head: str, note: str) -> dict:
    from dv.gate import evaluate

    nonblank(note, "approval note")
    with item.repo.lock():
        item.ensure_current()
        if head != item.head:
            raise DVError("--head must equal the full current HEAD", 1, "stale_head")
        output = evaluate(item)
        if not output["ok"]:
            output["command"] = "approve"
            return output
        item.ensure_current()
        write_json(
            item.file("approval.json"),
            {
                **item.scope,
                "at": now(),
                "by": "human",
                "note": note,
                "attachments": item.attachments,
                **review_hashes(item),
            },
        )
        item.set_status("approved")
        return result("approve", item.scope, [*output["checks"], *approval_checks(item)])
