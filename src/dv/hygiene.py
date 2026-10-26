"""Diff hygiene heuristics, kept independent from test execution."""

from __future__ import annotations

import ast
import fnmatch
import io
import re
import tokenize
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from dv import lexical
from dv.common import result, row, write_json
from dv.item import Item

TEST_PATH = re.compile(
    r"(^|/)(__tests__|__mocks__|tests?|specs?|e2e|fixtures?|test[-_]utils)/|"
    r"(^|/)conftest\.py$|(^|/)(?!\.env(?:\.|$))[^/]+\.(test|spec|stories)\.[\w]+$|"
    r"_test\.(go|py)$|(^|/)test_[^/]*\.py$"
)
DOC_PATH = re.compile(r"\.(md|mdx|rst|txt)$|(^|/)(docs?|\.changeset)/")
SNAPSHOT = re.compile(r"(^|/)__snapshots__/|\.snap$")
GENERATED = re.compile(r"(^|/)(generated|__generated__)/|\.(generated|gen)\.[\w]+$")
LOCKFILE = re.compile(
    r"(^|/)(package-lock\.json|pnpm-lock\.yaml|yarn\.lock|bun\.lockb?|poetry\.lock|"
    r"Cargo\.lock|uv\.lock|Gemfile\.lock|go\.sum|composer\.lock|Pipfile\.lock)$"
)
JUNK = re.compile(
    r"(^|/)(\.DS_Store|\.idea|\.vscode|\.env(?:\.[^/]*)?|coverage|dist|build|"
    r"\.turbo|__pycache__|node_modules)(/|$)|\.(pem|key|pyc)$"
)
DIRECTIVE = re.compile(
    r"eslint-disable|@ts-(expect-error|ignore|nocheck)|prettier-ignore|biome-ignore|"
    r"\bnoqa\b|type:\s*ignore|istanbul ignore|c8 ignore|\bpragma\b"
)
SLASH = re.compile(
    r"\.(m?[jt]sx?|cjs|vue|svelte|go|rs|java|kt|swift|c|cc|cpp|h|hpp|cs|"
    r"scss|less|css|dart|php)$"
)
HASH = re.compile(r"\.(py|sh|bash|zsh|ya?ml|toml|rb|r|pl|dockerfile)$|(^|/)(Dockerfile|Makefile)$")
HARD_TELLS = [
    (
        re.compile(
            (
                "generated (with|by|using) .{0,30}(claude|chatgpt|gpt|copilot|curs"
                "or|codex|gemini|\\bai\\b)"
            ),
            re.I,
        ),
        "generator footer",
    ),
    (
        re.compile(
            r"co-authored-by:.*(claude|anthropic|openai|copilot|gpt|cursor|codex|gemini)", re.I
        ),
        "AI coauthor trailer",
    ),
    (re.compile("\U0001f916"), "robot symbol"),
    (
        re.compile(r"\bas an ai\b|\bI (?:am|'m) (?:an? )?(?:AI|language model)\b", re.I),
        "AI self-reference",
    ),
    (
        re.compile(r"NEEDS USER DECISION|TODO\(user\)|<placeholder>|\[insert\b", re.I),
        "unresolved placeholder",
    ),
]
FILLER = re.compile(
    r"\b(?:comprehensive(?:ly)?|robust(?:ly|ness)?|seamless(?:ly)?|leverag(?:e|es|ed|ing)|"
    r"utiliz(?:e|es|ed|ing)|delv(?:e|es|ing)|streamlin(?:e|es|ed|ing)|cutting-edge|"
    r"elevat(?:e|es|ing)|empower(?:s|ing)?|meticulous(?:ly)?|intricate|pivotal|crucial|"
    r"furthermore|moreover|additionally|in conclusion|"
    r"this (?:pr|pull request) (?:aims|introduces|adds)|"
    r"game[- ]changer|best practices?|enhanc(?:e|es|ed|ing|ement)|ensur(?:e|es|ing)|"
    r"key (?:changes|improvements)|now (?:properly|correctly)|gracefully|significantly)\b",
    re.I,
)
EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")
HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
JS_TEST = re.compile(r"\b(?:it|test)(?:\s*\.\s*(?:only|skip|each))*\s*\(")
RUST_TEST = re.compile(r"#\[\s*test\s*\]")
GO_TEST = re.compile(r"\bfunc Test\w*\s*\(")
ASSERTION = re.compile(
    r"\b(?:expect\s*\(|assert\w*\b|should\b|t\.(?:is|deepEqual|true|false|throws|Fatal|Error))"
)
WEAK = re.compile(
    r"(?:expect\([^\n]*?\)\s*\.(?:toBeDefined|toBeTruthy|toBeInTheDocument|"
    r"not\.toBeNull|not\.toBeUndefined)\s*\(\s*\))|"
    r"(?:\bassert\s+(?:True|\w+)\s*(?:;|$))|"
    r"(?:\b(?:self\.)?assert(?:True|IsNotNone)\([^\n]*?\))|(?:\bassert\.ok\([^\n]*?\))",
    re.M,
)


