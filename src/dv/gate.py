"""Recomputed local readiness and delivery gates."""

from __future__ import annotations

from dv import evidence, hygiene, process
from dv.common import DVError, result, row, write_json
from dv.item import Item


def evaluate(item: Item, require_approval: bool = False) -> dict:
    checks = []
    command_results = []
    operational = False
    problem = item.repo.clean_problem()
    checks.append(
        row("committed state", "fail" if problem else "pass", problem or "Clean committed worktree")
    )
    if problem:
        output = result("gate", item.scope, checks)
        write_json(item.file("gate.json"), output)
        return output
    item.ensure_current()
    clean = hygiene.inspect(item)
    checks.extend(clean["checks"])
    checks.append(row("V1 hygiene", "pass" if clean["ok"] else "fail", str(clean["stats"])))
    write_json(item.file("hygiene.json"), clean)
    waiver = item.meta["exceptions"].get("no_test_reason")
    for name, label in (("static_commands", "static"), ("test_commands", "full suite")):
        commands = item.config[name]
        if not commands:
            checks.append(
                row(
                    f"V1 {label}",
                    "warn" if name == "test_commands" and waiver else "fail",
                    f"WAIVED, not run: {waiver}"
                    if name == "test_commands" and waiver
                    else "No commands configured",
                    f"Configure the repository's real {name}.",
                )
            )
        for index, command in enumerate(commands):
            log = item.file(f"checks/{name}-{index}.log")
            phase = process.run(command, item.root, log, item.config["timeout_seconds"])
            command_results.append({**phase, "log": str(log.relative_to(item.root)), "kind": label})
            passed = phase["status"] == "normal" and phase["exit_code"] == 0
            operational |= phase["status"] != "normal"
            checks.append(
                row(
                    f"V1 {label}",
                    "pass" if passed else "fail",
                    f"{command!r}: {phase['status']} exit={phase['exit_code']}; "
                    f"log={log.relative_to(item.root)}",
                    "Inspect the log, correct the failure, and rerun the gate.",
                )
            )
    checks.extend(evidence.redgreen_checks(item))
    if item.meta.get("description_file"):
        path = item.root / item.meta["description_file"]
        checks.append(
            row(
                "description",
                "pass" if path.is_file() and path.stat().st_size else "fail",
                "Exact delivery text exists"
                if path.is_file() and path.stat().st_size
                else "Description file missing or empty",
            )
        )
    runtime = item.meta["runtime_files"]
    if runtime:
        nonempty = all((item.root / path).stat().st_size for path in runtime)
        checks.append(
            row(
                "V3 runtime attachments",
                "pass" if nonempty else "fail",
                (
                    "Files exist and are review-bound; runtime behavior is a reviewer "
                    "attestation, not reproduced by dv"
                )
                if nonempty
                else "Runtime evidence is empty",
            )
        )
    else:
        checks.append(row("V3 runtime", "warn", "Not claimed; no runtime attachments supplied"))
    checks.extend(evidence.review_checks(item))
    merge = item.repo.run("merge-tree", "--write-tree", item.base_tip, item.head, check=False)
    if merge.returncode not in (0, 1):
        raise DVError(
            "Local mergeability check failed; Git 2.38+ is required: "
            + merge.stderr.decode("utf-8", "replace"),
            3,
            "git_error",
        )
    checks.append(
        row(
            "mergeability",
            "pass" if merge.returncode == 0 else "fail",
            "Merges with the local base ref"
            if merge.returncode == 0
            else "Conflicts with the local base ref",
            "Resolve conflicts against the local base and rerun evidence.",
        )
    )
    behind = int(item.repo.text("rev-list", "--count", f"{item.head}..{item.base_tip}"))
    if behind > 200:
        checks.append(row("base freshness", "warn", f"{behind} commits behind the local base"))
    if require_approval:
        checks.extend(evidence.approval_checks(item))
    try:
        item.ensure_current()
    except DVError as error:
        checks.append(
            row(
                "scope unchanged",
                "fail",
                str(error),
                "Inspect check-command mutations, then rerun.",
            )
        )
    output = result(
        "gate",
        item.scope,
        checks,
        commands=command_results,
        require_approval=require_approval,
        exit_code=3 if operational else (1 if any(r["outcome"] == "fail" for r in checks) else 0),
    )
    if output["ok"] and item.meta["status"] == "preparing":
        item.set_status("ready")
    write_json(item.file("gate.json"), output)
    return output


def run(item: Item, require_approval: bool = False) -> dict:
    with item.repo.lock():
        item.ensure_current(clean=False)
        return evaluate(item, require_approval)
