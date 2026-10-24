"""Conservative, offset-preserving code and comment views for hygiene checks."""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

JAVASCRIPT = re.compile(r"\.(m?[jt]sx?|cjs|vue|svelte)$")


def views(path: str, text: str) -> tuple[str, str]:
    code = list(text)
    comments = ["\n" if c == "\n" else " " for c in text]

    def mask(start: int, end: int, comment: bool = False) -> None:
        for at in range(start, end):
            if comment:
                comments[at] = text[at]
            if text[at] != "\n":
                code[at] = " "

    if Path(path).suffix == ".py":
        offsets = [0]
        for line in text.splitlines(keepends=True):
            offsets.append(offsets[-1] + len(line))
        try:
            for token in tokenize.generate_tokens(io.StringIO(text).readline):
                if token.type in (tokenize.STRING, tokenize.COMMENT):
                    start = offsets[token.start[0] - 1] + token.start[1]
                    end = offsets[token.end[0] - 1] + token.end[1]
                    mask(start, end, token.type == tokenize.COMMENT)
        except (tokenize.TokenError, IndentationError, SyntaxError):
            pass
        return "".join(code), "".join(comments)

    javascript = bool(JAVASCRIPT.search(path))
    at = 0
    while at < len(text):
        start = at
        if text.startswith("//", at):
            end = text.find("\n", at)
            at = len(text) if end < 0 else end
            mask(start, at, True)
        elif text.startswith("/*", at):
            end = text.find("*/", at + 2)
            at = len(text) if end < 0 else end + 2
            mask(start, at, True)
        elif text[at] in "\"'`":
            quote = text[at]
            at += 1
            while at < len(text):
                if text[at] == "\\":
                    at += 2
                elif text[at] == quote:
                    at += 1
                    break
                else:
                    at += 1
            at = min(at, len(text))
            mask(start, at)
        elif javascript and text[at] == "/" and regex_context("".join(code[:at])):
            at += 1
            in_class = False
            while at < len(text) and text[at] != "\n":
                char = text[at]
                if char == "\\":
                    at += 2
                    continue
                if char == "[":
                    in_class = True
                elif char == "]":
                    in_class = False
                elif char == "/" and not in_class:
                    at += 1
                    while at < len(text) and text[at].isalpha():
                        at += 1
                    break
                at += 1
            at = min(at, len(text))
            mask(start, at)
        else:
            at += 1
    return "".join(code), "".join(comments)


def regex_context(prefix: str) -> bool:
    prefix = prefix.rstrip()
    return (
        not prefix
        or prefix[-1] in "=([{,:;!?&|"
        or bool(re.search(r"\b(return|throw|case|yield|typeof|void|delete)\s*$", prefix))
    )


def balanced_end(text: str, start: int) -> int:
    pairs = {"(": ")", "{": "}", "[": "]"}
    stack = [pairs[text[start]]]
    for at in range(start + 1, len(text)):
        char = text[at]
        if char in pairs:
            stack.append(pairs[char])
        elif stack and char == stack[-1]:
            stack.pop()
            if not stack:
                return at + 1
    return len(text)


def javascript_body(text: str, start: int) -> str:
    opening = text.find("(", start)
    if opening < 0:
        return ""
    end = balanced_end(text, opening)
    next_start = end
    while next_start < len(text) and text[next_start].isspace():
        next_start += 1
    if next_start < len(text) and text[next_start] == "(":
        opening, end = next_start, balanced_end(text, next_start)
    at = opening + 1
    while at < end:
        if text.startswith("=>", at):
            body = at + 2
            while body < end and text[body].isspace():
                body += 1
            stop = balanced_end(text, body) if body < end and text[body] == "{" else end
            return text[body:stop]
        function = re.match(r"function\b[^({]*\(", text[at:end])
        if function:
            parameters = at + function.end() - 1
            body = balanced_end(text, parameters)
            while body < end and text[body].isspace():
                body += 1
            if body < end and text[body] == "{":
                return text[body : balanced_end(text, body)]
        if text[at] in "({[":
            at = balanced_end(text, at)
        else:
            at += 1
    return ""