@dataclass
class DiffFile:
    path: str
    status: str
    old: str
    new: str
    added: list[tuple[int, int, str]] = field(default_factory=list)
    removed: list[tuple[int, int, str]] = field(default_factory=list)
    binary: bool = False


def is_generated(path: str, configuration: dict) -> bool:
    return bool(GENERATED.search(path)) or any(
        fnmatch.fnmatchcase(path, pattern) for pattern in configuration.get("generated_paths", [])
    )


def category(path: str, configuration: dict, explicit: list[str] = ()) -> str:
    if LOCKFILE.search(path):
        return "lockfile"
    if is_generated(path, configuration):
        return "generated"
    if SNAPSHOT.search(path):
        return "snapshot"
    if TEST_PATH.search(path) or path in explicit:
        return "test"
    return "doc" if DOC_PATH.search(path) else "src"


def comment_text(path: str, line: str) -> str:
    s = line.strip()
    if not s or s in ("*/", "*}") or s.startswith("#!"):
        return ""
    if HASH.search(path):
        try:
            for token in tokenize.generate_tokens(io.StringIO(line).readline):
                if token.type == tokenize.COMMENT:
                    return token.string
        except (tokenize.TokenError, IndentationError):
            pass
    if SLASH.search(path):
        if s.startswith(("//", "/*", "* ", "{/*")) or s == "*":
            return s
        _, comments = lexical.views(path, line)
        if comments.strip():
            return comments.strip()
    if Path(path).suffix in (".html", ".htm", ".xml", ".vue", ".svelte"):
        found = line.find("<!--")
        if found >= 0:
            return line[found:]
    return ""


def is_comment(path: str, line: str) -> bool:
    return bool(comment_text(path, line))


def prose_findings(label: str, text: str, allowed: list[str] = ()) -> tuple[list[str], list[str]]:
    failures, soft = [], []
    kept = []
    for line in text.splitlines():
        if line in allowed:
            continue
        kept.append(line)
        for pattern, reason in HARD_TELLS:
            if pattern.search(line):
                failures.append(f"{label}: {reason}: {line[:120]}")
        soft.extend(f"{label}: filler '{match.group()}'" for match in FILLER.finditer(line))
    text = "\n".join(kept)
    if text.count("—") > 2:
        soft.append(f"{label}: more than two em dashes")
    if EMOJI.search(text):
        soft.append(f"{label}: decorative symbols")
    return failures, soft


def rust_mask(text: str) -> str:
    pattern = re.compile(
        r"""//[^\n]*|/\*.*?\*/|r(\#*)".*?"\1|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])' """, re.S | re.X
    )
    return pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)


def rust_test_start(text: str) -> tuple[int | None, bool]:
    """Only an uninterrupted suffix of top-level test items is classified as tests."""
    masked = rust_mask(text)
    pattern = re.compile(
        r"#\[\s*(?:cfg\(\s*test\s*\)|test)\s*\]\s*"
        r"(?:#\[[^\]]*\]\s*)*(?:(?:pub(?:\([^)]*\))?|async|unsafe)\s+)*"
        r"(?:mod\s+\w+\s*|fn\s+\w+[^;{]*)\{"
    )
    spans = []
    for match in pattern.finditer(masked):
        if masked[: match.start()].count("{") != masked[: match.start()].count("}"):
            continue
        depth, end = 1, match.end()
        while end < len(masked) and depth:
            depth += (masked[end] == "{") - (masked[end] == "}")
            end += 1
        if not depth:
            spans.append((match.start(), end))
    suffix = len(masked)
    accepted = []
    for start, end in reversed(spans):
        if not masked[end:suffix].strip():
            suffix = start
            accepted.append(start)
        else:
            break
    start_line = text.count("\n", 0, suffix) + 1 if accepted else None
    return start_line, len(accepted) != len(spans)


