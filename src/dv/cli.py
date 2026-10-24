"""Command-line interface for local delivery verification."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from enum import IntEnum
from pathlib import Path

from dv import __version__


class ExitCode(IntEnum):
    OK = 0
    CHECK_FAILED = 1
    INVALID_INPUT = 2
    OPERATIONAL_ERROR = 3
    INTERRUPTED = 130


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _commit_sha(value: str) -> str:
    if not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", value):
        raise argparse.ArgumentTypeError("must be a full 40- or 64-character commit ID")
    return value.lower()


def _item_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("item", type=Path, metavar="ITEM", help="path to item.json")
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dv",
        description="Verify a local code change before delivery.",
        epilog="Local checks only. No command pushes, publishes, or calls a model.",
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"dv {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create a local work item", allow_abbrev=False)
    init.add_argument("item", nargs="?", type=Path, default=Path(".dv/item.json"), metavar="ITEM")
    init.add_argument("--implementer", required=True, metavar="MODEL")
    init.add_argument("--base", default="main", metavar="REF")
    init.add_argument("--kind", choices=("small", "large"), default="small")
    init.add_argument("--json", action="store_true", help="emit machine-readable output")
    init.set_defaults(operation="init")

    redgreen = commands.add_parser(
        "redgreen", help="prove failure without the fix and success with it", allow_abbrev=False
    )
    _item_arguments(redgreen)
    redgreen.add_argument("--timeout", type=_positive_int, metavar="SECONDS")
    redgreen.set_defaults(operation="redgreen")

    hygiene = commands.add_parser(
        "hygiene", help="check diff and delivery-text hygiene", allow_abbrev=False
    )
    _item_arguments(hygiene)
    hygiene.set_defaults(operation="hygiene")

    review = commands.add_parser(
        "review", help="manage independent review evidence", allow_abbrev=False
    )
    review_commands = review.add_subparsers(dest="review_command", required=True)
    record = review_commands.add_parser(
        "record", help="record a review for an exact commit", allow_abbrev=False
    )
    _item_arguments(record)
    record.add_argument("--reviewer", required=True, metavar="MODEL")
    record.add_argument("--head", required=True, type=_commit_sha, metavar="SHA")
    record.add_argument("--verdict", required=True, choices=("pass", "fix-needed", "drop"))
    record.add_argument("--file", required=True, type=Path, metavar="REPORT")
    record.add_argument("--fresh-context", action="store_true")
    record.set_defaults(operation="review record")

    approve = commands.add_parser(
        "approve", help="record explicit human approval for an exact commit", allow_abbrev=False
    )
    _item_arguments(approve)
    approve.add_argument("--head", required=True, type=_commit_sha, metavar="SHA")
    approve.add_argument("--note", required=True, metavar="TEXT")
    approve.set_defaults(operation="approve")

    gate = commands.add_parser(
        "gate", help="recompute readiness or delivery checks", allow_abbrev=False
    )
    _item_arguments(gate)
    gate.add_argument("--require-approval", action="store_true")
    gate.set_defaults(operation="gate")

    hooks = commands.add_parser("hooks", help="manage optional agent hooks", allow_abbrev=False)
    hook_commands = hooks.add_subparsers(dest="hooks_command", required=True)
    install = hook_commands.add_parser(
        "install", help="install project-local hook entries", allow_abbrev=False
    )
    install.add_argument("--agent", required=True, choices=("claude-code",))
    install.add_argument("--dry-run", action="store_true")
    install.set_defaults(operation="hooks install")
    run = hook_commands.add_parser(
        "run", help="internal adapter for hook JSON on stdin", allow_abbrev=False
    )
    run.add_argument("event", choices=("stop", "pre-tool-use"))
    run.add_argument("--project", type=Path, default=Path.cwd(), help=argparse.SUPPRESS)
    run.set_defaults(operation="hooks run")
    return parser


def execute(args: argparse.Namespace) -> dict:
    from dv import evidence, gate, hooks, hygiene, redgreen
    from dv.item import Item, initialize

    if args.operation == "init":
        return initialize(args.item, args.implementer, args.base, args.kind)
    if args.operation == "hooks install":
        return hooks.install(Path.cwd(), args.dry_run)
    if args.operation == "hooks run":
        return hooks.run(args.event, args.project, sys.stdin.read())
    item = Item(args.item)
    if args.operation == "redgreen":
        return redgreen.run(item, args.timeout)
    if args.operation == "hygiene":
        return hygiene.run(item)
    if args.operation == "review record":
        return evidence.record_review(
            item, args.reviewer, args.head, args.verdict, args.file, args.fresh_context
        )
    if args.operation == "approve":
        return evidence.approve(item, args.head, args.note)
    return gate.run(item, args.require_approval)


def main(argv: Sequence[str] | None = None) -> int:
    from dv.common import DVError

    args = build_parser().parse_args(argv)
    try:
        output = execute(args)
        if args.operation == "hooks run":
            print(json.dumps(output, ensure_ascii=False))
            return ExitCode.OK
        if getattr(args, "json", False) or args.operation == "hooks install":
            print(json.dumps(output, ensure_ascii=False))
        else:
            print(f"{args.operation}: {'PASS' if output['ok'] else 'FAIL'}")
            print(f"HEAD {output.get('head_sha', '')} base {output.get('base_sha', '')}")
            for check in output.get("checks", []):
                print(f"[{check['outcome'].upper()}] {check['check']}: {check['detail']}")
                if check.get("fix"):
                    print(f"  Next: {check['fix']}")
        return output.get("exit_code", 0 if output["ok"] else 1)
    except KeyboardInterrupt:
        error = DVError(
            "Interrupted; inspect any remaining restoration.json before retrying",
            130,
            "interrupted",
        )
    except DVError as caught:
        error = caught
    except (OSError, UnicodeError) as caught:
        error = DVError(str(caught), 3, "io_error")
    if args.operation == "hooks run":
        from dv.hooks import deny

        output = (
            deny(str(error))
            if args.event == "pre-tool-use"
            else {"decision": "block", "reason": str(error)}
        )
        print(json.dumps(output))
        return ExitCode.OK
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "command": args.operation,
                    "ok": False,
                    "error": {"code": error.code, "message": str(error)},
                }
            )
        )
    else:
        print(f"dv: {error}", file=sys.stderr)
    return error.exit_code
