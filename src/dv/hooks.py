"""Opt-in project hook adapters; convenience barriers, not shell isolation."""

from __future__ import annotations

import copy
import re
import shlex
import shutil
import signal
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dv import config, gate
from dv.common import DVError, decode_json, digest, now, read_json, write_json
from dv.git import Repo
from dv.item import Item, safe_path


def deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def validate_payload(payload: object, project: Path) -> tuple[dict, Repo]:
    if not isinstance(payload, dict) or not isinstance(payload.get("cwd"), str):
        raise DVError("Hook payload needs an object with cwd")
    cwd = Path(payload["cwd"])
    if not cwd.is_absolute() or not cwd.is_dir():
        raise DVError("Hook cwd must be an existing absolute directory")
    repo = Repo(project)
    if repo.root != project.resolve() or Repo(cwd).root != repo.root:
        raise DVError("Hook cwd does not belong to the installed project")
    return payload, repo


def guarded(command: str) -> bool:
    normalized = command.replace("\\\n", "")
    try:
        lexer = shlex.shlex(normalized, posix=True, punctuation_chars=";&|()<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return bool(re.search(r"\b(git|gh)\b", normalized))
    for index, token in enumerate(tokens):
        executable = Path(token).name
        rest = tokens[index + 1 :]
        if executable == "git" and any(part == "push" or part.startswith("push;") for part in rest):
            return True
        if executable == "gh":
            if "api" in rest:
                methods = []
                for position, part in enumerate(rest):
                    if part in ("-X", "--method"):
                        methods.append(rest[position + 1] if position + 1 < len(rest) else "")
                    elif part.startswith("-X"):
                        methods.append(part[2:].lstrip("="))
                    elif part.startswith("--method="):
                        methods.append(part.split("=", 1)[1])
                fields = any(
                    part.startswith(("-f", "-F", "--field", "--raw-field", "--input"))
                    for part in rest
                )
                if any(method.upper() != "GET" for method in methods) or fields:
                    return True
            if any(
                part
                in {
                    "create",
                    "comment",
                    "review",
                    "merge",
                    "edit",
                    "close",
                    "reopen",
                    "ready",
                    "fork",
                    "delete",
                }
                for part in rest
            ):
                return True
    return False


def push_source(command: str, payload_cwd: Path, project: Path) -> str:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError as error:
        raise DVError("Cannot parse push command; deliver manually") from error
    if not tokens or Path(tokens.pop(0)).name != "git":
        raise DVError("Only a standalone direct git push can be gated; deliver manually")
    cwd = payload_cwd
    while tokens and tokens[0] == "-C":
        if len(tokens) < 2:
            raise DVError("Incomplete git -C")
        cwd = (cwd / tokens[1]).resolve()
        tokens = tokens[2:]
    if not cwd.is_dir() or Repo(cwd).root != project:
        raise DVError("Push targets a different repository; run its own delivery gate manually")
    if not tokens or tokens.pop(0) != "push":
        raise DVError("Unsupported git options; deliver manually")
    if tokens and tokens[0] == "--":
        tokens.pop(0)
    if len(tokens) != 2:
        raise DVError(
            "Use one explicit remote and one refspec; implicit/multiple pushes are denied"
        )
    remote, refspec = tokens
    if any(char in command for char in ("\n", "`", "$")) or any(
        char in value for value in tokens for char in (";", "&", "|", "(", ")", "<", ">")
    ):
        raise DVError("Compound or expanded push commands are denied; deliver manually")
    if remote.startswith("-") or refspec.startswith(("-", "+")):
        raise DVError("Push flags and force pushes are denied; deliver manually")
    if refspec.count(":") > 1:
        raise DVError("Ambiguous push refspec")
    source, *destination = refspec.split(":")
    if not source or any(char in refspec for char in "*?[]~^@{}"):
        raise DVError("Deletion, wildcard, or expression refspecs are denied")
    if destination and (not destination[0] or destination[0].startswith("-")):
        raise DVError("Invalid push destination")
    repo = Repo(project)
    policies = ["push.followTags"]
    if remote in repo.text("remote").splitlines():
        policies.append(f"remote.{remote}.mirror")
    for policy in policies:
        configured = repo.run("config", "--bool", "--get", policy, check=False)
        if configured.returncode not in (0, 1) or configured.stdout.strip() == b"true":
            raise DVError(f"{policy} could push additional refs; deliver manually")
    current_branch = (
        repo.text("symbolic-ref", "--quiet", "--short", "HEAD")
        if repo.run("symbolic-ref", "--quiet", "HEAD", check=False).returncode == 0
        else ""
    )
    allowed = {"HEAD", repo.resolve("HEAD")}
    if current_branch:
        allowed.update((current_branch, f"refs/heads/{current_branch}"))
    if source not in allowed or repo.resolve(source) != repo.resolve("HEAD"):
        raise DVError("Only the approved current HEAD or current branch may be pushed")
    if (
        destination
        and repo.run(
            "check-ref-format",
            f"refs/heads/{destination[0]}"
            if not destination[0].startswith("refs/")
            else destination[0],
            check=False,
        ).returncode
    ):
        raise DVError("Invalid destination ref")
    return source


def pre_tool_use(payload: object, project: Path) -> dict:
    try:
        payload, repo = validate_payload(payload, project)
        tool = payload.get("tool_name", "Bash")
        if tool != "Bash":
            return {}
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
            raise DVError("Missing Bash command in hook payload")
        command = tool_input["command"]
        if not guarded(command):
            return {}
        configuration = config.load(repo.root)
        if command in configuration["hooks"]["allow_commands"]:
            return {
                "systemMessage": (
                    "Delivery command allowed by an exact repository-configured exempt"
                    "ion; no gate pass is claimed."
                )
            }
        push_source(command, Path(payload["cwd"]), repo.root)
        for value in configuration["hooks"]["items"]:
            item = Item(safe_path(repo.root, value, metadata=True))
            output = gate.run(item, require_approval=True)
            if not output["ok"]:
                failures = "; ".join(
                    r["detail"] for r in output["checks"] if r["outcome"] == "fail"
                )
                return deny(f"Delivery gate failed for {value}: {failures}")
        return {}
    except (Exception, KeyboardInterrupt, SystemExit) as error:
        return deny(
            f"Delivery denied: {error}. Inspect the local evidence. "
            "Deliver manually if syntax is unsupported."
        )


def stop(payload: object, project: Path) -> dict:
    try:
        payload, repo = validate_payload(payload, project)
        session = payload.get("session_id")
        if not isinstance(session, str) or not session.strip():
            raise DVError("Stop payload requires session_id for per-session retry state")
        if type(payload.get("stop_hook_active", False)) is not bool:
            raise DVError("stop_hook_active must be a boolean")
        problems = []
        maximum = 3
        try:
            configuration = config.load(repo.root)
            maximum = configuration["hooks"]["max_stop_blocks"]
            for value in configuration["hooks"]["items"]:
                try:
                    path = safe_path(repo.root, value, metadata=True)
                    item = Item(path, allow_incomplete=True)
                    if item.meta["status"] in ("preparing", "blocked"):
                        continue
                    output = gate.run(item, require_approval=item.meta["status"] == "approved")
                    problems.extend(
                        f"{value}: {r['check']}: {r['detail']}"
                        for r in output["checks"]
                        if r["outcome"] == "fail"
                    )
                except Exception as error:
                    problems.append(f"{value}: {error}")
        except Exception as error:
            problems.append(str(error))
        with repo.lock():
            state_path = repo.git_path(f"dv-stop-{digest(session.encode())}.json")
            state = {}
            if state_path.exists():
                try:
                    state = read_json(state_path)
                except DVError as error:
                    problems.append(str(error))
            count = state.get("blocks", 0)
            if type(count) is not int or count < 0:
                count = 0
                problems.append("Malformed Stop retry counter")
            count = (
                count + 1
                if payload.get("stop_hook_active") and problems
                else (1 if problems else 0)
            )
            write_json(state_path, {"blocks": count, "at": now(), "problems": problems})
        if not problems:
            return {}
        message = "Unresolved verification problems: " + " | ".join(problems)
        if count > maximum:
            return {
                "systemMessage": (
                    f"Stop retry limit reached ({maximum}); ending without approval. {message}"
                )
            }
        return {
            "decision": "block",
            "reason": message + " Fix the findings or mark the item blocked with an honest reason.",
        }
    except Exception as error:
        return {"decision": "block", "reason": f"Cannot validate Stop hook: {error}"}


def timeout_seconds(project: Path) -> int:
    configuration = config.load(project)
    commands = len(configuration["static_commands"]) + len(configuration["test_commands"])
    return len(configuration["hooks"]["items"]) * commands * configuration["timeout_seconds"] + 120


@contextmanager
def deadline(seconds: int) -> Iterator[None]:
    def interrupt(signum: int, frame: object) -> None:
        raise KeyboardInterrupt(f"Hook interrupted by signal {signum}")

    previous = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGTERM, signal.SIGALRM)}
    timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *timer)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def install(project: Path, dry_run: bool = False) -> dict:
    repo = Repo(project)
    path = safe_path(repo.root, ".claude/settings.json", metadata=True)
    settings = read_json(path) if path.exists() else {}
    updated = copy.deepcopy(settings)
    hooks = updated.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise DVError("settings.hooks must be an object")
    executable = shutil.which("dv")
    invoked = Path(sys.argv[0])
    if invoked.name == "dv" and invoked.is_file():
        executable = str(invoked.absolute())
    if not executable:
        raise DVError("Install the dv entry point before installing hooks", 3, "missing_executable")
    prefix = shlex.quote(str(Path(executable).absolute()))
    timeout = timeout_seconds(repo.root)
    installed_hooks = {}
    for event, adapter in (("Stop", "stop"), ("PreToolUse", "pre-tool-use")):
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            raise DVError(f"settings.hooks.{event} must be an array")
        command = f"{prefix} hooks run {adapter} --project {shlex.quote(str(repo.root))}"
        owned = {"type": "command", "command": command, "timeout": timeout}
        if event == "PreToolUse":
            owned["onFailure"] = "block"
        installed_hooks[event] = [
            {"hooks": [owned], **({"matcher": "Bash"} if event == "PreToolUse" else {})}
        ]
        present = False
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                raise DVError(f"Malformed {event} hook entry")
            for hook in entry["hooks"]:
                if not isinstance(hook, dict):
                    raise DVError("Malformed hook command entry")
                if (
                    hook.get("type") == "command"
                    and hook.get("command") == command
                    and (event == "Stop" or entry.get("matcher") == "Bash")
                ):
                    hook.clear()
                    hook.update(owned)
                    present = True
        if not present:
            entry = {"hooks": [owned]}
            if event == "PreToolUse":
                entry["matcher"] = "Bash"
            entries.append(entry)
    if not dry_run:
        with repo.lock():
            current = read_json(path) if path.exists() else {}
            if current != settings:
                raise DVError("Settings changed during installation; retry", 3, "concurrent_change")
            write_json(path, updated)
    return {
        "schema_version": 1,
        "command": "hooks install",
        "ok": True,
        "dry_run": dry_run,
        "path": str(path),
        "hooks": installed_hooks,
        "minimum_claude_code_version": "2.1.310",
    }


def run(event: str, project: Path, text: str) -> dict:
    try:
        with deadline(timeout_seconds(project) - 30):
            payload = decode_json(text)
            return stop(payload, project) if event == "stop" else pre_tool_use(payload, project)
    except (Exception, KeyboardInterrupt, SystemExit) as error:
        return (
            deny(f"Hook failed or interrupted: {error}")
            if event == "pre-tool-use"
            else {"decision": "block", "reason": f"Hook failed or interrupted: {error}"}
        )