def diff_files(item: Item) -> list[DiffFile]:
    files = []
    for status, path in item.repo.changes(item.base, item.head):
        old_bytes, new_bytes = item.repo.blob(item.base, path), item.repo.blob(item.head, path)
        entry = DiffFile(
            path, status, old_bytes.decode("utf-8", "replace"), new_bytes.decode("utf-8", "replace")
        )
        patch = item.repo.run(
            "diff",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            "--unified=0",
            "--no-color",
            item.base,
            item.head,
            "--",
            path,
        )
        old_no = new_no = hunk = 0
        for line in patch.stdout.decode("utf-8", "replace").splitlines():
            match = HUNK.match(line)
            if match:
                old_no, new_no = map(int, match.groups())
                hunk += 1
            elif hunk and line.startswith("+"):
                entry.added.append((new_no, hunk, line[1:]))
                new_no += 1
            elif hunk and line.startswith("-"):
                entry.removed.append((old_no, hunk, line[1:]))
                old_no += 1
            elif hunk and line.startswith(" "):
                old_no += 1
                new_no += 1
            elif line.startswith(("Binary files", "GIT binary patch")):
                entry.binary = True
        files.append(entry)
    return files


def behavioral_predicate(node: ast.AST) -> bool:
    if isinstance(node, ast.Compare):
        return True
    if isinstance(node, ast.BoolOp):
        return any(behavioral_predicate(value) for value in node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return behavioral_predicate(node.operand)
    if isinstance(node, ast.Call):
        name = (
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else (node.func.id if isinstance(node.func, ast.Name) else "")
        )
        return bool(name and name not in ("bool", "exists", "hasattr", "is_file", "is_dir"))
    return False


def python_weak_tests(path: str, text: str, added_lines: set[int]) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [f"{path}: test syntax cannot be inspected; fix the syntax before verification"]
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_") or node.lineno not in added_lines:
            continue
        assertions = []
        for child in ast.walk(node):
            if isinstance(child, ast.Assert):
                assertions.append(behavioral_predicate(child.test))
            elif isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                name = child.func.attr
                if name.startswith("assert") or name in ("raises", "warns"):
                    if name in ("assertTrue", "assertFalse"):
                        assertions.append(bool(child.args and behavioral_predicate(child.args[0])))
                    else:
                        assertions.append(name != "assertIsNotNone")
        if not assertions:
            findings.append(f"{path}:{node.lineno}: new test without an assertion")
        elif not any(assertions):
            findings.append(f"{path}:{node.lineno}: test only asserts existence/truthiness")
    return findings


def weak_tests(path: str, text: str, added_lines: set[int]) -> list[str]:
    if SNAPSHOT.search(path):
        return []
    if path.endswith(".py"):
        return python_weak_tests(path, text, added_lines)
    is_rust, is_go = path.endswith(".rs"), path.endswith(".go")
    assertion = go_assertions(text) if is_go else ASSERTION
    text = rust_mask(text) if is_rust else lexical.views(path, text)[0]
    pattern = RUST_TEST if is_rust else GO_TEST if is_go else JS_TEST
    matches = list(pattern.finditer(text))
    findings = []
    for index, match in enumerate(matches):
        line = text.count("\n", 0, match.start()) + 1
        if line not in added_lines:
            continue
        if is_rust or is_go:
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            block = text[match.end() : end]
            opening = block.find("{")
            if opening >= 0:
                block = block[: lexical.balanced_end(block, opening)]
            if is_rust and re.search(r"#\[\s*should_panic\b", block):
                continue
        else:
            block = lexical.javascript_body(text, match.end() - 1)
        if not assertion.search(block):
            findings.append(f"{path}:{line}: new test without an assertion")
        elif not assertion.search(WEAK.sub("", block)):
            findings.append(f"{path}:{line}: test only asserts existence/truthiness")
    return findings


def go_assertions(text: str) -> re.Pattern:
    aliases = {"require", "assert"}
    for match in re.finditer(
        r'(?:\b(\w+)\s+)?"github\.com/stretchr/testify/(require|assert)"', text
    ):
        aliases.add(match.group(1) or match.group(2))
    names = "|".join(re.escape(name) for name in sorted(aliases))
    code = lexical.views("test.go", text)[0]
    for match in re.finditer(rf"\b(\w+)\s*:=\s*(?:{names})\.New\s*\(", code):
        aliases.add(match.group(1))
    names = "|".join(re.escape(name) for name in sorted(aliases))
    methods = (
        "Equal|EqualValues|Exactly|NotEqual|NoError|Error|ErrorIs|ErrorAs|EqualError|"
        "Contains|NotContains|Len|Empty|NotEmpty|Greater|GreaterOrEqual|Less|LessOrEqual|"
        "True|False|Nil|NotNil|Panics|PanicsWithValue|PanicsWithError|NotPanics|"
        "WithinDuration|InDelta|InEpsilon|Eventually|Never|ElementsMatch|Subset|JSONEq|Regexp|"
        "Zero|NotZero|Same|NotSame|IsType|Implements|NotSubset|NotRegexp|Negative|Positive"
    )
    return re.compile(ASSERTION.pattern + rf"|\b(?:{names})\.(?:{methods})f?\s*\(")


def inspect(item: Item) -> dict:
    checks = []
    stats = {
        key: 0
        for key in (
            "files",
            "added",
            "removed",
            "src_lines",
            "test_lines",
            "doc_lines",
            "generated_lines",
            "lockfile_lines",
            "snapshot_lines",
            "inline_rust_test_lines",
            "src_comments",
            "test_comments",
            "binary_files",
        )
    }
    files = diff_files(item)
    touched_test = False
    entries = []
    exceptions = item.meta["exceptions"]
    allowed = item.config["allowed_prose_lines"]
    for key, reason in exceptions.items():
        checks.append(row("exception", "warn", f"{key}: {reason}"))
    if allowed:
        checks.append(row("prose exemptions", "warn", f"Exact-line exemptions: {allowed!r}"))
    allowed_code = item.config["allowed_code_lines"]
    if allowed_code:
        checks.append(row("code exemptions", "warn", f"Exact-line exemptions: {allowed_code!r}"))
    if not files:
        checks.append(row("change", "fail", "No changes against merge base"))
    for file in files:
        path = file.path
        kind = category(path, item.config, item.meta["test_files"])
        stats["files"] += 1
        stats["added"] += len(file.added)
        stats["removed"] += len(file.removed)
        stats["binary_files"] += file.binary
        if file.status != "D" and JUNK.search(path):
            checks.append(
                row("junk path", "fail", path, "Remove editor/build/secret-like artifacts.")
            )
        if kind == "lockfile" and not exceptions.get("lockfile_reason"):
            checks.append(
                row(
                    "lockfile",
                    "fail",
                    path,
                    "Remove it or explain the dependency change in lockfile_reason.",
                )
            )
        head_start = base_start = None
        if path.endswith(".rs") and kind == "src":
            head_start, ambiguous = rust_test_start(file.new)
            base_start, old_ambiguous = rust_test_start(file.old)
            if ambiguous or old_ambiguous:
                checks.append(
                    row(
                        "Rust layout", "warn", f"{path}: non-trailing test blocks counted as source"
                    )
                )
        inline = sum(1 for at, _, _ in file.added if head_start and at >= head_start)
        inline += sum(1 for at, _, _ in file.removed if base_start and at >= base_start)
        changed = len(file.added) + len(file.removed)
        stats[f"{kind}_lines"] += changed - inline
        stats["test_lines"] += inline
        stats["inline_rust_test_lines"] += inline
        touched_test |= kind in ("test", "snapshot") or inline > 0
        entries.append(
            {
                "path": path,
                "status": file.status,
                "category": kind,
                "binary": file.binary,
                "added": len(file.added),
                "removed": len(file.removed),
                "inline_test_lines": inline,
            }
        )
        if kind in ("generated", "snapshot", "lockfile"):
            continue
        replaced = Counter(h for _, h, text in file.removed if is_comment(path, text))
        code_view, comment_view = lexical.views(path, file.new)
        code_lines, comment_lines = code_view.splitlines(), comment_view.splitlines()
        for at, hunk, line in file.added:
            in_test = kind == "test" or bool(head_start and at >= head_start)
            comment = (
                comment_lines[at - 1].strip()
                if SLASH.search(path) or path.endswith(".py")
                else comment_text(path, line)
            )
            if line not in allowed_code and re.search(r"\b(TODO|FIXME|XXX|HACK)\b", comment):
                checks.append(row("unfinished work", "fail", f"{path}:{at}: {comment[:120]}"))
            if comment:
                if DIRECTIVE.search(comment):
                    checks.append(row("directive", "warn", f"{path}:{at}: {comment}"))
                elif in_test:
                    stats["test_comments"] += 1
                elif replaced[hunk] > 0:
                    replaced[hunk] -= 1
                    checks.append(
                        row("comment edit", "warn", f"{path}:{at}: edited an existing comment")
                    )
                elif kind != "doc":
                    stats["src_comments"] += 1
                    if not exceptions.get("comment_justification"):
                        checks.append(
                            row(
                                "source comment",
                                "fail",
                                f"{path}:{at}: new comment in source",
                                "Remove it or explain exceptions.comment_justification.",
                            )
                        )
                failures, _ = (
                    prose_findings(f"{path}:{at}", comment) if line not in allowed else ([], [])
                )
                checks.extend(row("authorship", "fail", failure) for failure in failures)
            if kind == "doc":
                continue
            code = code_lines[at - 1]
            if (
                not in_test
                and line not in allowed_code
                and re.search(
                    r"\bconsole\s*\.\s*(log|debug)\s*\(|(?<![\w$.])debugger\s*(?=;|$)", code
                )
            ):
                checks.append(row("debug output", "fail", f"{path}:{at}: debug statement"))
            if in_test and re.search(
                r"\b(it|test|describe)\s*\.\s*only\s*(?:\(|\.)|\b(fit|fdescribe)\s*\(", code
            ):
                checks.append(
                    row("focused test", "fail", f"{path}:{at}: focused test left enabled")
                )
        if kind == "test" or head_start:
            added = {at for at, _, _ in file.added if kind == "test" or at >= head_start}
            reason = exceptions.get("weak_test_justification")
            checks.extend(
                row(
                    "weak test",
                    "warn" if reason else "fail",
                    f"{problem}; inspection waived: {reason}" if reason else problem,
                    "Add a behavioral assertion or explain exceptions.weak_test_justification "
                    "for an unrecognized assertion framework; red/green is still required.",
                )
                for problem in weak_tests(path, file.new, added)
            )
    if not touched_test and not exceptions.get("no_test_reason"):
        checks.append(
            row(
                "regression test",
                "fail",
                "No test file changed",
                "Add a regression test or explain exceptions.no_test_reason.",
            )
        )
    limits = item.config["limits"]
    cap = limits["max_source_lines_large" if item.meta["kind"] == "large" else "max_source_lines"]
    if stats["src_lines"] > cap:
        checks.append(
            row(
                "source size",
                "fail",
                f"{stats['src_lines']} changed source lines (cap {cap})",
                "Split the change.",
            )
        )
    elif stats["src_lines"] > limits["warn_source_lines"]:
        checks.append(row("source size", "warn", f"{stats['src_lines']} changed source lines"))
    if stats["test_lines"] > limits["warn_test_lines"]:
        checks.append(row("test size", "warn", f"{stats['test_lines']} changed test lines"))
    if stats["test_comments"] > 3:
        checks.append(
            row("test comments", "warn", f"{stats['test_comments']} new test comment lines")
        )
    whitespace = item.repo.run("diff", "--check", item.base, item.head, check=False)
    checks.append(
        row(
            "whitespace",
            "fail" if whitespace.returncode else "pass",
            whitespace.stdout.decode("utf-8", "replace").strip() or f"exit={whitespace.returncode}",
        )
    )
    commits = (
        item.repo.run("log", "--format=%B%x00", f"{item.base}..{item.head}")
        .stdout.decode("utf-8", "replace")
        .split("\0")
    )
    commits = [message.strip() for message in commits if message.strip()]
    if len(commits) > 3:
        checks.append(row("commit noise", "warn", f"{len(commits)} commits in inspected range"))
    prose = [("commit message", message) for message in commits]
    description = item.meta.get("description_file")
    if description and (item.root / description).is_file():
        prose.append(("description", (item.root / description).read_text(encoding="utf-8")))
    soft = []
    for label, text in prose:
        failures, hits = prose_findings(label, text, allowed)
        checks.extend(row("authorship", "fail", failure) for failure in failures)
        soft.extend(hits)
    if soft:
        checks.append(
            row(
                "prose filler",
                "fail" if len(soft) > limits["max_soft_tell_hits"] else "warn",
                "; ".join(soft),
            )
        )
    return result("hygiene", item.scope, checks, stats=stats, files=entries)


def run(item: Item) -> dict:
    with item.repo.lock():
        item.ensure_current()
        output = inspect(item)
        item.ensure_current()
        write_json(item.file("hygiene.json"), output)
        return output
